"""Replaceable public quote adapters; quote timestamps are never rewritten as live."""

import asyncio
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

import httpx

UTC = timezone.utc
SHANGHAI = ZoneInfo("Asia/Shanghai")
NEW_YORK = ZoneInfo("America/New_York")
CALENDARS = {"A": "XSHG", "FUND": "XSHG", "HK": "XHKG", "US": "XNYS"}
FX_CODES = {"HKD": "HKDCNH", "USD": "USDCNH"}


def positive(value):
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() and number > 0 else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def market_timezone(market):
    return NEW_YORK if market == "US" else SHANGHAI


def daily_timestamp(market, day):
    """Timestamp the real market session close, including half days when available."""
    day = date.fromisoformat(str(day)[:10])
    default = time(16) if market in {"US", "HK"} else time(15)
    if market in CALENDARS:
        try:
            calendar = trading_calendar(CALENDARS[market])
            if calendar.is_session(day.isoformat()):
                return calendar.session_close(day.isoformat()).to_pydatetime().isoformat()
        except (ValueError, KeyError, IndexError):
            pass
    return datetime.combine(day, default, market_timezone(market)).astimezone(UTC).isoformat()


@lru_cache(maxsize=3)
def trading_calendar(name):
    import exchange_calendars

    return exchange_calendars.get_calendar(name)


def session_status(market, at=None):
    at = at or datetime.now(UTC)
    if isinstance(at, str):
        at = datetime.fromisoformat(at.replace("Z", "+00:00"))
    if at.tzinfo is None:
        raise ValueError("行情时段必须使用带时区的时间")
    name = CALENDARS.get(market)
    if not name:
        return {"market": market, "state": "manual", "next_open": None}
    try:
        import pandas as pd

        calendar = trading_calendar(name)
        minute = pd.Timestamp(at).floor("min")
        local_day = at.astimezone(market_timezone(market)).date().isoformat()
        next_open = None
        if calendar.is_open_on_minute(minute, ignore_breaks=False):
            state = "open"
        elif calendar.is_session(local_day):
            opened = calendar.session_open(local_day)
            closed = calendar.session_close(local_day)
            state = "break" if opened <= minute < closed else "closed"
            if minute < opened:
                next_open = opened.isoformat()
            elif state == "break":
                next_open = calendar.session_break_end(local_day).isoformat()
        else:
            state = "closed"
        if next_open is None and state != "open":
            next_open = calendar.next_open(minute).isoformat()
        return {"market": market, "state": state, "next_open": next_open}
    except (ValueError, KeyError, IndexError, OverflowError):
        # Unknown calendar coverage must not silently turn a holiday into a weekday.
        return {
            "market": market,
            "state": "unknown",
            "next_open": None,
            "message": "交易日历超出覆盖范围，请更新行情日历依赖",
        }


def quote_code(instrument):
    market, symbol = instrument["market"], str(instrument["symbol"]).strip().upper()
    if market == "US":
        return "us" + symbol
    if market == "HK":
        return "hk" + symbol.zfill(5)
    if market in {"A", "FUND"}:
        symbol = symbol.zfill(6)
        prefix = (
            "bj"
            if symbol.startswith(("4", "8", "92"))
            else "sh"
            if symbol.startswith(("5", "6", "9"))
            else "sz"
        )
        return prefix + symbol
    return None


def parse_tencent(text, instruments):
    expected = {quote_code(item): item for item in instruments if quote_code(item)}
    rows = []
    for line in text.split(";"):
        key, separator, content = line.strip().partition("=")
        item = expected.get(key.removeprefix("v_"))
        if not separator or not item:
            continue
        values = content.strip().strip('"').split("~")
        if len(values) <= 30 or not positive(values[3]):
            continue
        stamp = values[30]
        parsed = None
        for fmt in ("%Y%m%d%H%M%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(stamp, fmt).replace(
                    tzinfo=market_timezone(item["market"])
                )
                break
            except ValueError:
                continue
        if parsed is None:
            continue
        rows.append(
            {
                "market": item["market"],
                "symbol": item["symbol"],
                "price": positive(values[3]),
                "previous_close": positive(values[4]),
                "occurred_at": parsed.astimezone(UTC).isoformat(),
                "market_date": parsed.date().isoformat(),
                "source": "tencent",
                "kind": "quote",
            }
        )
    return rows


