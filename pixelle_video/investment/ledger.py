"""Decimal investment ledger, independent of storage and market/network providers.

FX rates are CNY per unit of currency. Transaction ``amount`` and ``fee`` are
positive gross amounts in quote currency; settlement equivalents are also gross.
Exchange consumes ``amount`` of ``currency`` and receives ``settlement_amount``
of ``settlement_currency``. Split ``quantity`` is the new/old share ratio.
Opening positions replace quantity and cost without changing cash or capital.
Missing financial data propagates as None, never as an assumed zero.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, localcontext
from typing import Callable
from zoneinfo import ZoneInfo

UTC = timezone.utc
BEIJING = ZoneInfo("Asia/Shanghai")
ZERO = Decimal("0")
ONE = Decimal("1")
MARKETS = {"A", "HK", "US", "FUND", "OTHER", "CASH"}
ACTIONS = {
    "buy",
    "sell",
    "deposit",
    "withdraw",
    "dividend",
    "fee",
    "exchange",
    "split",
    "opening_position",
    "opening_capital",
    "opening_cash",
}
DEFAULT_CURRENCIES = {
    "A": "CNY",
    "HK": "HKD",
    "US": "USD",
    "FUND": "CNY",
    "OTHER": "CNY",
    "CASH": "CNY",
}


class LedgerError(ValueError):
    """An invalid record or impossible long-only ledger transition."""


def decimal_value(value, field_name="value", *, nullable=False):
    """Parse exact finite decimal input; reject floats, booleans and NaN/Infinity."""
    if value is None and nullable:
        return None
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int, Decimal)):
        raise LedgerError(f"{field_name} must be a decimal string")
    try:
        result = Decimal(value)
    except (InvalidOperation, ValueError):
        raise LedgerError(f"{field_name} must be a decimal string") from None
    if not result.is_finite() or len(result.as_tuple().digits) > 80 or abs(result.adjusted()) > 80:
        raise LedgerError(f"{field_name} must be a finite decimal of at most 80 digits")
    return result


def decimal_string(value):
    """JSON-safe decimal, without exponent notation or negative zero."""
    if value is None:
        return None
    if value == 0:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def cumulative_return_percentage(pnl, net_invested):
    """Simple cumulative capital return in percentage points; not annualized.

    Missing valuation or nonpositive net contributions has no meaningful ratio.
    Accept saved decimal strings so legacy snapshots need no database rewrite.
    """
    if pnl is None or net_invested is None:
        return None
    pnl, net_invested = Decimal(pnl), Decimal(net_invested)
    if not pnl.is_finite() or not net_invested.is_finite() or net_invested <= ZERO:
        return None
    with localcontext() as context:
        context.prec = 100
        return decimal_string(pnl / net_invested * 100)


def parse_time(value=None, *, date_only=False):
    """Parse an aware ISO timestamp. Date-only input means Beijing day end."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date) or (
        isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
    ):
        if not date_only:
            raise LedgerError("occurred_at requires a timezone; set date_only for a date")
        try:
            day = value if isinstance(value, date) else date.fromisoformat(value)
            result = datetime.combine(day, time.max, BEIJING)
        except ValueError:
            raise LedgerError("Invalid occurred_at") from None
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise LedgerError("Invalid occurred_at") from None
    else:
        raise LedgerError("Invalid occurred_at")
    if result.tzinfo is None or result.utcoffset() is None:
        raise LedgerError("occurred_at requires a timezone")
    return result.astimezone(UTC)


def timestamp(value=None, *, date_only=False):
    """Canonical UTC timestamp; fixed precision keeps SQLite time ordering exact."""
    return parse_time(value, date_only=date_only).isoformat(timespec="microseconds")


