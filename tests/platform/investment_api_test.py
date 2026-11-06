"""Synthetic investment API integration tests; all market I/O stays in-process."""

import csv
import importlib
import io
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace

import httpx
import pytest
import requests
from fastapi.testclient import TestClient

from api.app import create_app
from pixelle_video.config import config_manager
from pixelle_video.config.schema import PixelleVideoConfig
from pixelle_video.investment.ledger import parse_time, timestamp
from pixelle_video.investment.market import MarketProvider
from pixelle_video.platform.database import Database
from pixelle_video.platform.runtime import Platform
from tests.platform.fakes import MemoryStorage

PREFIX = "/api/investment"
DEPOSIT_AT = "2026-11-04T01:00:00+00:00"
TRADE_AT = "2026-11-04T02:00:00+00:00"
NOW = datetime(2026, 11, 5, 2, 30, tzinfo=timezone.utc)


class FakeProvider:
    def __init__(self):
        self.rows = []
        self.calls = []

    def sessions(self, at=None):
        self.calls.append(("sessions", at))
        return [{"market": market, "state": "open"} for market in ("A", "HK", "US")]

    async def quotes(self, instruments):
        self.calls.append(("quotes", instruments))
        keys = {(item["market"], item["symbol"]) for item in instruments}
        return [dict(row) for row in self.rows if (row["market"], row["symbol"]) in keys]

    async def history(self, instrument, start, end):
        self.calls.append(("history", instrument, start, end))
        return []

    async def fx_rates(self):
        self.calls.append(("fx_rates",))
        return []

    async def fx_history(self, currency, start, end):
        self.calls.append(("fx_history", currency, start, end))
        return []


@pytest.fixture
def platform(tmp_path, monkeypatch):
    original_config, original_database = config_manager.config, config_manager.database
    config_manager.config = PixelleVideoConfig()
    monkeypatch.setenv("PIXELLE_USERNAME", "creator")
    monkeypatch.setenv("PIXELLE_PASSWORD", "test-password")
    monkeypatch.setenv("PIXELLE_COOKIE_SECURE", "false")
    monkeypatch.setenv("PIXELLE_TEMP_DIR", str(tmp_path / "processing"))
    monkeypatch.delenv("PIXELLE_PUBLIC_URL", raising=False)
    monkeypatch.delenv("PIXELLE_TRUSTED_PROXIES", raising=False)

    def forbid_network(*args, **kwargs):
        pytest.fail("Investment integration tests must not make external requests")

    monkeypatch.setattr(MarketProvider, "_get", forbid_network)
    monkeypatch.setattr(requests.sessions.Session, "request", forbid_network)
    monkeypatch.setattr(httpx.AsyncClient, "request", forbid_network)
    templates = importlib.import_module("pixelle_video.platform.builtin_templates")
    monkeypatch.setattr(templates, "template_catalog", lambda root: {})
    service_module = importlib.import_module("pixelle_video.investment.service")
    clock = SimpleNamespace(now=NOW)
    monkeypatch.setattr(
        service_module,
        "parse_time",
        lambda value=None: clock.now if value is None else parse_time(value),
    )
    database = Database(tmp_path / "investment.sqlite3")
    try:
        instance = Platform(database, MemoryStorage())
        instance.resources.builtin_templates = {}
        instance.investment.provider = FakeProvider()
        instance.test_clock = clock
        yield instance
    finally:
        database.close()
        config_manager.config = original_config
        config_manager.database = original_database


@pytest.fixture
def client(platform):
    with TestClient(create_app(platform)) as instance:
        response = instance.post(
            "/api/auth/login", json={"username": "creator", "password": "test-password"}
        )
        assert response.status_code == 200, response.text
        yield instance


def success(response):
    assert response.status_code == 200, (
        f"{response.request.url}: {response.status_code} {response.text}"
    )
    return response.json()


def trade(**changes):
    return {
        "action": "buy",
        "occurred_at": TRADE_AT,
        "market": "A",
        "symbol": "600000",
        "name": "Synthetic equity",
        "currency": "CNY",
        "quantity": "10",
        "price": "10",
        "fee": "1",
        **changes,
    }


def create(client, body):
    return success(client.post(PREFIX + "/records", json=body))


