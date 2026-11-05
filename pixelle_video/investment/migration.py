"""Read-only conversion of a legacy portfolio root into the investment repository.

The root is supplied by the caller. No legacy scripts are imported or executed,
and no network access or writes to the source directory take place.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime
from decimal import Decimal, localcontext
from pathlib import Path

from .imports import (
    ALIASES,
    MARKETS,
    SHANGHAI,
    UTC,
    column_mapping,
    decimal_string,
    normalize_record,
    normalize_symbol,
    occurred_at,
)


def _load_sources(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("legacy root must be a directory")
    data = root / "data" if (root / "data").is_dir() else root
    files = [root / "config.json"]
    files.extend(
        data / name
        for name in (
            "instruments.csv",
            "transactions.csv",
            "holdings_snapshot.csv",
            "manual_prices.csv",
        )
    )
    files.append(data / "cache" / "fx.json")
    files.extend(sorted((data / "cache" / "prices").glob("*.csv")))
    sources, hashes = {}, {}
    for path in files:
        if not path.exists():
            continue
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("legacy sources must remain inside the authorized root")
        raw = resolved.read_bytes()
        relative = path.relative_to(root).as_posix()
        hashes[relative] = hashlib.sha256(raw).hexdigest()
        if path.suffix == ".json":
            sources[relative] = json.loads(raw.decode("utf-8-sig"), parse_float=Decimal)
        else:
            sources[relative] = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    digest = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    return sources, hashes, digest


def _source(sources, name):
    return next(
        ((key, value) for key, value in sources.items() if key == name or key.endswith("/" + name)),
        (name, []),
    )


def _daily_timestamp(market, day):
    from .market import daily_timestamp

    return (
        datetime.fromisoformat(daily_timestamp(market, day))
        .astimezone(UTC)
        .isoformat(timespec="microseconds")
    )


def _cache_rows(sources, warnings):
    prices, fx = [], []
    for path, rows in sources.items():
        if "/cache/prices/" not in "/" + path or not path.endswith(".csv"):
            continue
        market, separator, symbol = Path(path).stem.partition("_")
        if not separator:
            warnings.append({"code": "invalid_price_cache_name", "source": path})
            continue
        previous = None
        for index, row in sorted(
            enumerate(rows, 2), key=lambda item: item[1].get("date", item[1].get("日期", ""))
        ):
            day = row.get("date", row.get("日期"))
            try:
                day = (
                    datetime.fromisoformat(occurred_at(day)[0])
                    .astimezone(SHANGHAI)
                    .date()
                    .isoformat()
                )
                price = decimal_string(
                    row.get("price", row.get("价格")), "price", required=True, positive=True
                )
                if market == "FX":
                    currency = {
                        "HKDCNH": "HKD",
                        "USDCNH": "USD",
                        "HKDCNY": "HKD",
                        "USDCNY": "USD",
                    }.get(symbol)
                    if currency is None:
                        raise ValueError("unsupported FX cache code")
                    fx.append(
                        {
                            "currency": currency,
                            "occurred_at": occurred_at(day)[0],
                            "rate": price,
                            "source": "legacy:" + path,
                            "kind": "historical",
                        }
                    )
                else:
                    prices.append(
                        {
                            "market": market,
                            "symbol": normalize_symbol(symbol, market),
                            "occurred_at": _daily_timestamp(market, day),
                            "price": price,
                            "previous_close": previous,
                            "source": "legacy:" + path,
                            "market_date": day,
                            "kind": "daily",
                        }
                    )
                previous = price
            except ValueError as exc:
                warnings.append(
                    {
                        "code": "invalid_price_cache_row",
                        "source": path,
                        "index": index,
                        "message": str(exc),
                    }
                )
    path, entries = _source(sources, "cache/fx.json")
    if isinstance(entries, dict):
        for day, values in entries.items():
            if day.startswith("_") or not isinstance(values, dict):
                continue
            for currency, value in values.items():
                if currency.startswith("_") or currency == "CNY":
                    continue
                try:
                    # A fetched fallback is known only at its fetch time. A cache without
                    # one is available at Shanghai EOD, never at the start of its date.
                    stamp = occurred_at(values.get("_ts") or day)[0]
                    stamp = datetime.fromisoformat(stamp).isoformat(timespec="microseconds")
                    rate = decimal_string(value, "rate", required=True, positive=True)
                    fx.append(
                        {
                            "currency": currency,
                            "occurred_at": stamp,
                            "rate": rate,
                            "source": "legacy:" + path,
                            "kind": "historical",
                        }
                    )
                except ValueError as exc:
                    warnings.append(
                        {
                            "code": "invalid_fx_cache_row",
                            "source": path,
                            "date": day,
                            "currency": currency,
                            "message": str(exc),
                        }
                    )
    return prices, fx


def _historical_fx(rows, currency, stamp):
    if currency == "CNY":
        return "1"
    eligible = [row for row in rows if row["currency"] == currency and row["occurred_at"] <= stamp]
    return max(eligible, key=lambda row: row["occurred_at"])["rate"] if eligible else None


def _provenance(path, source_hash, index, raw, kind="legacy"):
    return {"kind": kind, "file": path, "hash": source_hash, "row_index": index, "raw": raw}


def _stable_id(path, source_hash, index, suffix=""):
    digest = hashlib.sha256(f"{path}\0{source_hash}\0{index}\0{suffix}".encode()).hexdigest()
    return "legacy_" + digest


def _legacy_records(sources, hashes, fx, warnings):
    path, rows = _source(sources, "transactions.csv")
    inst_path, instruments = _source(sources, "instruments.csv")
    metadata = {}
    for row in instruments:
        market = row.get("市场", row.get("market", "A"))
        market = {"MANUAL": "OTHER", "港股": "HK", "美股": "US", "A股": "A", "基金": "FUND"}.get(
            market, market
        )
        symbol = normalize_symbol(row.get("代码", row.get("symbol")), market)
        metadata[(market, symbol)] = {ALIASES.get(key, key): value for key, value in row.items()}
    records, deposits = [], []
    for index, raw in enumerate(rows, 2):
        mapping = column_mapping(list(raw))
        fields = {field: raw[column] for field, column in mapping.items()}
        note = str(fields.get("note") or "")
        if "期初建仓买入" in note:
            fields["action"] = "opening_position"
        elif "期初建仓转入" in note:
            fields["action"] = "opening_capital"
        try:
            record = normalize_record(fields)
        except ValueError as exc:
            raise ValueError(f"{path} row {index}: {exc}") from exc
        record.update(
            {
                "id": _stable_id(path, hashes[path], index),
                "source": "legacy",
                "provenance": _provenance(path, hashes[path], index, raw),
            }
        )
        instrument = metadata.get((record["market"], record["symbol"]), {})
        for field in ("name", "asset_class"):
            if instrument.get(field):
                record[field] = instrument[field]
        if record["market"] == "OTHER" and record["symbol"]:
            record["estimated"] = True
            record["valuation_kind"] = "manual_estimate"
        if record["market"] == "HK":
            rate = _historical_fx(fx, "HKD", record["occurred_at"])
            record["settlement_currency"] = "CNY"
            if rate is None:
                warnings.append(
                    {
                        "code": "missing_historical_fx",
                        "record_id": record["id"],
                        "currency": "HKD",
                        "date": record["occurred_at"],
                    }
                )
            else:
                record["fx_rate"] = rate
            record["estimated"] = True
            record["settlement_amount"] = None
        if record["action"] == "opening_capital":
            deposits.append(record)
        else:
            records.append(record)

    # One legacy transfer includes both the cost already held and any residual cash.
    # Allocate each currency's costs once, rather than counting the snapshot as trades.
    position_costs = {}
    for record in records:
        if record["action"] == "opening_position":
            key = (record["occurred_at"], record["currency"])
            position_costs[key] = (
                position_costs.get(key, Decimal("0"))
                + Decimal(record["amount"])
                + Decimal(record["fee"])
            )
    allocated = {}
    for deposit in deposits:
        key = (deposit["occurred_at"], deposit["currency"])
        total = Decimal(deposit["amount"])
        cost = max(
            Decimal("0"), position_costs.get(key, Decimal("0")) - allocated.get(key, Decimal("0"))
        )
        capital, residual = min(total, cost), max(Decimal("0"), total - cost)
        allocated[key] = allocated.get(key, Decimal("0")) + capital
        origin_currency = deposit["currency"]
        rate = _historical_fx(fx, origin_currency, deposit["occurred_at"])
        if origin_currency == "HKD" and rate is None:
            raise ValueError(
                "HKD opening transfer requires historical source-day FX; current fallback is not historical evidence"
            )
        for action, value in (("opening_capital", capital), ("opening_cash", residual)):
            if value <= 0:
                continue
            record = dict(deposit)
            record["id"] = _stable_id(
                path, hashes[path], deposit["provenance"]["row_index"], action
            )
            record["action"] = action
            record["amount"] = format(value, "f")
            if origin_currency == "HKD":
                record["currency"] = record["settlement_currency"] = "CNY"
                record["amount"] = format(value * Decimal(rate), "f")
                record["estimated"] = True
                record["provenance"] = {
                    **record["provenance"],
                    "source_currency": "HKD",
                    "source_amount": format(value, "f"),
                    "historical_fx": rate,
                }
            records.append(record)
    for key, value in position_costs.items():
        if key not in allocated and value:
            warnings.append({"code": "missing_opening_capital", "currency": key[1], "date": key[0]})
        elif allocated.get(key, Decimal("0")) < value:
            warnings.append(
                {"code": "opening_capital_below_position_cost", "currency": key[1], "date": key[0]}
            )
    return records, metadata


def _snapshot_role(root, records):
    """Read documentation as role evidence, outside the financial source digest.

    README was not part of the original migration digest. Keep that contract so
    adding report metadata cannot change existing record IDs or invalidate a retry.
    Only retain its hash and classified role evidence; README content stays private.
    """
    root = Path(root).resolve(strict=True)
    readme = root / "README.md"
    if readme.exists():
        resolved = readme.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError("legacy sources must remain inside the authorized root")
        raw = resolved.read_bytes()
        text = raw.decode("utf-8-sig")
        evidence = {"file": "README.md", "hash": hashlib.sha256(raw).hexdigest()}
        for section in re.split(r"(?m)(?=^#{1,6}\s)", text):
            if "holdings_snapshot.csv" not in section:
                continue
            heading = section.splitlines()[0] if section else ""
            if re.search(r"期初|初始|第一次|initial|opening", heading, re.IGNORECASE) and re.search(
                r"建仓|init_from_snapshot|initial|opening", section, re.IGNORECASE
            ):
                return "initial", {**evidence, "basis": "readme_initial_snapshot"}
            if re.search(r"当前|期末|最新|current|closing|latest", heading, re.IGNORECASE):
                return "current", {**evidence, "basis": "readme_current_snapshot"}
        return "unknown", {**evidence, "basis": "readme_role_unspecified"}
    if any(record["action"] == "opening_position" for record in records):
        return "initial", {"basis": "explicit_opening_records_without_readme"}
    return "unknown", {"basis": "snapshot_role_unspecified"}


def _snapshot_reconciliation(root, sources, records, inception):
    """Compare initial quantities only; never replay subsequent trades or mutate them."""
    path, rows = _source(sources, "holdings_snapshot.csv")
    role, evidence = _snapshot_role(root, records)
    report = {
        "version": 1,
        "role": role,
        "role_evidence": evidence,
        "source": path,
        "snapshot_date": inception,
        "snapshot_rows": len(rows),
        "comparison_basis": "legacy_opening_positions_at_inception",
        "validation_only": True,
        "discrepancies": [],
        "errors": [],
    }
    warnings = []
    if path not in sources:
        report.update({"role": "missing", "status": "unavailable", "complete": False})
        return report, warnings
    if role != "initial":
        report.update({"status": "not_compared", "complete": False})
        warnings.append(
            {
                "code": "opening_snapshot_role_unverified",
                "source": path,
                "message": "持仓快照未明确用于期初建仓，未将其与期初持仓比较或写入账本",
            }
        )
        return report, warnings
    snapshot, opening = {}, {}
    for index, raw in enumerate(rows, 2):
        fields = {field: raw[column] for field, column in column_mapping(list(raw)).items()}
        try:
            market_text = str(fields.get("market") or "").strip()
            market = MARKETS.get(market_text, market_text.upper())
            if market not in {"A", "FUND", "US", "HK", "OTHER"}:
                raise ValueError("market: unsupported snapshot market")
            symbol = normalize_symbol(fields.get("symbol"), market)
            quantity = Decimal(decimal_string(fields.get("quantity"), "quantity", required=True))
        except ValueError as exc:
            report["errors"].append({"index": index, "message": str(exc)})
            warnings.append(
                {
                    "code": "opening_snapshot_invalid_row",
                    "source": path,
                    "index": index,
                    "message": "期初持仓快照包含无效的市场、代码或数量，未修改账本",
                }
            )
            continue
        key = market, symbol
        item = snapshot.setdefault(key, {"quantity": Decimal("0"), "indices": []})
        item["quantity"] += quantity
        item["indices"].append(index)
        if len(item["indices"]) == 2:
            warnings.append(
                {
                    "code": "opening_snapshot_duplicate_security",
                    "source": path,
                    "market": market,
                    "symbol": symbol,
                    "message": "期初快照同一标的出现多行，校验按数量合计比较，未修改账本",
                }
            )
    for record in records:
        if record["action"] != "opening_position" or record.get("voided", False):
            continue
        day = datetime.fromisoformat(record["occurred_at"]).astimezone(SHANGHAI).date().isoformat()
        if day != inception:
            continue
        key = record["market"], record["symbol"]
        if key in opening:
            warnings.append(
                {
                    "code": "opening_snapshot_repeated_opening_position",
                    "market": key[0],
                    "symbol": key[1],
                    "message": "同一期初标的有多条建仓记录，校验按账本替换规则使用最后一条，未修改账本",
                }
            )
        # opening_position replaces the position, matching ledger semantics.
        opening[key] = {"quantity": Decimal(record["quantity"]), "record_id": record["id"]}
    for key in sorted(snapshot.keys() | opening.keys()):
        expected = snapshot.get(key, {}).get("quantity", Decimal("0"))
        actual = opening.get(key, {}).get("quantity", Decimal("0"))
        if actual == expected:
            continue
        code = (
            "quantity_mismatch"
            if key in snapshot and key in opening
            else "missing_opening_position"
            if key in snapshot
            else "missing_snapshot_position"
        )
        discrepancy = {
            "code": code,
            "market": key[0],
            "symbol": key[1],
            "snapshot_quantity": format(expected, "f"),
            "opening_quantity": format(actual, "f"),
            "difference": format(actual - expected, "f"),
            "snapshot_indices": snapshot.get(key, {}).get("indices", []),
            "opening_record_id": opening.get(key, {}).get("record_id"),
        }
        report["discrepancies"].append(discrepancy)
        warnings.append(
            {
                "code": "opening_snapshot_quantity_discrepancy",
                "source": path,
                "market": key[0],
                "symbol": key[1],
                "discrepancy": code,
                "message": "期初建仓记录数量与初始持仓快照不一致，仅记录差异，未覆盖持仓或现金",
            }
        )
    report.update(
        {
            "status": "compared",
            "complete": not report["errors"],
            "matched": not report["errors"] and not report["discrepancies"],
            "snapshot_positions": len(snapshot),
            "opening_positions": len(opening),
        }
    )
    return report, warnings


def _enrich_snapshot_report(repository, root, sources, hashes, previous):
    """Upgrade a prior report from its unchanged sources, touching settings only."""
    if previous.get("opening_snapshot_reconciliation", {}).get("version") == 1:
        return previous
    with localcontext() as context:
        context.prec = 100
        path, rows = _source(sources, "transactions.csv")
        records = []
        for index, raw in enumerate(rows, 2):
            fields = {field: raw[column] for field, column in column_mapping(list(raw)).items()}
            if "期初建仓买入" not in str(fields.get("note") or "") and fields.get("action") not in {
                "opening_position",
                "期初持仓",
            }:
                continue
            fields["action"] = "opening_position"
            record = normalize_record(fields)
            records.append({**record, "id": _stable_id(path, hashes[path], index)})
        if not rows:
            # A holdings-only migration established its openings from these rows.
            path, rows = _source(sources, "holdings_snapshot.csv")
            for index, raw in enumerate(rows, 2):
                fields = {field: raw[column] for field, column in column_mapping(list(raw)).items()}
                fields.update({"action": "opening_position", "occurred_at": previous["inception"]})
                record = normalize_record(fields)
                records.append({**record, "id": _stable_id(path, hashes[path], index)})
        comparison, warnings = _snapshot_reconciliation(
            root, sources, records, previous["inception"]
        )
    enriched = {
        **previous,
        "opening_snapshot_reconciliation": comparison,
        "warnings": [*previous.get("warnings", []), *warnings],
    }
    repository.set_setting("migration", enriched)
    return enriched


def migrate_legacy(database, root):
    """Migrate an explicit authorized root once; return a JSON-safe audit report.

    Identical sources return the stored report, enriching missing snapshot metadata
    in settings only. Changed sources require a fresh database or explicit review
    rather than silently duplicating opening records.
    """
    from .imports import _persist_batch
    from .ledger import Ledger, timestamp
    from .ledger import normalize_record as ledger_normalize
    from .repository import InvestmentRepository, _json

    repository = (
        database if isinstance(database, InvestmentRepository) else InvestmentRepository(database)
    )
    sources, hashes, digest = _load_sources(root)
    with repository.database.lock:
        previous = repository.get_setting("migration")
        if previous:
            if previous.get("source_hash") != digest:
                raise ValueError(
                    "legacy migration sources changed; refusing to duplicate or replace an existing migration"
                )
            previous = _enrich_snapshot_report(repository, root, sources, hashes, previous)
            return {**previous, "already_migrated": True}
        if repository.list_records(include_voided=True):
            raise ValueError(
                "legacy migration requires an empty ledger; existing transactions or opening snapshots overlap"
            )
        warnings = []
        with localcontext() as context:
            context.prec = 100
            prices, fx = _cache_rows(sources, warnings)
            records, instruments = _legacy_records(sources, hashes, fx, warnings)
            _, config = _source(sources, "config.json")
            config = config if isinstance(config, dict) else {}
            inception = config.get("inception")
            snapshot_path, snapshot_rows = _source(sources, "holdings_snapshot.csv")
            if not records and snapshot_rows:
                if not inception:
                    raise ValueError("holdings-only legacy sources require config.inception")
                for index, raw in enumerate(snapshot_rows, 2):
                    fields = {
                        field: raw[column] for field, column in column_mapping(list(raw)).items()
                    }
                    fields.update({"action": "opening_position", "occurred_at": inception})
                    record = normalize_record(fields)
                    record.update(
                        {
                            "id": _stable_id(snapshot_path, hashes[snapshot_path], index),
                            "source": "legacy",
                            "provenance": _provenance(
                                snapshot_path, hashes[snapshot_path], index, raw
                            ),
                        }
                    )
                    if record["market"] == "HK":
                        rate = _historical_fx(fx, "HKD", record["occurred_at"])
                        if rate is None:
                            raise ValueError("HKD opening snapshot requires historical FX")
                        record.update({"fx_rate": rate, "estimated": True})
                    if record["market"] == "OTHER":
                        record.update({"estimated": True, "valuation_kind": "manual_estimate"})
                    records.append(record)
                    capital = {
                        **record,
                        "id": record["id"] + "_capital",
                        "action": "opening_capital",
                        "symbol": "",
                        "quantity": "0",
                        "price": "0",
                        "external_id": None,
                        "note": "Opening capital derived from legacy holdings-only snapshot",
                    }
                    if record["market"] == "HK":
                        capital.update(
                            {
                                "currency": "CNY",
                                "settlement_currency": "CNY",
                                "amount": format(
                                    Decimal(record["amount"]) * Decimal(record["fx_rate"]), "f"
                                ),
                            }
                        )
                    records.append(capital)
            records = [ledger_normalize(record) for record in records]
            if not records:
                raise ValueError("legacy source has no investment transactions or holdings")
            manual_path, manual_rows = _source(sources, "manual_prices.csv")
            for index, raw in enumerate(manual_rows, 2):
                try:
                    market = str(raw.get("市场", raw.get("market", "OTHER"))).upper()
                    market = "OTHER" if market == "MANUAL" else market
                    symbol = normalize_symbol(raw.get("代码", raw.get("symbol")), market)
                    stamp = occurred_at(raw.get("日期", raw.get("date")))[0]
                    price = decimal_string(
                        raw.get("价格", raw.get("price")), "price", required=True, positive=True
                    )
                    prices.append(
                        {
                            "market": market,
                            "symbol": symbol,
                            "occurred_at": stamp,
                            "price": price,
                            "previous_close": None,
                            "source": "legacy:" + manual_path,
                            "market_date": datetime.fromisoformat(stamp)
                            .astimezone(SHANGHAI)
                            .date()
                            .isoformat(),
                            "kind": "manual",
                        }
                    )
                except ValueError as exc:
                    raise ValueError(f"{manual_path} row {index}: {exc}") from exc
            dates = [
                datetime.fromisoformat(record["occurred_at"])
                .astimezone(SHANGHAI)
                .date()
                .isoformat()
                for record in records
            ]
            inception = occurred_at(inception or min(dates))[0]
            inception_day = (
                datetime.fromisoformat(inception).astimezone(SHANGHAI).date().isoformat()
            )
            comparison, snapshot_warnings = _snapshot_reconciliation(
                root, sources, records, inception_day
            )
            warnings.extend(snapshot_warnings)
            state = Ledger(lambda currency, at: _historical_fx(fx, currency, at)).replay(
                records, max(record["occurred_at"] for record in records)
            )
            warnings.extend(state.warnings)
            for currency, balance in state.cash.items():
                if balance is None:
                    warnings.append(
                        {"code": "cash_reconciliation_incomplete", "currency": currency}
                    )
                elif balance < 0:
                    warnings.append(
                        {
                            "code": "cash_reconciliation_negative",
                            "currency": currency,
                            "message": "历史结算金额超过已记录的入金，请核对券商现金对账单",
                        }
                    )
            warnings.append(
                {
                    "code": "preopening_sold_pnl_unavailable",
                    "message": "旧持仓快照不含起始日前已卖出资产的已实现盈亏，历史成本仅覆盖保留的持仓",
                }
            )
            if any(
                record.get("provenance", {}).get("source_currency") == "HKD" for record in records
            ):
                warnings.append(
                    {
                        "code": "virtual_hkd_capital_converted",
                        "message": "旧虚拟港币入金已按历史汇率折为人民币期初投入，后续港股交易按人民币估算结算",
                    }
                )
            report = {
                "version": 1,
                "source_hash": digest,
                "source_files": hashes,
                "created_at": timestamp(),
                "inception": inception_day,
                "source_range": {"start": min(dates), "end": max(dates)},
                "record_count": len(records),
                "price_count": len(prices),
                "fx_count": len(fx),
                "holdings_snapshot_rows": len(snapshot_rows),
                "instrument_count": len(instruments),
                "opening_snapshot_reconciliation": comparison,
                "estimated": state.estimated,
                "warnings": warnings,
                "already_migrated": False,
                "history": {
                    "preopening_sold_pnl_available": False,
                    "opening_costs_preserved": True,
                },
                "config": {
                    "base_currency": config.get("base_currency", "CNY"),
                    "inception": inception_day,
                },
            }
            # Convert Decimal config fields and raw-source numeric values with the
            # repository serializer before returning as well as before persistence.
            report = json.loads(_json(report))
            _persist_batch(
                repository,
                records,
                prices=prices,
                fx=fx,
                settings={"migration": report, "inception": inception_day},
            )
            return report