def normalize_record(record):
    """Return the complete public record schema and validate action requirements."""
    if not isinstance(record, dict):
        raise LedgerError("Record must be an object")
    result = dict(record)
    action = result.get("action")
    if action not in ACTIONS:
        raise LedgerError("Unknown investment action")
    market = result.get("market", "OTHER")
    if market not in MARKETS:
        raise LedgerError("Unknown investment market")
    for key in ("date_only", "estimated", "voided"):
        if not isinstance(result.get(key, False), bool):
            raise LedgerError(f"{key} must be a boolean")
        result[key] = result.get(key, False)
    result["id"] = result.get("id") or uuid.uuid4().hex
    if not isinstance(result["id"], str) or not result["id"].strip():
        raise LedgerError("id must be a nonempty string")
    occurred = parse_time(result.get("occurred_at"), date_only=result["date_only"])
    if result["date_only"]:
        occurred = datetime.combine(occurred.astimezone(BEIJING).date(), time.max, BEIJING)
    result["occurred_at"] = timestamp(occurred)
    result["action"], result["market"] = action, market
    for key in ("symbol", "name", "note", "source"):
        result[key] = result.get(key, "manual" if key == "source" else "")
        if not isinstance(result[key], str):
            raise LedgerError(f"{key} must be a string")
        result[key] = result[key].strip() if key == "symbol" else result[key]
    if action in {"buy", "sell", "split", "opening_position"} and not result["symbol"]:
        raise LedgerError(f"{action} requires a symbol")
    if market == "CASH" and action in {"buy", "sell", "split", "opening_position"}:
        raise LedgerError("CASH market cannot hold a security position")
    currency = result.get("currency") or DEFAULT_CURRENCIES[market]
    settlement = result.get("settlement_currency") or ("CNY" if market == "HK" else currency)
    for key, value in (("currency", currency), ("settlement_currency", settlement)):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{3}", value):
            raise LedgerError(f"{key} must be a three-letter currency")
        result[key] = value.upper()
    for key in ("quantity", "price", "amount", "fee"):
        if (
            key == "amount"
            and action in {"buy", "sell", "opening_position"}
            and (result.get(key) is None or decimal_value(result.get(key), key) == 0)
        ):
            with localcontext() as context:
                context.prec = 100
                value = decimal_value(result.get("quantity", "0"), "quantity") * decimal_value(
                    result.get("price", "0"), "price"
                )
        else:
            value = decimal_value(result.get(key, "0"), key)
        if value < 0:
            raise LedgerError(f"{key} cannot be negative")
        result[key] = decimal_string(value)
    for key in ("settlement_amount", "settlement_fee", "fx_rate"):
        value = decimal_value(result.get(key), key, nullable=True)
        if value is not None and (value < 0 or (key == "fx_rate" and value == 0)):
            raise LedgerError(f"{key} must be {'positive' if key == 'fx_rate' else 'nonnegative'}")
        result[key] = decimal_string(value)
    if action in {"buy", "sell", "split", "opening_position"} and Decimal(result["quantity"]) <= 0:
        raise LedgerError(f"{action} requires positive quantity")
    if action == "exchange" and result["currency"] == result["settlement_currency"]:
        raise LedgerError("exchange requires two different currencies")
    if result.get("external_id") is not None and not isinstance(result["external_id"], str):
        raise LedgerError("external_id must be a string or null")
    result.setdefault("external_id", None)
    return result


def _add(left, right):
    return None if left is None or right is None else left + right


def _subtract(left, right):
    return None if left is None or right is None else left - right


@dataclass
class Holding:
    market: str
    symbol: str
    currency: str
    name: str = ""
    quantity: Decimal = ZERO
    cost_native: Decimal | None = ZERO
    cost: Decimal | None = ZERO
    realized_pnl: Decimal | None = ZERO
    realized_pnl_native: Decimal | None = ZERO
    estimated: bool = False


@dataclass
class LedgerState:
    """Replay result. Currency balances and all monetary state remain Decimal."""

    cash: dict[str, Decimal | None] = field(default_factory=dict)
    holdings: dict[tuple[str, str], Holding] = field(default_factory=dict)
    net_invested: Decimal | None = ZERO
    realized_pnl: Decimal | None = ZERO
    external_flows: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    estimated: bool = False
    record_count: int = 0