def seed(client, **changes):
    deposit = create(
        client, {"action": "deposit", "occurred_at": DEPOSIT_AT, "market": "CASH", "amount": "1000"}
    )
    return deposit, create(client, trade(**changes))


def quote(price="12", occurred_at="2026-11-04T02:30:00+00:00", **changes):
    return {
        "market": "A",
        "symbol": "600000",
        "price": price,
        "previous_close": "10",
        "occurred_at": occurred_at,
        "market_date": "2026-11-04",
        "source": "synthetic",
        "kind": "quote",
        **changes,
    }


async def settle_refresh(engine):
    await engine._manual_refresh


def refresh(client, platform):
    result = success(client.post(PREFIX + "/refresh"))
    assert result == {"status": "refreshing"}
    client.portal.call(settle_refresh, platform.investment)


def database_state(platform):
    """Capture records, audit and actual samples to check atomic rejection."""
    return {
        table: platform.database.all(f"SELECT * FROM {table} ORDER BY rowid")
        for table in ("investment_records", "investment_revisions", "investment_snapshots")
    }


def inspect_csv(client, rows, columns=None):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns or ["日期", "动作", "市场", "代码", "数量", "价格", "流水号", "备注"])
    writer.writerows(rows)
    response = client.post(
        PREFIX + "/imports/inspect",
        files={"file": ("synthetic.csv", stream.getvalue().encode("utf-8-sig"), "text/csv")},
    )
    return success(response)


def preview(client, inspection, **changes):
    return success(
        client.post(
            PREFIX + "/imports/preview",
            json={
                "id": inspection["id"],
                "sheet": inspection["sheets"][0]["name"],
                "mapping": {},
                **changes,
            },
        )
    )


def confirm(client, inspection, indices):
    return client.post(
        PREFIX + "/imports/confirm", json={"id": inspection["id"], "indices": indices}
    )


def test_every_investment_endpoint_requires_login(platform):
    app = create_app(platform)
    with TestClient(app) as anonymous:
        routes = [route for route in app.routes if route.path.startswith(PREFIX + "/")]
        assert len(routes) >= 13
        for route in routes:
            path = route.path.replace("{record_id}", "missing")
            for method in route.methods:
                if method in {"HEAD", "OPTIONS"}:
                    continue
                kwargs = {}
                if path.endswith("/inspect"):
                    kwargs["files"] = {"file": ("synthetic.csv", b"a\nb\n", "text/csv")}
                elif method in {"POST", "PATCH"}:
                    kwargs["json"] = {}
                response = anonymous.request(method, path, **kwargs)
                assert response.status_code == 401, f"{method} {path}: {response.text}"
                assert response.headers["cache-control"] == "no-store"
        assert not platform.investment.repository.list_records()
        assert not platform.investment.provider.calls


def test_decimal_crud_refresh_holdings_and_snapshot_remain_exact(client, platform):
    amount, quantity, price, fee, current_price = map(
        Decimal,
        (
            "100.000000000000000001",
            "0.123456789012345678",
            "0.200000000000000003",
            "0.000000000000000001",
            "0.300000000000000007",
        ),
    )
    deposit = create(
        client,
        {"action": "deposit", "occurred_at": DEPOSIT_AT, "market": "CASH", "amount": str(amount)},
    )
    record = create(client, trade(quantity=str(quantity), price=str(price), fee=str(fee)))
    assert deposit["amount"] == str(amount)
    assert record["quantity"] == str(quantity)
    assert record["price"] == str(price)
    assert record["fee"] == format(fee, "f")
    platform.investment.provider.rows = [quote(str(current_price))]
    refresh(client, platform)
    positions = success(client.get(PREFIX + "/positions"))["items"]
    overview = success(client.get(PREFIX + "/overview"))
    assert len(positions) == 1
    with localcontext() as context:
        context.prec = 100
        cost = quantity * price + fee
        market_value = quantity * current_price
        expected_cash = amount - cost
        assert Decimal(record["amount"]) == quantity * price
        for key, expected in {
            "quantity": quantity,
            "cost": cost,
            "market_value": market_value,
        }.items():
            assert isinstance(positions[0][key], str)
            assert Decimal(positions[0][key]) == expected
        for key, expected in {
            "cash": expected_cash,
            "net_invested": amount,
            "total_assets": expected_cash + market_value,
            "cumulative_pnl": market_value - cost,
        }.items():
            assert isinstance(overview[key], str)
            assert Decimal(overview[key]) == expected
    assert overview["complete"] is True
    assert positions[0]["source"] == "synthetic"
    assert {item["id"] for item in success(client.get(PREFIX + "/records"))["items"]} == {
        deposit["id"],
        record["id"],
    }
    sample = platform.investment.repository.snapshots(kind="minute")[-1]
    assert sample["occurred_at"] == timestamp(NOW)
    assert sample["payload"]["total_assets"] == overview["total_assets"]
    assert any(call[0] == "quotes" for call in platform.investment.provider.calls)


