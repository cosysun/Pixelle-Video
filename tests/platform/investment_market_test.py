from datetime import datetime, timezone

import httpx
import pytest

from api.schemas.investment import RecordRequest
from pixelle_video.investment.market import (
    MarketProvider,
    daily_timestamp,
    parse_tencent,
    quote_code,
    session_status,
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