class Ledger:
    """Replay using an injected historical FX lookup: (currency, at) -> rate.

    No network access is performed. The caller must supply only historical rates
    known on/before the event. Missing rates keep replay possible but incomplete.
    """

    def __init__(self, historical_fx: Callable | None = None):
        self.historical_fx = historical_fx or (lambda currency, at: None)

    def replay(self, records, at=None):
        cutoff = parse_time(at)
        prepared = [
            normalize_record(record) for record in records if not record.get("voided", False)
        ]
        # Stable input order breaks timestamp ties; opening records precede trades.
        prepared.sort(
            key=lambda record: (
                record["occurred_at"],
                0 if record["action"].startswith("opening_") else 1,
            )
        )
        state = LedgerState()
        with localcontext() as context:
            context.prec = 100
            for record in prepared:
                if parse_time(record["occurred_at"]) <= cutoff:
                    self._apply(state, record)
                    state.record_count += 1
        return state

    def _warn(self, state, record, code, currency):
        warning = {
            "code": code,
            "record_id": record["id"],
            "currency": currency,
            "message": f"缺少 {record['occurred_at']} 之前的 {currency} 历史汇率",
        }
        if warning not in state.warnings:
            state.warnings.append(warning)

    def _rate(self, state, record, currency):
        if currency == "CNY":
            return ONE
        value = self.historical_fx(currency, record["occurred_at"])
        if isinstance(value, dict):
            value = value.get("rate")
        rate = decimal_value(value, "historical FX", nullable=True)
        if rate is None or rate <= 0:
            self._warn(state, record, "missing_historical_fx", currency)
            return None
        return rate

    def _base(self, state, record, value, currency):
        if value is None:
            return None
        if value == 0:
            return ZERO
        rate = self._rate(state, record, currency)
        return None if rate is None else value * rate

    def _settlement(self, state, record):
        amount, fee = Decimal(record["amount"]), Decimal(record["fee"])
        gross = decimal_value(record["settlement_amount"], nullable=True)
        settled_fee = decimal_value(record["settlement_fee"], nullable=True)
        same = record["currency"] == record["settlement_currency"]
        conversion = decimal_value(record["fx_rate"], nullable=True)
        if same:
            conversion = ONE
        elif (gross is None and amount != 0) or (settled_fee is None and fee != 0):
            state.estimated = True
            record["estimated"] = True
            if conversion is None:
                origin = self._rate(state, record, record["currency"])
                target = self._rate(state, record, record["settlement_currency"])
                conversion = origin / target if origin is not None and target is not None else None
        if gross is None:
            gross = ZERO if amount == 0 else (None if conversion is None else amount * conversion)
        if settled_fee is None:
            settled_fee = ZERO if fee == 0 else (None if conversion is None else fee * conversion)
        return gross, settled_fee

    @staticmethod
    def _cash(state, currency, delta):
        state.cash[currency] = _add(state.cash.get(currency, ZERO), delta)

    def _external(self, state, record, value):
        state.net_invested = _add(state.net_invested, value)
        state.external_flows.append({"occurred_at": record["occurred_at"], "amount": value})

    def _apply(self, state, record):
        action, currency, settlement = (
            record["action"],
            record["currency"],
            record["settlement_currency"],
        )
        quantity, amount, fee = (
            Decimal(record["quantity"]),
            Decimal(record["amount"]),
            Decimal(record["fee"]),
        )
        state.estimated |= record["estimated"]
        if action == "split":
            holding = state.holdings.get((record["market"], record["symbol"]))
            if holding is None or holding.quantity <= 0:
                raise LedgerError(f"Cannot split an empty position: {record['symbol']}")
            holding.quantity *= quantity
            return
        if action == "exchange":
            received, settled_fee = self._settlement(state, record)
            self._cash(state, currency, -(amount + fee))
            # Exchange fee is charged in origin currency unless explicitly settled.
            explicit_fee = decimal_value(record["settlement_fee"], nullable=True)
            self._cash(state, settlement, _subtract(received, explicit_fee or ZERO))
            origin_fee = self._base(state, record, fee, currency)
            target_fee = self._base(state, record, explicit_fee or ZERO, settlement)
            state.realized_pnl = _subtract(state.realized_pnl, _add(origin_fee, target_fee))
            return
        gross, settled_fee = self._settlement(state, record)
        paid = _add(gross, settled_fee)
        proceeds = _subtract(gross, settled_fee)
        if action in {"deposit", "withdraw", "opening_cash", "opening_capital"}:
            sign = -ONE if action == "withdraw" else ONE
            if action != "opening_capital":
                cash_delta = -paid if action == "withdraw" and paid is not None else proceeds
                self._cash(state, settlement, cash_delta)
            # Fees reduce performance, not the amount of external capital.
            external = self._base(state, record, gross, settlement)
            self._external(state, record, None if external is None else external * sign)
            if action != "opening_capital":
                state.realized_pnl = _subtract(
                    state.realized_pnl, self._base(state, record, settled_fee, settlement)
                )
        elif action in {"dividend", "fee"}:
            delta = proceeds if action == "dividend" else (None if paid is None else -paid)
            self._cash(state, settlement, delta)
            state.realized_pnl = _add(
                state.realized_pnl, self._base(state, record, delta, settlement)
            )
        elif action in {"buy", "sell", "opening_position"}:
            key = (record["market"], record["symbol"])
            holding = state.holdings.setdefault(key, Holding(*key, currency, record["name"]))
            if holding.currency != currency:
                raise LedgerError(f"Quote currency changed for {record['symbol']}")
            if record["name"]:
                holding.name = record["name"]
            holding.estimated |= record["estimated"]
            native_paid = amount + fee
            base_paid = self._base(state, record, paid, settlement)
            if action == "opening_position":
                holding.quantity, holding.cost_native, holding.cost = (
                    quantity,
                    native_paid,
                    base_paid,
                )
            elif action == "buy":
                self._cash(state, settlement, None if paid is None else -paid)
                holding.quantity += quantity
                holding.cost_native = _add(holding.cost_native, native_paid)
                holding.cost = _add(holding.cost, base_paid)
            else:
                if quantity > holding.quantity:
                    raise LedgerError(f"Sell exceeds position for {record['symbol']}")
                fraction = quantity / holding.quantity
                released_native = (
                    None if holding.cost_native is None else holding.cost_native * fraction
                )
                released = None if holding.cost is None else holding.cost * fraction
                realized = _subtract(self._base(state, record, proceeds, settlement), released)
                holding.realized_pnl = _add(holding.realized_pnl, realized)
                holding.realized_pnl_native = _add(
                    holding.realized_pnl_native, _subtract(amount - fee, released_native)
                )
                state.realized_pnl = _add(state.realized_pnl, realized)
                holding.quantity -= quantity
                holding.cost_native = _subtract(holding.cost_native, released_native)
                holding.cost = _subtract(holding.cost, released)
                if holding.quantity == 0:
                    holding.cost_native, holding.cost = ZERO, ZERO
                self._cash(state, settlement, proceeds)