def test_date_only_is_shanghai_end_of_day_and_not_active_early(client, platform):
    record = create(
        client,
        {
            "action": "deposit",
            "market": "CASH",
            "amount": "13.25",
            "occurred_at": "2026-11-05T00:00:00+08:00",
            "date_only": True,
        },
    )
    effective = parse_time(record["occurred_at"])
    assert effective.astimezone(timezone(timedelta(hours=8))).date().isoformat() == "2026-11-05"
    assert effective.hour == 15 and effective.minute == 59 and effective.second == 59
    assert record["date_only"] is True
    assert success(client.get(PREFIX + "/overview"))["net_invested"] == "0"
    platform.test_clock.now = effective + timedelta(microseconds=1)
    assert success(client.get(PREFIX + "/overview"))["net_invested"] == "13.25"


def test_record_schema_rejects_unsupported_currency_and_invalid_decimals(client, platform):
    before = database_state(platform)
    invalid = [
        {"currency": "EUR"},
        {"settlement_currency": "JPY"},
        {"currency": "USD"},
        {"quantity": 0.125},
        {"price": 1.25},
        {"amount": 12.5},
        {"fee": 0.1},
        {"settlement_amount": 10.5},
        {"settlement_fee": 0.1},
        {"fx_rate": 1.1},
        {"quantity": True},
        {"quantity": "NaN"},
        {"price": "Infinity"},
        {"fee": "-1"},
        {"price": "1e19"},
        {"occurred_at": "2026-11-04T10:00:00"},
    ]
    for changes in invalid:
        response = client.post(PREFIX + "/records", json=trade(**changes))
        assert response.status_code == 422, f"{changes}: {response.text}"
    assert database_state(platform) == before


def test_oversell_create_update_and_void_roll_back_ledger_audit_and_samples(client, platform):
    _, buy = seed(client)
    repository = platform.investment.repository
    repository.add_price(quote())
    observed = NOW - timedelta(minutes=2)
    repository.save_snapshot(observed, "minute", platform.investment.overview(observed))
    before = database_state(platform)
    response = client.post(
        PREFIX + "/records",
        json=trade(action="sell", quantity="11", occurred_at="2026-11-04T03:00:00+00:00"),
    )
    assert response.status_code == 400, response.text
    assert database_state(platform) == before
    sell = create(
        client, trade(action="sell", quantity="8", occurred_at="2026-11-04T03:00:00+00:00")
    )
    before = database_state(platform)
    rejected = [
        client.patch(
            PREFIX + "/records/" + sell["id"],
            json=trade(action="sell", quantity="11", occurred_at=sell["occurred_at"]),
        ),
        client.patch(PREFIX + "/records/" + buy["id"], json=trade(quantity="7")),
        client.delete(PREFIX + "/records/" + buy["id"]),
    ]
    for response in rejected:
        assert response.status_code == 400, response.text
    assert database_state(platform) == before


