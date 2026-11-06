"""Local investment reads and independent, asynchronous market sampling.

CRUD, overview, positions, curve and rebuild_history are synchronous/cache-only.
start, stop and refresh are asynchronous. refresh fetches history at most once
per Beijing day, independent of the video queue. Provider methods may be sync or
async; the default MarketProvider uses sync sessions and async market requests.
"""

import asyncio
import inspect
from datetime import datetime, time, timedelta
from decimal import Decimal, localcontext

from .ledger import (
    BEIJING,
    UTC,
    ZERO,
    Ledger,
    LedgerError,
    cumulative_return_percentage,
    decimal_string,
    normalize_record,
    parse_time,
    timestamp,
    value_state,
)
from .market import portfolio_trading_day, quote_delay_seconds, quote_is_stale, trading_windows
from .repository import InvestmentRepository
from .returns import seeded_compounded_points, time_weighted_points


class InvestmentService:
    """Inject a MarketProvider-compatible object to avoid network in callers/tests."""

    def __init__(self, database, provider=None):
        self.database = database
        self.repository = InvestmentRepository(database)
        if provider is None:
            from .market import MarketProvider

            provider = MarketProvider()
        self.provider = provider
        self.worker = None
        self._manual_refresh = None
        self._refresh_lock = asyncio.Lock()
        self._stopping = False
        self._sessions = []
        self._refresh_warnings = []

    def _ledger(self):
        return Ledger(lambda currency, at: self.repository.latest_fx(currency, at, historical=True))

    @staticmethod
    def _validation_cutoff(records):
        # Include future records too so edits cannot make a later sell impossible.
        return max((parse_time(record["occurred_at"]) for record in records), default=parse_time())

    def validate_records(self, records):
        """Validate an entire proposed ledger, including later/future transactions."""
        return self._ledger().replay(records, self._validation_cutoff(records))

    def _prepare(self, values):
        value = normalize_record(values)
        if value["currency"] != value["settlement_currency"]:
            value["estimated"] = value["settlement_amount"] is None or (
                Decimal(value["fee"]) != 0 and value["settlement_fee"] is None
            )
        return value

    def create_record(self, values):
        """Normalize and replay before atomically adding a record/revision."""
        record = self._prepare(values)
        with self.database.lock:
            records = self.repository.list_records(include_voided=True)
            if any(existing["id"] == record["id"] for existing in records):
                raise LedgerError("投资记录编号已存在")
            self.validate_records(records + [record])
            result = self.repository.add_record(record)
            self.rebuild_history()
        return result

    def update_record(self, record_id, values):
        """Validate an edit against the whole ledger, preserving stable tie order."""
        with self.database.lock:
            previous = self.repository.get_record(record_id)
            if previous is None:
                raise LedgerError("投资记录不存在")
            record = self._prepare({**previous, **values, "id": record_id})
            records = [
                record if item["id"] == record_id else item
                for item in self.repository.list_records(include_voided=True)
            ]
            self.validate_records(records)
            result = self.repository.update_record(record_id, record)
            self.rebuild_history()
        return result

    def void_record(self, record_id):
        """Soft-delete after checking that remaining sells still have inventory."""
        with self.database.lock:
            previous = self.repository.get_record(record_id)
            if previous is None:
                raise LedgerError("投资记录不存在")
            records = [
                {**item, "voided": True} if item["id"] == record_id else item
                for item in self.repository.list_records(include_voided=True)
            ]
            self.validate_records(records)
            result = self.repository.void_record(record_id)
            self.rebuild_history()
        return result

    def list_records(self, at=None, include_voided=True, **filters):
        """Return stable ascending record dictionaries, including audit-visible voids."""
        return self.repository.list_records(at, include_voided, **filters)

    def _value(self, at, *, daily=False, records=None):
        state = self._ledger().replay(
            self.repository.list_records(at=at) if records is None else records, at
        )

        def prices(market, symbol, cutoff):
            return self.repository.latest_price(market, symbol, cutoff, daily=daily)

        result = value_state(state, at, prices, self.repository.latest_fx)
        result["market_value"] = result["holdings_value"]
        active = {
            row["market"]
            for row in self._sessions
            if row.get("state") in {"open", "active", "trading"}
        }
        if "A" in active:
            active.add("FUND")
        for item in result["items"]:
            item["quote_delay_seconds"] = quote_delay_seconds(item)
            item["quote_delayed"] = item["quote_delay_seconds"] > 0
            item["stale"] = bool(
                item["market"] in active
                and item["price_at"]
                and quote_is_stale(item, parse_time(at))
            )
            if item["stale"]:
                result["warnings"].append(
                    {
                        "code": "stale_price",
                        "market": item["market"],
                        "symbol": item["symbol"],
                        "message": f"{item['symbol']} 的行情已超过正常报价延迟与五分钟更新容差",
                    }
                )
        for balance in result["cash_balances"]:
            balance["fx_stale"] = bool(
                active
                and balance["fx_at"]
                and parse_time(at) - parse_time(balance["fx_at"]) > timedelta(minutes=5)
            )
            if balance["fx_stale"]:
                result["warnings"].append(
                    {
                        "code": "stale_fx",
                        "currency": balance["currency"],
                        "message": f"{balance['currency']} 的汇率已超过五分钟未更新",
                    }
                )
        for item in result["items"]:
            item["fx_stale"] = bool(
                item["market"] in active
                and item["fx_at"]
                and parse_time(at) - parse_time(item["fx_at"]) > timedelta(minutes=5)
            )
            if item["fx_stale"]:
                result["warnings"].append(
                    {
                        "code": "stale_fx",
                        "currency": item["currency"],
                        "message": f"{item['currency']} 的汇率已超过五分钟未更新",
                    }
                )
        if any(
            balance["amount"] is not None and Decimal(balance["amount"]) < 0
            for balance in result["cash_balances"]
        ):
            result["warnings"].append(
                {
                    "code": "negative_cash",
                    "message": "账本存在负现金余额，请检查期初现金及出入金记录",
                }
            )
        return state, result

    def _daily_breakdown(self, baseline, current, start, end, total_pnl):
        """Attribute position and trading cash changes; FX stays in the remainder."""
        records = self.repository.list_records(at=end)
        opening = [row for row in records if parse_time(row["occurred_at"]) <= start]
        before_state = self._ledger().replay(opening, start)
        breakdown, attributed = [], ZERO
        for market, markets in (("A", {"A", "FUND"}), ("HK", {"HK"}), ("US", {"US"})):
            selected = opening + [
                row
                for row in records
                if start < parse_time(row["occurred_at"]) <= end
                and row["market"] in markets
                and row["action"] in {"buy", "sell", "split", "dividend", "fee"}
            ]
            after_state = self._ledger().replay(selected, end)
            changes = {}
            complete = True
            for value, sign in ((baseline, -1), (current, 1)):
                for item in value["items"]:
                    if item["market"] in markets:
                        native = item["market_value_native"]
                        if native is None:
                            complete = False
                        else:
                            currency = item["currency"]
                            changes[currency] = changes.get(currency, ZERO) + sign * Decimal(native)
            for currency in before_state.cash.keys() | after_state.cash.keys():
                before = before_state.cash.get(currency, ZERO)
                after = after_state.cash.get(currency, ZERO)
                if before is None or after is None:
                    complete = False
                else:
                    changes[currency] = changes.get(currency, ZERO) + after - before
            pnl = ZERO
            for currency, change in changes.items():
                if change == ZERO:
                    continue
                fx = self.repository.latest_fx(currency, end)
                if fx is None:
                    complete = False
                else:
                    pnl += change * Decimal(fx["rate"])
            pnl = pnl if complete else None
            breakdown.append({"market": market, "pnl": decimal_string(pnl)})
            attributed = None if attributed is None or pnl is None else attributed + pnl
        remainder = None if total_pnl is None or attributed is None else total_pnl - attributed
        breakdown.append({"market": "OTHER", "pnl": decimal_string(remainder)})
        return breakdown

    def overview(self, at=None, *, _daily=False):
        """CNY totals and complete local-market trading-day performance."""
        at = parse_time(at)
        with self.database.lock, localcontext() as context:
            context.prec = 100
            state, result = self._value(at, daily=_daily)
            trading = portfolio_trading_day(at)
            baseline_at = trading["baseline_at"]
            daily_at = min(at, trading["end_at"])
            _, baseline = self._value(baseline_at, daily=True)
            daily_state, daily_value = (state, result) if daily_at == at else self._value(daily_at)
            external = ZERO
            for flow in daily_state.external_flows:
                if baseline_at < parse_time(flow["occurred_at"]) <= daily_at:
                    if flow["amount"] is None:
                        external = None
                        break
                    external += flow["amount"]
            if daily_value["total"] is None or baseline["total"] is None or external is None:
                daily = None
            else:
                daily = Decimal(daily_value["total"]) - Decimal(baseline["total"]) - external
            result["daily_pnl"] = decimal_string(daily)
            result["daily_return"] = cumulative_return_percentage(daily, baseline["total"])
            result["daily_baseline_at"] = timestamp(baseline_at)
            result["daily_baseline_assets"] = baseline["total"]
            result["daily_date"] = trading["date"]
            result["daily_previous_date"] = trading["previous_date"]
            result["daily_valuation_at"] = timestamp(daily_at)
            result["daily_basis"] = "market_trading_day"
            result["daily_breakdown"] = self._daily_breakdown(
                baseline, daily_value, baseline_at, daily_at, daily
            )
            result["daily_net_external"] = decimal_string(external)
            result["daily_complete"] = daily is not None
            if daily is None:
                result["warnings"].append(
                    {
                        "code": "missing_daily_baseline",
                        "message": "上一交易日日终估值或期间外部资金流水不完整，无法计算交易日收益",
                    }
                )
            result["sessions"] = list(self._sessions)
            result["refresh"] = self.repository.get_setting("refresh", {})
            source_times = [item["price_at"] for item in result["items"] if item["price_at"]]
            source_times += [item["fx_at"] for item in result["items"] if item["fx_at"]]
            source_times += [item["fx_at"] for item in result["cash_balances"] if item["fx_at"]]
            # A sampler heartbeat is not evidence of a new market quote.
            result["updated_at"] = max(source_times, default=None)
            result["migration"] = self.repository.get_setting("migration")
            inception = (result["migration"] or {}).get("inception") or self.repository.get_setting(
                "inception"
            )
            result["history_note"] = (
                f"{inception} 之前已卖出资产的收益缺少原始流水，未计入历史收益；期初持仓保留成本与浮动收益"
                if inception
                else "期初持仓保留成本与浮动收益；期初之前已卖出资产的历史收益缺少原始流水，未计入历史收益"
            )
            result["warnings"].extend(self._refresh_warnings)
            return result

    def positions(self, at=None):
        """Frontend wrapper with items, warnings and completeness."""
        with self.database.lock:
            _, value = self._value(parse_time(at))
        return {
            "items": value["items"],
            "occurred_at": value["occurred_at"],
            "warnings": value["warnings"] + self._refresh_warnings,
            "completeness": value["completeness"],
            "complete": value["complete"],
        }

    def _rebuild_existing_samples(self):
        """Revalue actual sampled times without inventing unsampled intervals."""
        for row in self.repository.snapshots():
            snapshot = self.overview(row["occurred_at"], _daily=row["kind"] == "daily")
            snapshot["kind"] = row["kind"]
            if row["kind"] == "daily":
                snapshot["date"] = (
                    parse_time(row["occurred_at"]).astimezone(BEIJING).date() - timedelta(days=1)
                ).isoformat()
            self.repository.save_snapshot(row["occurred_at"], row["kind"], snapshot)

    def rebuild_history(self):
        """Synchronously rebuild daily history and existing minute samples, no I/O.

        Each daily observation occurs at next-day 08:00 Asia/Shanghai. Only
        source data known by that observation are used, and future samples are
        omitted. Existing minute timestamp gaps are preserved across ledger edits.
        """
        with self.database.lock:
            records = self.repository.list_records()
            self.validate_records(records)
            # Request additional source history only when its coverage changes.
            # Amount/fee/note edits reuse the previously fetched data.
            coverage = {
                "instruments": self._instruments(records),
                "start": min(
                    (
                        parse_time(record["occurred_at"]).astimezone(BEIJING).date().isoformat()
                        for record in records
                    ),
                    default=None,
                ),
                "currencies": sorted(
                    {
                        record[field]
                        for record in records
                        for field in ("currency", "settlement_currency")
                        if record[field] != "CNY"
                    }
                ),
            }
            if self.repository.get_setting("history_coverage") != coverage:
                self.repository.set_setting("history_day", None)
                self.repository.set_setting("history_coverage", coverage)
            self._rebuild_existing_samples()
            if not records:
                return {"daily": 0, "minute": len(self.repository.snapshots(kind="minute"))}
            current = parse_time()
            day = min(
                parse_time(record["occurred_at"]).astimezone(BEIJING).date() for record in records
            )
            count = 0
            while True:
                observation = datetime.combine(
                    day + timedelta(days=1), time(8), BEIJING
                ).astimezone(UTC)
                if observation > current:
                    break
                snapshot = self.overview(observation, _daily=True)
                snapshot.update({"date": day.isoformat(), "kind": "daily"})
                self.repository.save_snapshot(observation, "daily", snapshot)
                day += timedelta(days=1)
                count += 1
            self.repository.prune_minutes(current - timedelta(days=90))
            return {"daily": count, "minute": len(self.repository.snapshots(kind="minute"))}

    def _return_boundaries(self, records, baseline_at, end):
        """Revalue just before each external flow using only previous-known data."""
        groups = {}
        for record in records:
            at = parse_time(record["occurred_at"])
            if baseline_at < at <= end and record["action"] in {
                "deposit",
                "withdraw",
                "opening_cash",
                "opening_capital",
                "opening_position",
            }:
                groups.setdefault(at, []).append(record)
        boundaries = []
        for at, group in sorted(groups.items()):
            # Prices/FX published at the flow time belong to its valuation,
            # while transactions at that time have not changed the pre-flow
            # holdings yet. This keeps a same-time FX update out of capital.
            _, before = self._value(
                at, records=[record for record in records if parse_time(record["occurred_at"]) < at]
            )
            flow, estimated, warning = ZERO, bool(before["estimated"]), None
            for record in group:
                if record["action"].startswith("opening_"):
                    warning = "跟踪起点之后存在期初调整，请核对起点及期初记录"
                    break
                state = self._ledger().replay([record], at)
                external = state.external_flows[0]["amount"]
                if external is None:
                    flow = None
                    break
                with localcontext() as context:
                    context.prec = 100
                    flow += external
                estimated |= state.estimated or bool(record["date_only"])
            # Reconstructing a flow boundary with carried prices/FX is an
            # estimate. Never use a later quote to make it look exact.
            sources = [item.get("price_at") for item in before["items"]]
            sources += [
                item.get("fx_at")
                for item in before["cash_balances"]
                if item["currency"] != "CNY" and item["amount"] not in {None, "0"}
            ]
            sources += [item.get("fx_at") for item in before["items"] if item["currency"] != "CNY"]
            estimated |= any(
                source is None or at - parse_time(source) > timedelta(seconds=90)
                for source in sources
            )
            boundaries.append(
                {
                    "occurred_at": timestamp(at),
                    "before": before["total"],
                    "flow": decimal_string(flow),
                    "estimated": estimated,
                    "warning": warning,
                }
            )
        return boundaries

    def _time_weighted_curve(self, points, current):
        """Use the first daily observation as baseline, never the selected window."""
        daily = self.repository.snapshots(end=current, kind="daily")
        candidates = daily
        baseline = None
        if candidates:
            row = candidates[0]
            baseline = {**row["payload"], "occurred_at": row["occurred_at"]}
            baseline["total"] = baseline.get("total_assets", baseline.get("total"))
        baseline_at = parse_time(baseline["occurred_at"]) if baseline else current
        boundaries = self._return_boundaries(self.repository.list_records(), baseline_at, current)
        items = time_weighted_points(points, baseline, boundaries)
        initial_return = (
            cumulative_return_percentage(
                baseline.get("cumulative_pnl"), baseline.get("net_invested")
            )
            if baseline
            else None
        )
        items = seeded_compounded_points(items, initial_return)
        day = baseline.get("date") if baseline else None
        if baseline and not day:
            day = baseline_at.astimezone(BEIJING).date().isoformat()
        return items, {
            "return_method": "initial_capital_linked_twr",
            "return_baseline_at": baseline["occurred_at"] if baseline else None,
            "return_baseline_date": day,
            "return_baseline_rate": initial_return,
            "return_note": (
                f"自 {day} 的期初收益率 {Decimal(initial_return):+.2f}% 起算"
                "（当日累计盈亏 ÷ 累计净投入）；包含期初浮盈浮亏，之后复利衔接，剔除出入金影响，非年化。"
                if day and initial_return is not None
                else "等待完整的起点累计盈亏、累计净投入及资产市值建立收益率起点。"
            ),
        }

    def curve(self, period="1m"):
        """Return daily/minute points and segments, breaking missing sample gaps.

        Periods: 1d/today, 1w/7d, 1m/30d, 3m/90d, 6m, 1y, all/max.
        Missing valuations stay null. Minute gaps >90 seconds and daily gaps
        >36 hours start new segments so charts cannot bridge an outage.
        """
        current = parse_time()
        aliases = {
            "day": "1d",
            "today": "1d",
            "week": "1w",
            "7d": "1w",
            "month": "1m",
            "quarter": "3m",
            "30d": "1m",
            "90d": "3m",
            "year": "1y",
            "max": "all",
        }
        period = aliases.get(period, period)
        durations = {"1d": 1, "1w": 7, "1m": 30, "3m": 90, "6m": 180, "1y": 365}
        if period not in {*durations, "all"}:
            raise LedgerError("不支持的收益曲线周期")
        kind = "minute" if period == "1d" else "daily"
        start = None if period == "all" else current - timedelta(days=durations[period])
        if period == "1d":
            trading = portfolio_trading_day(current)
            start = trading["baseline_at"]
            current = min(current, trading["end_at"])
        baseline_point = None
        windows = []
        with self.database.lock:
            rows = self.repository.snapshots(start=start, end=current, kind=kind)
            if kind == "minute":
                _, daily_baseline = self._value(start, daily=True)
                daily_state = self._ledger().replay(
                    self.repository.list_records(at=current), current
                )
                windows = trading_windows(trading["date"])
                filtered = []
                for row in rows:
                    at = parse_time(row["occurred_at"])
                    value = row["payload"]
                    delay = max(
                        (quote_delay_seconds(item) for item in value.get("items", [])), default=0
                    )
                    window = next(
                        (
                            i
                            for i, interval in enumerate(windows)
                            if parse_time(interval["start"])
                            <= at
                            <= parse_time(interval["end"]) + timedelta(seconds=90 + delay)
                        ),
                        None,
                    )
                    if window is None or at <= start:
                        continue
                    fresh = True
                    delayed = False
                    for item in value.get("items", []):
                        market = item["market"]
                        if market not in {"A", "FUND", "HK", "US"}:
                            continue
                        sessions = trading_windows(trading["date"], [market])
                        if any(
                            parse_time(interval["start"])
                            <= at
                            <= parse_time(interval["end"])
                            + timedelta(seconds=90 + quote_delay_seconds(item))
                            for interval in sessions
                        ):
                            delayed |= quote_delay_seconds(item) > 0
                            if quote_is_stale(item, at):
                                fresh = False
                                break
                    filtered.append(
                        {
                            **row,
                            "payload": {
                                **value,
                                "trading_window": window,
                                "chart_stale": not fresh,
                                "quote_delayed": delayed,
                                "quote_delay_seconds": delay if delayed else 0,
                                "estimated": bool(value.get("estimated") or delayed),
                                "chart_at": timestamp(min(at, parse_time(windows[window]["end"]))),
                            },
                        }
                    )
                rows = filtered
                baseline_rows = self.repository.snapshots(start=start, end=start, kind="daily")
                if baseline_rows:
                    row = baseline_rows[0]
                    baseline_point = {
                        **row["payload"],
                        "occurred_at": row["occurred_at"],
                        "kind": "baseline",
                    }
                    baseline_points, _ = self._time_weighted_curve([baseline_point], current)
                    baseline_point = baseline_points[0]
            else:
                rows = [
                    row
                    for row in rows
                    if not row["payload"].get("date")
                    or portfolio_trading_day(parse_time(row["occurred_at"]))["date"]
                    == row["payload"]["date"]
                ]
            points = [
                {**row["payload"], "occurred_at": row["occurred_at"], "kind": kind} for row in rows
            ]
            if kind == "minute" and windows:
                close = parse_time(windows[-1]["end"])
                if current >= close:
                    cutoff = min(current, close + timedelta(minutes=5))
                    _, closing_value = self._value(cutoff)
                    closing_items = [
                        item
                        for item in closing_value["items"]
                        if any(
                            parse_time(interval["end"]) == close
                            for interval in trading_windows(trading["date"], [item["market"]])
                        )
                    ]
                    if closing_items and all(
                        item.get("market_date") == trading["date"]
                        and item.get("price_at")
                        and close - timedelta(seconds=90) <= parse_time(item["price_at"]) <= cutoff
                        for item in closing_items
                    ):
                        source_at = max(parse_time(item["price_at"]) for item in closing_items)
                        if not points or parse_time(points[-1]["occurred_at"]) < source_at:
                            points.append(
                                {
                                    **closing_value,
                                    "occurred_at": timestamp(source_at),
                                    "kind": "close",
                                    "chart_at": timestamp(close),
                                    "trading_window": len(windows) - 1,
                                    "break_before": True,
                                }
                            )
            if kind == "daily" and points:
                # Long ranges show daily history plus the newest actual sample.
                # Keep it separate so no line invents a missing intraday interval.
                latest = self.database.one(
                    "SELECT occurred_at,payload FROM investment_snapshots "
                    "WHERE kind='minute' AND occurred_at>? AND occurred_at<=? "
                    "ORDER BY occurred_at DESC LIMIT 1",
                    (points[-1]["occurred_at"], timestamp(current)),
                )
                if latest:
                    import json

                    points.append(
                        {
                            **json.loads(latest["payload"]),
                            "occurred_at": latest["occurred_at"],
                            "kind": "latest_sample",
                            "break_before": True,
                        }
                    )
            points, return_metadata = self._time_weighted_curve(points, current)
            for point in points:
                if point.get("chart_stale"):
                    for field in (
                        "total",
                        "total_assets",
                        "cumulative_pnl",
                        "time_weighted_return",
                        "compounded_return",
                    ):
                        point[field] = None
                    point["complete"] = False
        items, segments, segment, previous, previous_window = [], [], [], None, None
        gap = timedelta(seconds=90) if kind == "minute" else timedelta(hours=36)
        for point in points:
            point["cumulative_return"] = cumulative_return_percentage(
                point.get("cumulative_pnl"), point.get("net_invested")
            )
            stamp = parse_time(point["occurred_at"])
            if kind == "minute":
                with localcontext() as context:
                    context.prec = 100
                    external = ZERO
                    for flow in daily_state.external_flows:
                        if start < parse_time(flow["occurred_at"]) <= stamp:
                            if flow["amount"] is None:
                                external = None
                                break
                            external += flow["amount"]
                    total = point.get("total_assets", point.get("total"))
                    pnl = (
                        Decimal(total) - Decimal(daily_baseline["total"]) - external
                        if total is not None
                        and daily_baseline["total"] is not None
                        and external is not None
                        else None
                    )
                    point["daily_pnl"] = decimal_string(pnl)
                    point["daily_return"] = cumulative_return_percentage(
                        pnl, daily_baseline["total"]
                    )
                    point["daily_baseline_assets"] = daily_baseline["total"]
                    point["daily_net_external"] = decimal_string(external)
                    point["daily_return_estimated"] = bool(
                        daily_baseline.get("estimated") or point.get("estimated")
                    )
            valid = point.get("total") is not None and point.get("cumulative_pnl") is not None
            break_before = (
                bool(point.get("break_before"))
                or not valid
                or (previous is not None and stamp - previous > gap)
                or (
                    kind == "minute"
                    and previous_window is not None
                    and point.get("trading_window") != previous_window
                )
            )
            point["break_before"] = break_before
            if break_before and segment:
                segments.append(segment)
                segment = []
            if valid:
                segment.append(point)
            items.append(point)
            previous = stamp
            previous_window = point.get("trading_window")
        if segment:
            segments.append(segment)
        return {
            "period": period,
            "kind": kind,
            "items": items,
            "points": items,
            "segments": segments,
            "currency": "CNY",
            "complete": all(point.get("complete", False) for point in items),
            "trading_date": trading["date"] if kind == "minute" else None,
            "daily_baseline_at": timestamp(start) if kind == "minute" else None,
            "daily_baseline_assets": daily_baseline["total"] if kind == "minute" else None,
            "baseline_point": baseline_point,
            "trading_windows": windows,
            **return_metadata,
        }

    async def _call(self, name, *args):
        method = getattr(self.provider, name, None)
        if method is None:
            return []
        value = method(*args)
        return await value if inspect.isawaitable(value) else value

    def _instruments(self, records):
        return [
            {"market": market, "symbol": symbol}
            for market, symbol in sorted(
                {
                    (record["market"], record["symbol"])
                    for record in records
                    if record["symbol"]
                    and record["action"] in {"buy", "sell", "opening_position", "split"}
                }
            )
        ]

    async def _fetch_history(self, records, at):
        first = (
            min(parse_time(record["occurred_at"]) for record in records).astimezone(BEIJING).date()
        )
        # Include a prior day for a previous-known opening-day price/FX.
        start, end = (
            (first - timedelta(days=7)).isoformat(),
            at.astimezone(BEIJING).date().isoformat(),
        )
        instruments = self._instruments(records)
        currencies = sorted(
            {
                record[field]
                for record in records
                for field in ("currency", "settlement_currency")
                if record[field] != "CNY"
            }
        )
        requests = [
            ("history", instrument, self._call("history", instrument, start, end))
            for instrument in instruments
        ]
        requests += [
            ("fx_history", currency, self._call("fx_history", currency, start, end))
            for currency in currencies
        ]
        outcomes = await asyncio.gather(
            *(request[2] for request in requests), return_exceptions=True
        )
        history_warnings = []
        for (name, subject, _), outcome in zip(requests, outcomes, strict=True):
            if isinstance(outcome, BaseException):
                history_warnings.append(
                    {
                        "code": "history_refresh_failed",
                        "subject": subject,
                        "message": "历史行情或汇率获取失败，继续使用已有数据",
                    }
                )
                continue
            try:
                # Future session closes returned by an endpoint are not available yet.
                rows = [
                    {**row, "kind": "daily" if name == "history" else "historical"}
                    for row in (outcome or [])
                    if parse_time(row["occurred_at"]) <= at
                ]
                if name == "history":
                    self.repository.save_prices(rows)
                else:
                    self.repository.save_fx(rows)
            except (ValueError, KeyError, TypeError):
                history_warnings.append(
                    {"code": "invalid_history", "message": "历史行情数据格式无效，继续使用已有数据"}
                )
        self._refresh_warnings.extend(history_warnings)
        self.repository.set_setting("history_warnings", history_warnings)
        if history_warnings:
            # Keep any prior successful day; partial data do not finish today's
            # history refresh. Bound retries from the end of the failed attempt.
            self.repository.set_setting(
                "history_retry_after", timestamp(parse_time() + timedelta(minutes=10))
            )
        else:
            self.repository.set_setting("history_day", at.astimezone(BEIJING).date().isoformat())
            self.repository.set_setting("history_retry_after", None)

    async def refresh(self, *, active_only=False):
        """Serialize sampling; return immediately without market calls when empty."""
        async with self._refresh_lock:
            at = parse_time()
            records = self.repository.list_records(at=at)
            if not records:
                return self.overview(at)
            self._refresh_warnings = []
            try:
                self._sessions = await self._call("sessions", at) or []
            except Exception:
                self._sessions = []
                self._refresh_warnings.append(
                    {"code": "sessions_failed", "message": "交易时段状态暂不可用"}
                )
            instruments = self._instruments(records)
            active = {
                row["market"]
                for row in self._sessions
                if row.get("state") in {"open", "active", "trading"}
            }
            if "A" in active:
                active.add("FUND")
            closing = {
                row["market"]
                for row in self._sessions
                if row.get("state") == "closed"
                and row.get("close_at")
                and parse_time(row["close_at"])
                <= at
                <= parse_time(row["close_at"])
                + timedelta(seconds=300 + row.get("quote_delay_seconds", 0))
                and self.repository.get_setting("closing_sample:" + row["market"])
                != row.get("market_date")
            }
            if "A" in closing:
                closing.add("FUND")
            if active_only:
                instruments = [
                    instrument
                    for instrument in instruments
                    if instrument["market"] in active | closing
                ]
            cash = self._ledger().replay(records, at).cash
            currency_markets = {"USD": "US", "HKD": "HK"}
            active_foreign_cash = any(
                balance != ZERO and currency_markets.get(currency) in active
                for currency, balance in cash.items()
            )
            sample_active = bool(instruments) or active_foreign_cash
            requests = [("quotes", self._call("quotes", instruments))] if instruments else []
            has_foreign = any(
                record[field] != "CNY"
                for record in records
                for field in ("currency", "settlement_currency")
            )
            if has_foreign and (not active_only or sample_active):
                requests.append(("fx_rates", self._call("fx_rates")))
            outcomes = await asyncio.gather(
                *(request[1] for request in requests), return_exceptions=True
            )
            for (name, _), outcome in zip(requests, outcomes, strict=True):
                if isinstance(outcome, BaseException):
                    self._refresh_warnings.append(
                        {"code": "refresh_failed", "message": "行情刷新失败，继续显示已有行情"}
                    )
                    continue
                try:
                    rows = [
                        row
                        for row in (outcome or [])
                        if parse_time(row["occurred_at"]) <= parse_time()
                    ]
                    if name == "quotes":
                        self.repository.save_prices(rows)
                        for session in self._sessions:
                            market = session["market"]
                            expected = [
                                item
                                for item in instruments
                                if item["market"]
                                in ({market, "FUND"} if market == "A" else {market})
                            ]
                            if (
                                market in closing
                                and expected
                                and all(
                                    any(
                                        row["market"] == item["market"]
                                        and row["symbol"] == item["symbol"]
                                        and parse_time(row["occurred_at"])
                                        >= parse_time(session["close_at"])
                                        for row in rows
                                    )
                                    for item in expected
                                )
                            ):
                                self.repository.set_setting(
                                    "closing_sample:" + market, session["market_date"]
                                )
                    else:
                        self.repository.save_fx(rows)
                except (ValueError, KeyError, TypeError):
                    self._refresh_warnings.append(
                        {"code": "invalid_quote", "message": "行情数据格式无效，继续显示已有行情"}
                    )
            day = at.astimezone(BEIJING).date().isoformat()
            local = at.astimezone(BEIJING)
            retry_after = self.repository.get_setting("history_retry_after")
            retry_due = retry_after is None or at >= parse_time(retry_after)
            # Before 08:00 the US close for the previous Beijing day may still
            # be unavailable. Run automatic daily backfill once after 08:00.
            if (
                local.time() >= time(8)
                and self.repository.get_setting("history_day") != day
                and retry_due
            ):
                await self._fetch_history(records, at)
                rebuild = asyncio.create_task(asyncio.to_thread(self.rebuild_history))
                try:
                    await asyncio.shield(rebuild)
                except asyncio.CancelledError:
                    # to_thread cannot be cancelled. Await completion before
                    # stop() allows the database to close.
                    await rebuild
                    raise
            elif retry_after is not None:
                self._refresh_warnings.extend(self.repository.get_setting("history_warnings", []))
            observed = parse_time()
            snapshot = self.overview(observed)
            if not active_only or sample_active:
                self.repository.save_snapshot(observed, "minute", snapshot)
            self.repository.prune_minutes(observed - timedelta(days=90))
            metadata = {
                "occurred_at": timestamp(observed),
                "warnings": list(self._refresh_warnings),
                "complete": not self._refresh_warnings,
            }
            self.repository.set_setting("refresh", metadata)
            snapshot["refresh"] = metadata
            return snapshot

    async def start(self):
        """Start a dedicated 60-second background sampler, idempotently."""
        if self.worker is not None and not self.worker.done():
            return
        self._stopping = False
        self.worker = asyncio.create_task(self._loop(), name="investment-market-sampler")

    async def _loop(self):
        while not self._stopping:
            started = asyncio.get_running_loop().time()
            try:
                await self.refresh(active_only=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._refresh_warnings = [
                    {"code": "refresh_failed", "message": "投资行情刷新失败，将在下一周期重试"}
                ]
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.1, 60 - elapsed))

    async def stop(self):
        """Cancel/await worker and API's manual refresh before database shutdown."""
        self._stopping = True
        tasks = {task for task in (self.worker, self._manual_refresh) if task is not None}
        current = asyncio.current_task()
        tasks.discard(current)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        # A to_thread rebuild cannot be interrupted: drain the shared lock before
        # returning so callers can safely close the shared SQLite connection.
        await asyncio.to_thread(self._drain_database)
        self.worker = None
        self._manual_refresh = None

    def _drain_database(self):
        with self.database.lock:
            pass