def replay(records, at=None, historical_fx=None):
    """Convenience entry point for callers that do not need a Ledger instance."""
    return Ledger(historical_fx).replay(records, at)


def value_state(state, at, price_lookup, fx_lookup):
    """Value replay state using previous-known quotes/FX; return JSON-safe fields."""
    at = timestamp(at)
    warnings = list(state.warnings)
    missing_prices, missing_fx = [], []
    fx_sources = {}

    def rate(currency):
        if currency == "CNY":
            return ONE
        row = fx_lookup(currency, at)
        if isinstance(row, dict):
            fx_sources[currency] = row
        value = row.get("rate") if isinstance(row, dict) else row
        result = decimal_value(value, "FX", nullable=True)
        if result is None or result <= 0:
            if currency not in missing_fx:
                missing_fx.append(currency)
                warnings.append(
                    {
                        "code": "missing_fx",
                        "currency": currency,
                        "message": f"缺少 {currency} 估值汇率",
                    }
                )
            return None
        return result

    with localcontext() as context:
        context.prec = 100
        balances, cash_total = [], ZERO
        for currency, balance in sorted(state.cash.items()):
            conversion = ONE if balance == ZERO else rate(currency)
            base = None if balance is None or conversion is None else balance * conversion
            cash_total = _add(cash_total, base)
            balances.append(
                {
                    "currency": currency,
                    "amount": decimal_string(balance),
                    "balance": decimal_string(balance),
                    "rate": decimal_string(conversion),
                    "value": decimal_string(base),
                    "cny_value": decimal_string(base),
                    "market_value": decimal_string(base),
                    "fx_at": fx_sources.get(currency, {}).get("occurred_at"),
                    "fx_source": fx_sources.get(currency, {}).get("source"),
                }
            )
        items, holdings_total = [], ZERO
        for key, holding in sorted(state.holdings.items()):
            if holding.quantity == 0:
                continue
            quote = price_lookup(holding.market, holding.symbol, at)
            price = decimal_value(quote.get("price"), "price", nullable=True) if quote else None
            previous = (
                decimal_value(quote.get("previous_close"), "previous_close", nullable=True)
                if quote
                else None
            )
            if price is None:
                missing_prices.append({"market": holding.market, "symbol": holding.symbol})
                warnings.append(
                    {
                        "code": "missing_price",
                        "market": holding.market,
                        "symbol": holding.symbol,
                        "message": f"缺少 {holding.market}:{holding.symbol} 的历史行情",
                    }
                )
            conversion = rate(holding.currency)
            native_value = None if price is None else holding.quantity * price
            base_value = (
                None if native_value is None or conversion is None else native_value * conversion
            )
            pnl = _subtract(base_value, holding.cost)
            daily = (
                None
                if price is None or previous is None or previous == 0
                else (price / previous - ONE) * 100
            )
            item = {
                "market": holding.market,
                "symbol": holding.symbol,
                "name": holding.name,
                "currency": holding.currency,
                "quantity": decimal_string(holding.quantity),
                "price": decimal_string(price),
                "previous_close": decimal_string(previous),
                "price_at": quote.get("occurred_at") if quote else None,
                "source": quote.get("source") if quote else None,
                "market_date": quote.get("market_date") if quote else None,
                "cost": decimal_string(holding.cost),
                "cost_native": decimal_string(holding.cost_native),
                "average_cost": decimal_string(
                    None if holding.cost_native is None else holding.cost_native / holding.quantity
                ),
                "market_value_native": decimal_string(native_value),
                "market_value": decimal_string(base_value),
                "pnl": decimal_string(pnl),
                "pnl_native": decimal_string(_subtract(native_value, holding.cost_native)),
                "realized_pnl": decimal_string(holding.realized_pnl),
                "realized_pnl_native": decimal_string(holding.realized_pnl_native),
                "daily_change": decimal_string(daily),
                "weight": None,
                "estimated": holding.estimated,
                "fx_rate": decimal_string(conversion),
                "fx_at": fx_sources.get(holding.currency, {}).get("occurred_at"),
                "fx_source": fx_sources.get(holding.currency, {}).get("source"),
                "complete": base_value is not None and holding.cost is not None,
            }
            # Manual valuations verify the displayed OTHER asset value; the
            # opening record's estimate/provenance remains in storage and audit.
            item["cost_estimated"] = holding.estimated
            if holding.market == "OTHER" and quote and quote.get("source") == "manual":
                item["estimated"] = False
                item["valuation_verified"] = True
            else:
                item["valuation_verified"] = not item["estimated"] and base_value is not None
            items.append(item)
            holdings_total = _add(holdings_total, base_value)
        total = _add(cash_total, holdings_total)
        cumulative_pnl = _subtract(total, state.net_invested)
        for item in items:
            if total is not None and total != 0 and item["market_value"] is not None:
                item["weight"] = decimal_string(Decimal(item["market_value"]) / total * 100)
        complete = total is not None and state.net_invested is not None
        completeness = {
            "complete": complete,
            "missing_prices": missing_prices,
            "missing_fx": missing_fx,
            "estimated": state.estimated,
            "historical_fx_complete": not state.warnings,
        }
        return {
            "occurred_at": at,
            "currency": "CNY",
            "base_currency": "CNY",
            "total": decimal_string(total),
            "total_assets": decimal_string(total),
            "cash": decimal_string(cash_total),
            "cash_balances": balances,
            "holdings_value": decimal_string(holdings_total),
            "net_invested": decimal_string(state.net_invested),
            "cumulative_pnl": decimal_string(cumulative_pnl),
            "cumulative_return": cumulative_return_percentage(cumulative_pnl, state.net_invested),
            "realized_pnl": decimal_string(state.realized_pnl),
            "items": items,
            "warnings": warnings,
            "completeness": completeness,
            "complete": complete,
            "estimated": state.estimated,
        }