def test_update_void_preserve_revisions_and_revalue_only_actual_minute_times(client, platform):
    _, buy = seed(client)
    repository = platform.investment.repository
    repository.add_price(quote(occurred_at=NOW - timedelta(minutes=13)))
    repository.add_price(quote(occurred_at=NOW - timedelta(minutes=3)))
    times = [NOW - timedelta(minutes=12), NOW - timedelta(minutes=2)]
    for observed in times:
        repository.save_snapshot(observed, "minute", platform.investment.overview(observed))
    edited = success(
        client.patch(PREFIX + "/records/" + buy["id"], json=trade(quantity="5", note="corrected"))
    )
    assert edited["id"] == buy["id"] and edited["quantity"] == "5"
    samples = repository.snapshots(kind="minute")
    assert [row["occurred_at"] for row in samples] == list(map(timestamp, times))
    assert [row["payload"]["total_assets"] for row in samples] == ["1009", "1009"]
    curve = success(client.get(PREFIX + "/curve?period=today"))
    assert [point["occurred_at"] for point in curve["points"]] == list(map(timestamp, times))
    assert curve["points"][1]["break_before"] is True
    assert len(curve["segments"]) == 2
    assert success(client.delete(PREFIX + "/records/" + buy["id"]))["voided"] is True
    listed = success(client.get(PREFIX + "/records"))["items"]
    assert next(row for row in listed if row["id"] == buy["id"])["voided"] is True
    assert success(client.get(PREFIX + "/positions"))["items"] == []
    assert repository.get_record(buy["id"])["voided"] is True
    revisions = success(client.get(PREFIX + "/records/" + buy["id"] + "/revisions"))["items"]
    assert [row["operation"] for row in revisions] == ["create", "update", "void"]
    assert [row["payload"]["quantity"] for row in revisions] == ["10", "5", "5"]
    assert [row["payload"]["voided"] for row in revisions] == [False, False, True]
    assert revisions[1]["payload"]["note"] == "corrected"
    assert all(parse_time(row["occurred_at"]).tzinfo for row in revisions)
    assert [row["payload"]["total_assets"] for row in repository.snapshots(kind="minute")] == [
        "1000",
        "1000",
    ]
    success(client.delete(PREFIX + "/records/" + buy["id"]))
    assert len(repository.revisions(buy["id"])) == 3


def test_csv_inspect_preview_confirm_is_explicit_idempotent_and_deduplicates_external_id(
    client, platform
):
    rows = [["2026-11-04", "买入", "A股", "600000", "2.5", "10.125", "SYNTH-1", "original"]]
    inspection = inspect_csv(client, rows)
    assert inspection["duplicate_file"] is False
    assert inspection["sheets"][0]["columns"][0] == "日期"
    assert inspection["sheets"][0]["rows"][0]["代码"] == "600000"
    assert not platform.investment.repository.list_records()
    candidate = preview(client, inspection)["rows"][0]
    assert candidate["index"] == 2 and not candidate["errors"]
    assert candidate["record"]["action"] == "buy"
    assert candidate["record"]["quantity"] == "2.5"
    assert candidate["record"]["date_only"] is True
    assert not platform.investment.repository.list_records()
    first = success(confirm(client, inspection, [2]))
    assert first["inserted"] == 1
    before = database_state(platform)
    retry = success(confirm(client, inspection, [2]))
    assert retry["inserted"] == 0 and retry["already_confirmed"] == [2]
    assert retry["record_ids"] == first["record_ids"]
    assert database_state(platform) == before
    repeated = inspect_csv(client, rows)
    assert repeated["id"] == inspection["id"] and repeated["duplicate_file"] is True
    other_file = inspect_csv(client, [rows[0][:-1] + ["different file comment"]])
    assert other_file["id"] != inspection["id"]
    duplicate = preview(client, other_file)["rows"][0]
    assert duplicate["external_duplicate"] is True and not duplicate["errors"]
    result = success(confirm(client, other_file, [2]))
    assert result["inserted"] == 0 and result["skipped_external_duplicates"] == [2]
    assert len(platform.investment.repository.list_records()) == 1
    record = platform.investment.repository.get_record(first["record_ids"][0])
    assert record["provenance"]["raw"]["流水号"] == "SYNTH-1"
    assert len(platform.investment.repository.revisions(record["id"])) == 1