class MarketProvider:
    """HTTP requests use bounded timeouts and remain separate from the video queue."""

    def __init__(self, client=None):
        self.client = client
        self._secids = {}
        self._semaphore = asyncio.Semaphore(6)

    async def _get(self, url, params=None, text=False):
        async with self._semaphore:
            client = self.client or httpx.AsyncClient(
                timeout=10, headers={"User-Agent": "Mozilla/5.0"}
            )
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                if text:
                    return response.content.decode("gb18030", errors="replace")
                return json.loads(response.content, parse_float=Decimal)
            finally:
                if self.client is None:
                    await client.aclose()

    def sessions(self, at=None):
        return [session_status(market, at) for market in ("A", "HK", "US")]

    async def quotes(self, instruments):
        supported = [item for item in instruments if quote_code(item)]
        result = []
        for offset in range(0, len(supported), 50):
            chunk = supported[offset : offset + 50]
            raw = await self._get(
                "https://qt.gtimg.cn/q=" + ",".join(quote_code(item) for item in chunk), text=True
            )
            result.extend(parse_tencent(raw, chunk))
        return result

    async def _secid(self, instrument):
        market, symbol = instrument["market"], str(instrument["symbol"]).upper()
        if market == "HK":
            return "116." + symbol.zfill(5)
        if market == "FX":
            return "133." + symbol
        if market in {"A", "FUND"}:
            prefix = "1" if quote_code(instrument).startswith("sh") else "0"
            return prefix + "." + symbol.zfill(6)
        if market != "US":
            return None
        if symbol in self._secids:
            return self._secids[symbol]
        payload = await self._get(
            "https://searchapi.eastmoney.com/api/suggest/get",
            {
                "input": symbol,
                "type": "14",
                "count": "10",
                "token": "D43BF722C8E33BDC906FB84D85E326E8",
            },
        )
        for item in (payload.get("QuotationCodeTable") or {}).get("Data") or []:
            if str(item.get("Code", "")).upper().replace("_", ".") == symbol:
                value = item.get("QuoteID")
                if value:
                    self._secids[symbol] = value
                    return value
        return None

    async def history(self, instrument, start, end):
        start, end = date.fromisoformat(str(start)[:10]), date.fromisoformat(str(end)[:10])
        secid = await self._secid(instrument)
        if not secid:
            return []
        result = []
        cursor = start
        while cursor <= end:
            last = min(cursor + timedelta(days=365), end)
            payload = await self._get(
                "https://push2his.eastmoney.com/api/qt/stock/kline/get",
                {
                    "secid": secid,
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                    "klt": "101",
                    "fqt": "0",
                    "beg": cursor.strftime("%Y%m%d"),
                    "end": last.strftime("%Y%m%d"),
                    "lmt": "2000",
                },
            )
            previous = None
            for line in (payload.get("data") or {}).get("klines") or []:
                fields = line.split(",")
                if len(fields) < 3 or not positive(fields[2]):
                    continue
                day = fields[0]
                result.append(
                    {
                        "market": instrument["market"],
                        "symbol": instrument["symbol"],
                        "price": positive(fields[2]),
                        "previous_close": previous,
                        "occurred_at": daily_timestamp(instrument["market"], day),
                        "market_date": day,
                        "source": "eastmoney",
                        "kind": "daily",
                    }
                )
                previous = positive(fields[2])
            cursor = last + timedelta(days=1)
        return result

    async def fx_rates(self):
        payload = await self._get(
            "https://push2.eastmoney.com/api/qt/ulist.np/get",
            {
                "secids": ",".join("133." + code for code in FX_CODES.values()),
                "fields": "f2,f12,f13,f14,f124",
                "fltt": "2",
                "invt": "2",
            },
        )
        result = []
        rows = (payload.get("data") or {}).get("diff") or []
        if isinstance(rows, dict):
            rows = rows.values()
        for item in rows:
            currency = next(
                (cur for cur, code in FX_CODES.items() if code == item.get("f12")), None
            )
            rate = positive(item.get("f2"))
            try:
                source_time = int(item["f124"])
                stamp = datetime.fromtimestamp(source_time, UTC).isoformat()
            except (ValueError, TypeError, KeyError, OverflowError, OSError):
                continue
            if currency and rate and source_time > 0:
                result.append(
                    {
                        "currency": currency,
                        "rate": rate,
                        "occurred_at": stamp,
                        "source": "eastmoney-reference",
                        "kind": "quote",
                    }
                )
        return result

    async def fx_history(self, currency, start, end):
        code = FX_CODES.get(currency)
        if not code:
            return []
        prices = await self.history({"market": "FX", "symbol": code}, start, end)
        # FX daily rates are only known after that date completes in China.
        return [
            {
                "currency": currency,
                "rate": item["price"],
                "occurred_at": datetime.combine(
                    date.fromisoformat(item["market_date"]), time(23, 59, 59), SHANGHAI
                )
                .astimezone(UTC)
                .isoformat(),
                "source": "eastmoney-reference",
                "kind": "daily",
            }
            for item in prices
        ]
