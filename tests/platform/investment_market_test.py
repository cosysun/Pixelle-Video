from datetime import datetime, timezone

import httpx
import pytest

from api.schemas.investment import RecordRequest
from pixelle_video.investment.market import (
    MarketProvider,
    daily_timestamp,
    parse_tencent,
    portfolio_trading_day,
    quote_code,
    session_status,
    trading_windows,
)


def synthetic_quote(code, price="12.34", stamp="20261105103000", previous="12"):
    fields = [""] * 40
    fields[3], fields[4], fields[30] = price, previous, stamp
    return f'v_{code}="' + "~".join(fields) + '";'


def test_tencent_uses_market_source_time_and_handles_missing_rows():
    instruments = [{"market": "A", "symbol": "600000"}, {"market": "US", "symbol": "BRK.B"}]
    text = synthetic_quote("sh600000") + synthetic_quote("usBRK.B", stamp="2026-11-04 16:00:01")
    parsed = parse_tencent(text, instruments)
    assert parsed[0]["occurred_at"] == "2026-11-05T02:30:00+00:00"
    assert parsed[1]["occurred_at"] == "2026-11-04T21:00:01+00:00"
    assert parsed[1]["market_date"] == "2026-11-04"
    assert parse_tencent(synthetic_quote("sh600000", price="0"), instruments) == []
    assert parse_tencent(synthetic_quote("sh600000", stamp=""), instruments) == []
    assert quote_code({"market": "HK", "symbol": "700"}) == "hk00700"
    assert quote_code({"market": "A", "symbol": "920000"}) == "bj920000"


def test_calendars_break_holiday_half_day_and_daylight_savings():
    def utc(value):
        return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)

    assert session_status("A", utc("2026-11-05T04:00:00"))["state"] == "break"
    assert session_status("HK", utc("2026-11-05T04:30:00"))["state"] == "break"
    assert session_status("A", utc("2026-10-01T02:00:00"))["state"] == "closed"
    assert session_status("US", utc("2026-07-02T13:35:00"))["state"] == "open"
    assert session_status("US", utc("2026-11-05T13:35:00"))["state"] == "closed"
    assert session_status("US", utc("2026-11-05T14:35:00"))["state"] == "open"
    assert session_status("US", utc("2026-11-27T19:00:00"))["state"] == "closed"
    assert session_status("US", utc("2026-11-27T19:00:00"))["close_at"].startswith(
        "2026-11-27T18:00:00"
    )
    assert daily_timestamp("US", "2026-11-27").startswith("2026-11-27T18:00:00")