def test_import_conflicts_and_invalid_selection_fail_without_partial_writes(client, platform):
    first = inspect_csv(client, [["2026-11-04", "买入", "A股", "600000", "2", "10", "SYNTH-1", ""]])
    preview(client, first)
    success(confirm(client, first, [2]))
    inspection = inspect_csv(
        client,
        [
            ["2026-11-04", "买入", "A股", "600000", "1", "10", "SYNTH-2", "valid"],
            ["2026-11-04", "买入", "A股", "600000", "3", "10", "SYNTH-1", "conflict"],
            ["2026-11-04", "买入", "A股", "600000", "1", "NaN", "SYNTH-3", "invalid"],
        ],
    )
    candidates = preview(client, inspection)["rows"]
    assert not candidates[0]["errors"]
    assert any("conflict" in error for error in candidates[1]["errors"])
    assert candidates[2]["errors"]
    before = database_state(platform)
    for indices in ([2, 3], [2, 4], [2, 999]):
        response = confirm(client, inspection, indices)
        assert response.status_code == 400, response.text
        assert database_state(platform) == before
        assert platform.investment.repository.get_import(inspection["id"])["confirmed"] is False
    pending = inspect_csv(
        client,
        [["2026-11-04T04:00:00+00:00", "买入", "A股", "600000", "1", "10", "SYNTH-RACE", ""]],
    )
    assert not preview(client, pending)["rows"][0]["errors"]
    create(
        client,
        trade(
            quantity="2", fee="0", external_id="SYNTH-RACE", occurred_at="2026-11-04T04:00:00+00:00"
        ),
    )
    before = database_state(platform)
    response = confirm(client, pending, [2])
    assert response.status_code == 400 and "conflict" in response.json()["detail"]
    assert database_state(platform) == before


def test_missing_quote_keeps_portfolio_and_curve_values_null(client, platform):
    seed(client)
    overview = success(client.get(PREFIX + "/overview"))
    position = success(client.get(PREFIX + "/positions"))["items"][0]
    assert position["quantity"] == "10"
    assert position["price"] is None and position["market_value"] is None
    assert overview["total_assets"] is None and overview["cumulative_pnl"] is None
    assert overview["cumulative_return"] is None
    assert overview["complete"] is False
    assert any(warning["code"] == "missing_price" for warning in overview["warnings"])
    refresh(client, platform)
    points = success(client.get(PREFIX + "/curve?period=today"))["points"]
    assert len(points) == 1 and points[0]["total_assets"] is None
    assert points[0]["complete"] is False
    assert points[0]["cumulative_return"] is None


def test_curve_derives_returns_from_each_legacy_snapshot_without_rewriting_it(client, platform):
    repository = platform.investment.repository
    payloads = [
        {"total": "1100", "cumulative_pnl": "100", "net_invested": "1000"},
        {"total": "2100", "cumulative_pnl": "100", "net_invested": "2000"},
        {"total": "100", "cumulative_pnl": "100", "net_invested": "0"},
        {"total": "100", "cumulative_pnl": "100", "net_invested": "-1000"},
        {"total": "100", "cumulative_pnl": "100"},
        {"total": None, "cumulative_pnl": None, "net_invested": "1000"},
        {"total": "1900", "cumulative_pnl": "-100", "net_invested": "2000"},
    ]
    for index, payload in enumerate(payloads):
        repository.save_snapshot(NOW - timedelta(minutes=8 - index), "minute", payload)
        repository.save_snapshot(NOW - timedelta(days=8 - index), "daily", payload)
    before = repository.snapshots()
    for period in ("today", "all"):
        points = success(client.get(PREFIX + "/curve", params={"period": period}))["points"]
        expected = [
            "10",
            "5",
            None,
            None,
            None,
            None,
            "-5",
        ]
        if period == "all":
            expected.append("-5")
            assert points[-1]["kind"] == "latest_sample" and points[-1]["break_before"]
        assert [point["cumulative_return"] for point in points] == expected
    assert repository.snapshots() == before


def test_other_manual_valuation_uses_holdings_at_valuation_time_and_never_future_prices(
    client, platform
):
    seed(client, market="OTHER", symbol="SYNTH", quantity="4", price="10", fee="0")
    valued_at = "2026-11-04T03:00:00+00:00"
    success(
        client.post(
            PREFIX + "/valuation",
            json={
                "market": "OTHER",
                "symbol": "SYNTH",
                "occurred_at": valued_at,
                "total_amount": "50.000000000000000004",
            },
        )
    )
    position = success(client.get(PREFIX + "/positions"))["items"][0]
    assert position["price"] == "12.500000000000000001"
    assert position["market_value"] == "50.000000000000000004"
    assert position["source"] == "manual" and parse_time(position["price_at"]) == parse_time(
        valued_at
    )
    assert platform.investment.positions("2026-11-04T02:30:00+00:00")["items"][0]["price"] is None
    repository = platform.investment.repository
    repository.add_price(
        quote(
            "999",
            occurred_at="2026-11-06T02:00:00+00:00",
            market="OTHER",
            symbol="SYNTH",
            kind="manual",
        )
    )
    assert success(client.get(PREFIX + "/positions"))["items"][0]["price"] == position["price"]
    before = repository.prices()
    for changes in (
        {"total_amount": "0"},
        {"total_amount": "NaN"},
        {"total_amount": "-1"},
        {"total_amount": 1.5},
    ):
        response = client.post(
            PREFIX + "/valuation",
            json={"market": "OTHER", "symbol": "SYNTH", "occurred_at": valued_at, **changes},
        )
        assert response.status_code == 422, response.text
    for symbol, occurred in (("MISSING", valued_at), ("SYNTH", DEPOSIT_AT)):
        response = client.post(
            PREFIX + "/valuation",
            json={
                "market": "OTHER",
                "symbol": symbol,
                "occurred_at": occurred,
                "total_amount": "50",
            },
        )
        assert response.status_code == 400, response.text
        assert "持仓" in response.json()["detail"]
    assert repository.prices() == before


def test_other_total_valuation_accepts_eighty_digit_thirds_and_verifies_legacy_estimate(
    client, platform
):
    _, record = seed(
        client, market="OTHER", symbol="SYNTH", quantity="3", price="1", fee="0", estimated=True
    )
    repository = platform.investment.repository
    repository.add_price(
        quote("0.5", market="OTHER", symbol="SYNTH", source="legacy", kind="manual")
    )
    legacy = success(client.get(PREFIX + "/positions"))["items"][0]
    assert legacy["source"] == "legacy"
    assert legacy["estimated"] is True and legacy["valuation_verified"] is False

    valued_at = "2026-11-04T03:00:00+00:00"
    result = success(
        client.post(
            PREFIX + "/valuation",
            json={
                "market": "OTHER",
                "symbol": "SYNTH",
                "occurred_at": valued_at,
                "total_amount": "1",
            },
        )
    )
    assert result == {"success": True}
    position = success(client.get(PREFIX + "/positions"))["items"][0]
    stored = repository.latest_price("OTHER", "SYNTH", valued_at)
    assert isinstance(position["price"], str)
    assert position["price"] == stored["price"] == "0." + "3" * 80
    assert len(Decimal(stored["price"]).as_tuple().digits) == 80
    with localcontext() as context:
        context.prec = 100
        expected_value = Decimal(position["price"]) * Decimal("3")
        assert Decimal(position["market_value"]) == expected_value
        assert abs(expected_value - Decimal("1")) <= Decimal("1e-79")
    assert position["source"] == "manual" and stored["kind"] == "manual"
    assert position["estimated"] is False and position["valuation_verified"] is True
    assert position["cost_estimated"] is True
    assert repository.get_record(record["id"])["estimated"] is True
    assert repository.revisions(record["id"])[0]["payload"]["estimated"] is True


def test_all_documented_curve_periods_are_supported(client):
    failures = {}
    for period in ("today", "week", "month", "quarter", "all"):
        response = client.get(PREFIX + "/curve", params={"period": period})
        if response.status_code != 200:
            failures[period] = (response.status_code, response.json())
        else:
            assert isinstance(response.json()["points"], list)
    assert not failures, failures
    assert client.get(PREFIX + "/curve?period=invalid").status_code == 400


def test_investment_deep_link_serves_html_while_data_stays_protected(
    platform, tmp_path, monkeypatch
):
    module = importlib.import_module("api.app")
    dist = tmp_path / "frontend" / "dist"
    dist.mkdir(parents=True)
    html = "<!doctype html><html><body><div id='root'>synthetic frontend</div></body></html>"
    (dist / "index.html").write_text(html, encoding="utf-8")
    monkeypatch.setattr(module, "ROOT", tmp_path)
    with TestClient(create_app(platform)) as browser:
        response = browser.get("/investment")
        assert response.status_code == 200 and response.text == html
        assert response.headers["content-type"].startswith("text/html")
        assert browser.get(PREFIX + "/overview").status_code == 401
        success(
            browser.post(
                "/api/auth/login", json={"username": "creator", "password": "test-password"}
            )
        )
        assert browser.get("/investment").text == html
        assert browser.get(PREFIX + "/overview").status_code == 200
        success(browser.post("/api/auth/logout"))
        assert browser.get(PREFIX + "/overview").status_code == 401