@pytest.mark.asyncio
async def test_history_is_unadjusted_and_fx_is_not_known_before_day_end():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"data": {"klines": ["2026-11-04,7.0,7.01,7.1"]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = MarketProvider(client)
        rows = await provider.fx_history("USD", "2026-11-04", "2026-11-04")
    assert seen[0].url.params["fqt"] == "0"
    assert rows[0]["rate"] == "7.01"
    assert rows[0]["occurred_at"] == "2026-11-04T15:59:59+00:00"


def test_record_api_precision_validation():
    row = RecordRequest(
        action="buy",
        occurred_at="2026-11-05T02:00:00Z",
        symbol="00700",
        market="HK",
        currency="HKD",
        quantity="100",
        price="400.123456789",
    )
    assert row.price == "400.123456789"
    with pytest.raises(ValueError):
        RecordRequest(action="deposit", occurred_at="2026-11-05T02:00:00Z", amount="NaN")
    with pytest.raises(ValueError):
        RecordRequest(
            action="exchange",
            occurred_at="2026-11-05T02:00:00Z",
            currency="USD",
            settlement_currency="USD",
            amount="100",
            settlement_amount="100",
        )


@pytest.mark.asyncio
async def test_reference_fx_retains_source_timestamp_and_rejects_missing_time():
    def handle(request):
        assert "f124" in request.url.params["fields"]
        return httpx.Response(
            200,
            json={
                "data": {
                    "diff": {
                        "0": {"f12": "HKDCNH", "f2": "0.9", "f124": 1793786400},
                        "1": {"f12": "USDCNH", "f2": "7"},
                    }
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        rows = await MarketProvider(client).fx_rates()
    assert len(rows) == 1
    assert rows[0]["currency"] == "HKD"
    assert rows[0]["occurred_at"] == datetime.fromtimestamp(1793786400, timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_berkshire_history_resolves_provider_share_class_and_keeps_ledger_symbol():
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.host == "searchapi.eastmoney.com":
            assert request.url.params["input"] == "BRK_B"
            return httpx.Response(
                200,
                json={"QuotationCodeTable": {"Data": [{"Code": "BRK_B", "QuoteID": "106.BRK_B"}]}},
            )
        assert request.url.params["secid"] == "106.BRK_B"
        return httpx.Response(200, json={"data": {"klines": ["2026-11-05,100,110,120"]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        rows = await MarketProvider(client).history(
            {"market": "US", "symbol": "BRK.B"}, "2026-11-05", "2026-11-05"
        )
    assert len(seen) == 2
    assert rows[0]["symbol"] == "BRK.B" and rows[0]["price"] == "110"


@pytest.mark.asyncio
async def test_unresolved_supported_history_symbol_cannot_silently_complete_refresh():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"QuotationCodeTable": {"Data": []}})
        )
    ) as client:
        with pytest.raises(ValueError, match="历史行情代码"):
            await MarketProvider(client).history(
                {"market": "US", "symbol": "SYNTH"}, "2026-11-05", "2026-11-05"
            )


@pytest.mark.parametrize(
    "at,day",
    [
        ("2026-11-05T15:00:00+00:00", "2026-11-05"),
        ("2026-11-05T17:00:00+00:00", "2026-11-05"),
        ("2026-11-06T01:29:00+00:00", "2026-11-05"),
        ("2026-11-06T01:30:00+00:00", "2026-11-06"),
        ("2026-11-08T02:00:00+00:00", "2026-11-06"),
        ("2026-07-03T17:00:00+00:00", "2026-07-03"),
        ("2026-10-01T13:29:00+00:00", "2026-09-30"),
        ("2026-10-01T13:30:00+00:00", "2026-10-01"),
    ],
)
def test_portfolio_trading_day_rolls_only_at_market_open(at, day):
    trading = portfolio_trading_day(datetime.fromisoformat(at))
    assert trading["date"] == day
    assert trading["previous_date"] < day
    assert trading["baseline_at"] <= datetime.fromisoformat(at)


def test_trading_windows_compress_lunch_and_follow_dst_holidays_half_days():
    windows = trading_windows("2026-11-05")
    assert [(w["start"][11:16], w["end"][11:16]) for w in windows] == [
        ("01:30", "04:00"),
        ("05:00", "08:00"),
        ("14:30", "21:00"),
    ]
    assert trading_windows("2026-07-02", ["US"])[0]["start"][11:16] == "13:30"
    assert trading_windows("2026-11-27", ["US"])[0]["end"][11:16] == "18:00"
    assert trading_windows("2026-11-08") == []


@pytest.mark.parametrize(
    "market,source,at,price_at,stale",
    [
        ("HK", "tencent", "02:30:40", "02:15:33", False),
        ("HK", "tencent", "02:30:40", "02:09:33", True),
        ("HK", "other", "02:30:40", "02:15:33", True),
        ("A", "tencent", "02:30:40", "02:15:33", True),
        ("A", "tencent", "02:30:40", "02:30:33", False),
        ("HK", "tencent", "05:00:40", "03:59:59", False),
        ("HK", "tencent", "05:20:40", "03:59:59", True),
        ("HK", "tencent", "08:15:40", "07:59:59", False),
        ("HK", "tencent", "02:30:40", "02:31:00", True),
        ("HK", "tencent", "02:30:40", None, True),
    ],
)
def test_quote_freshness_accounts_for_source_delay_and_lunch(market, source, at, price_at, stale):
    from pixelle_video.investment.market import quote_is_stale

    item = {
        "market": market,
        "source": source,
        "price_at": f"2026-11-06T{price_at}+00:00" if price_at else None,
    }
    assert quote_is_stale(item, datetime.fromisoformat(f"2026-11-06T{at}+00:00")) is stale


def test_public_provider_declares_hk_delay_without_changing_session_hours():
    rows = MarketProvider().sessions(datetime.fromisoformat("2026-11-06T08:05:00+00:00"))
    hk = next(row for row in rows if row["market"] == "HK")
    assert hk["state"] == "closed" and hk["quote_delay_seconds"] == 900
    assert hk["close_at"].startswith("2026-11-06T08:00:00")
    assert all(row["quote_delay_seconds"] == 0 for row in rows if row["market"] != "HK")
