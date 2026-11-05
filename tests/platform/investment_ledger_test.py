"""Deterministic ledger/storage/service tests; providers never make HTTP calls."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from pixelle_video.investment import service as service_module
from pixelle_video.investment.ledger import (
    UTC,
    Ledger,
    LedgerError,
    cumulative_return_percentage,
    decimal_string,
    normalize_record,
    parse_time,
    timestamp,
)
from pixelle_video.investment.repository import InvestmentRepository, initialize
from pixelle_video.investment.service import InvestmentService
from pixelle_video.platform.database import Database

DAY = "2026-11-03T08:00:00Z"
NOW = datetime(2026, 11, 5, 2, tzinfo=UTC)


def record(action, **values):
    return {
        "action": action,
        "market": "A",
        "symbol": "600000",
        "currency": "CNY",
        "occurred_at": DAY,
        **values,
    }


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "investments.sqlite3")
    yield db
    db.close()


class FakeProvider:
    def __init__(self, *, state="open", fail_history=False):
        self.state = state
        self.fail_history = fail_history
        self.calls = []

    def sessions(self, at):
        assert at.tzinfo is not None
        self.calls.append("sessions")
        return [{"market": "A", "state": self.state, "next_open": None}]

    async def quotes(self, instruments):
        self.calls.append("quotes")
        return [
            {
                **item,
                "price": "12",
                "previous_close": "11",
                "occurred_at": NOW.isoformat(),
                "source": "fake",
                "market_date": "2026-11-05",
            }
            for item in instruments
        ]

    async def fx_rates(self):
        self.calls.append("fx_rates")
        return [
            {"currency": "USD", "rate": "7.2", "occurred_at": NOW.isoformat(), "source": "fake"}
        ]

    async def history(self, instrument, start, end):
        self.calls.append("history:" + instrument["symbol"])
        if self.fail_history and instrument["symbol"] == "600000":
            raise RuntimeError("fake endpoint failure")
        return [
            {
                **instrument,
                "price": "11",
                "previous_close": "10",
                "occurred_at": DAY,
                "market_date": "2026-11-03",
                "source": "fake",
                "kind": "daily",
            },
            {
                **instrument,
                "price": "999",
                "occurred_at": "2026-11-06T08:00:00Z",
                "source": "future",
            },
        ]

    async def fx_history(self, currency, start, end):
        self.calls.append("fx_history")
        return [{"currency": currency, "rate": "7", "occurred_at": DAY, "source": "fake"}]


@pytest.fixture
def engine(database, monkeypatch):
    monkeypatch.setattr(
        service_module, "parse_time", lambda value=None: NOW if value is None else parse_time(value)
    )
    return InvestmentService(database, FakeProvider())


@pytest.mark.parametrize("value", [0.1, True, "NaN", "Infinity", "-1"])
def test_invalid_decimal_input_is_rejected(value):
    with pytest.raises(LedgerError):
        normalize_record(record("deposit", amount=value))


def test_decimal_date_and_cash_schema_normalization():
    value = normalize_record(
        record(
            "buy",
            quantity="0.1000",
            price="0.20",
            amount="0",
            occurred_at="2026-11-03",
            date_only=True,
        )
    )
    assert value["amount"] == "0.02"
    assert value["quantity"] == "0.1"
    assert value["occurred_at"] == "2026-11-03T15:59:59.999999+00:00"
    assert decimal_string(Decimal("-0.000")) == "0"
    assert normalize_record(record("deposit", market="CASH", amount="1"))["market"] == "CASH"
    with pytest.raises(LedgerError):
        normalize_record(record("buy", market="CASH", quantity="1", price="1"))


def test_opening_cash_capital_and_positions_do_not_double_count(engine):
    engine.create_record(record("opening_cash", amount="1000"))
    engine.create_record(record("opening_capital", amount="500"))
    engine.create_record(record("opening_position", quantity="10", price="50"))
    engine.repository.add_price(
        {"market": "A", "symbol": "600000", "price": "60", "occurred_at": DAY}
    )
    value = engine.overview(NOW)
    assert value["cash"] == "1000"
    assert value["net_invested"] == "1500"
    assert value["total"] == "1600"
    assert value["cumulative_pnl"] == "100"
    assert value["items"][0]["pnl"] == "100"


@pytest.mark.parametrize(
    ("pnl", "capital", "expected"),
    [
        ("100", "1000", "10"),
        ("-25", "1000", "-2.5"),
        ("0", "1000", "0"),
        ("0.000000000000000001", "0.000000000000000003", None),
        ("100", "0", None),
        ("100", "-1000", None),
        (None, "1000", None),
        ("100", None, None),
    ],
)
def test_cumulative_return_decimal_ratio_and_undefined_denominators(pnl, capital, expected):
    result = cumulative_return_percentage(pnl, capital)
    if pnl == "0.000000000000000001":
        assert result.startswith("33.333333333333333333333333333333")
        assert isinstance(result, str)
    else:
        assert result == expected


def test_cumulative_return_uses_current_net_contributions_without_profit_from_deposits(engine):
    engine.create_record(record("opening_cash", amount="1000"))
    engine.create_record(record("dividend", amount="100"))
    assert engine.overview(NOW)["cumulative_return"] == "10"
    engine.create_record(record("deposit", amount="1000"))
    overview = engine.overview(NOW)
    assert overview["cumulative_pnl"] == "100"
    assert overview["cumulative_return"] == "5"
    engine.create_record(record("fee", amount="200"))
    assert engine.overview(NOW)["cumulative_return"] == "-5"
    engine.create_record(record("withdraw", amount="2000"))
    assert engine.overview(NOW)["cumulative_return"] is None


def test_moving_average_sell_split_dividend_and_fee():
    records = [
        record("opening_cash", amount="1000"),
        record("buy", quantity="10", price="10", fee="1"),
        record("buy", quantity="10", price="20", fee="1"),
        record("sell", quantity="5", price="30", fee="2"),
        record("split", quantity="2"),
        record("dividend", amount="10", fee="1"),
        record("fee", amount="3"),
    ]
    state = Ledger().replay(records, NOW)
    holding = state.holdings[("A", "600000")]
    assert holding.quantity == Decimal("30")
    assert holding.cost == Decimal("226.5")
    assert holding.realized_pnl == Decimal("72.5")
    assert state.realized_pnl == Decimal("78.5")
    assert state.cash["CNY"] == Decimal("852")
    assert state.net_invested == Decimal("1000")


def test_hk_conversion_estimation_gross_settlement_and_unknown_fx():
    trade = record(
        "buy", market="HK", symbol="00700", currency="HKD", quantity="10", price="100", fee="10"
    )
    converted = Ledger(lambda currency, at: "0.9").replay([trade], NOW)
    assert converted.cash["CNY"] == Decimal("-909")
    assert converted.holdings[("HK", "00700")].cost == Decimal("909")
    assert converted.estimated
    explicit = {**trade, "settlement_amount": "880", "settlement_fee": "8"}
    known = Ledger().replay([explicit], NOW)
    assert known.cash["CNY"] == Decimal("-888")
    assert not known.estimated
    missing = Ledger().replay([trade], NOW)
    assert missing.cash["CNY"] is None
    assert missing.holdings[("HK", "00700")].cost is None
    assert missing.warnings[0]["code"] == "missing_historical_fx"


def test_exchange_withdraw_and_external_flow_accounting():
    records = [
        record("opening_cash", amount="1000"),
        record(
            "exchange",
            amount="700",
            currency="CNY",
            settlement_currency="USD",
            settlement_amount="100",
            fee="1",
        ),
        record("withdraw", amount="50", currency="USD", fee="1"),
    ]
    state = Ledger(lambda currency, at: "7").replay(records, NOW)
    assert state.cash == {"CNY": Decimal("299"), "USD": Decimal("49")}
    assert state.net_invested == Decimal("650")
    assert state.realized_pnl == Decimal("-8")


def test_first_missing_price_and_cash_fx_never_become_zero(engine):
    engine.create_record(record("opening_position", quantity="1", price="10"))
    value = engine.overview(NOW)
    assert value["total"] is None and value["cumulative_pnl"] is None
    assert value["items"][0]["market_value_native"] is None
    assert not value["complete"]
    engine.repository.add_price(
        {"market": "A", "symbol": "600000", "price": "20", "occurred_at": "2026-11-06T00:00:00Z"}
    )
    assert engine.overview(NOW)["total"] is None
    engine.create_record(
        record("opening_cash", currency="USD", settlement_currency="USD", amount="100")
    )
    assert engine.overview(NOW)["cash"] is None
    engine.repository.add_fx(
        {"currency": "USD", "rate": "7", "occurred_at": DAY, "kind": "historical"}
    )
    assert engine.overview(NOW)["cash_balances"][0]["cny_value"] == "700"


def test_cash_fx_moves_value_but_not_historical_capital(engine):
    engine.repository.add_fx({"currency": "USD", "rate": "7", "occurred_at": DAY, "kind": "quote"})
    engine.create_record(
        record("opening_cash", currency="USD", settlement_currency="USD", amount="100")
    )
    engine.repository.add_fx(
        {"currency": "USD", "rate": "8", "occurred_at": NOW.isoformat(), "kind": "quote"}
    )
    value = engine.overview(NOW)
    assert value["net_invested"] == "700"
    assert value["cash"] == "800"
    assert value["cumulative_pnl"] == "100"


def test_actual_hk_settlement_clears_estimate_and_manual_value_preserves_audit(engine):
    engine.repository.add_fx(
        {"currency": "HKD", "rate": "0.9", "occurred_at": DAY, "kind": "historical"}
    )
    trade = engine.create_record(
        record(
            "buy", market="HK", symbol="00700", currency="HKD", quantity="1", price="100", fee="1"
        )
    )
    assert trade["estimated"]
    corrected = engine.update_record(
        trade["id"], {"settlement_amount": "88", "settlement_fee": "0.8"}
    )
    assert not corrected["estimated"]
    assert engine.repository.revisions(trade["id"])[0]["payload"]["estimated"]
    manual = engine.create_record(
        record(
            "opening_position",
            market="OTHER",
            symbol="PROPERTY",
            quantity="1",
            price="10",
            estimated=True,
        )
    )
    engine.repository.add_price(
        {
            "market": "OTHER",
            "symbol": "PROPERTY",
            "price": "12",
            "occurred_at": DAY,
            "kind": "manual",
            "source": "manual",
        }
    )
    item = next(item for item in engine.positions(NOW)["items"] if item["symbol"] == "PROPERTY")
    assert not item["estimated"] and item["valuation_verified"]
    assert item["cost_estimated"]
    assert engine.repository.get_record(manual["id"])["estimated"]


def test_legacy_manual_prices_remain_estimated_until_user_verifies_value(engine):
    opening = engine.create_record(
        record(
            "opening_position",
            market="OTHER",
            symbol="PROPERTY",
            quantity="1",
            price="10",
            estimated=True,
            source="legacy",
        )
    )
    engine.repository.add_price(
        {
            "market": "OTHER",
            "symbol": "PROPERTY",
            "price": "12",
            "occurred_at": DAY,
            "kind": "manual",
            "source": "legacy:data/manual_prices.csv",
        }
    )
    legacy = engine.positions(DAY)["items"][0]
    assert legacy["market_value"] == "12"
    assert legacy["estimated"] and legacy["cost_estimated"]
    assert not legacy["valuation_verified"]

    verified_at = "2026-11-04T08:00:00Z"
    engine.repository.add_price(
        {
            "market": "OTHER",
            "symbol": "PROPERTY",
            "price": "13",
            "occurred_at": verified_at,
            "kind": "manual",
            "source": "manual",
        }
    )
    verified = engine.positions(verified_at)["items"][0]
    assert verified["market_value"] == "13"
    assert not verified["estimated"] and verified["valuation_verified"]
    assert verified["cost_estimated"]
    assert engine.positions(DAY)["items"][0]["estimated"]
    assert engine.repository.get_record(opening["id"])["source"] == "legacy"
    assert engine.repository.revisions(opening["id"])[0]["payload"]["estimated"]


def test_daily_pnl_beijing_midnight_minus_new_external_money(engine):
    engine.create_record(record("opening_cash", amount="100"))
    engine.create_record(record("deposit", amount="50", occurred_at="2026-11-05T01:00:00Z"))
    engine.create_record(record("fee", amount="3", occurred_at="2026-11-05T01:30:00Z"))
    value = engine.overview(NOW)
    assert value["daily_baseline_at"] == "2026-11-04T16:00:00.000000+00:00"
    assert value["daily_net_external"] == "50"
    assert value["daily_pnl"] == "-3"


def test_crud_validates_entire_replay_and_preserves_revisions(engine):
    buy = engine.create_record(record("buy", quantity="10", price="10"))
    sell = engine.create_record(
        record("sell", quantity="5", price="12", occurred_at="2026-11-04T08:00:00Z")
    )
    with pytest.raises(LedgerError, match="exceeds"):
        engine.update_record(buy["id"], {"quantity": "4", "amount": "40"})
    with pytest.raises(LedgerError, match="exceeds"):
        engine.void_record(buy["id"])
    assert len(engine.repository.revisions(buy["id"])) == 1
    engine.void_record(sell["id"])
    engine.void_record(buy["id"])
    assert all(item["voided"] for item in engine.list_records())
    assert engine.repository.list_records() == []
    assert [item["operation"] for item in engine.repository.revisions(buy["id"])] == [
        "create",
        "void",
    ]


def test_rebuild_preserves_minute_spine_and_breaks_curve_gaps(engine):
    opening = engine.create_record(record("opening_cash", amount="10"))
    times = [NOW - timedelta(minutes=10), NOW - timedelta(minutes=9), NOW - timedelta(minutes=2)]
    for sample in times:
        engine.repository.save_snapshot(sample, "minute", engine.overview(sample))
    engine.update_record(opening["id"], {"amount": "20"})
    samples = engine.repository.snapshots(kind="minute")
    assert [item["occurred_at"] for item in samples] == [timestamp(sample) for sample in times]
    assert all(item["payload"]["total"] == "20" for item in samples)
    curve = engine.curve("today")
    assert [len(segment) for segment in curve["segments"]] == [2, 1]
    engine.rebuild_history()
    assert len(engine.repository.snapshots(kind="minute")) == 3
    assert engine.curve("quarter")["period"] == "3m"


def test_time_weighted_flow_fee_correction_restart_and_window_stability(engine):
    engine.create_record(record("opening_cash", amount="100000"))
    engine.create_record(record("dividend", amount="10000", occurred_at="2026-11-04T03:00:00Z"))
    deposited = engine.create_record(
        record("deposit", amount="100000", occurred_at="2026-11-04T04:00:00Z")
    )
    sample = NOW - timedelta(minutes=1)
    engine.repository.save_snapshot(sample, "minute", engine.overview(sample))
    today = engine.curve("today")
    assert today["return_baseline_date"] == "2026-11-03"
    assert today["points"][0]["time_weighted_return"] == "10"
    assert today["points"][0]["cumulative_return"] == "5"
    assert not today["points"][0]["twr_estimated"]
    assert engine.curve("all")["points"][0]["time_weighted_return"] == "0"
    engine.update_record(deposited["id"], {"fee": "2100"})
    assert engine.curve("today")["points"][0]["time_weighted_return"] == "8.9"
    restarted = InvestmentService(engine.database, FakeProvider())
    assert restarted.curve("today")["points"] == engine.curve("today")["points"]


def test_time_weighted_foreign_cash_flows_are_converted_at_event_rates(engine):
    engine.repository.add_fx({"currency": "USD", "rate": "7", "occurred_at": DAY})
    engine.create_record(
        record("opening_cash", currency="USD", settlement_currency="USD", amount="100")
    )
    engine.repository.add_fx(
        {"currency": "USD", "rate": "7.7", "occurred_at": "2026-11-04T02:59:30Z"}
    )
    engine.create_record(
        record(
            "deposit",
            currency="USD",
            settlement_currency="USD",
            amount="100",
            occurred_at="2026-11-04T03:00:00Z",
        )
    )
    engine.repository.add_fx(
        {"currency": "USD", "rate": "8.47", "occurred_at": "2026-11-05T01:00:00Z"}
    )
    sample = NOW - timedelta(minutes=1)
    engine.repository.save_snapshot(sample, "minute", engine.overview(sample))
    assert engine.curve("today")["points"][0]["time_weighted_return"] == "21"


def test_time_weighted_flow_uses_same_timestamp_fx_but_pre_flow_cash(engine):
    engine.repository.add_fx({"currency": "USD", "rate": "7", "occurred_at": DAY})
    engine.create_record(
        record("opening_cash", currency="USD", settlement_currency="USD", amount="100")
    )
    at = "2026-11-04T03:00:00Z"
    engine.repository.add_fx({"currency": "USD", "rate": "7.7", "occurred_at": at})
    engine.create_record(
        record("deposit", currency="USD", settlement_currency="USD", amount="100", occurred_at=at)
    )
    sample = NOW - timedelta(minutes=1)
    engine.repository.save_snapshot(sample, "minute", engine.overview(sample))
    assert engine.curve("today")["points"][0]["time_weighted_return"] == "10"


def test_time_weighted_stale_flow_boundary_estimate_and_missing_price_chain(engine):
    engine.create_record(record("opening_cash", amount="1000"))
    engine.create_record(
        record("buy", quantity="1", price="100", occurred_at="2026-11-04T01:00:00Z")
    )
    engine.create_record(record("deposit", amount="1000", occurred_at="2026-11-04T03:00:00Z"))
    engine.repository.add_price(
        {
            "market": "A",
            "symbol": "600000",
            "price": "110",
            "occurred_at": "2026-11-04T04:00:00Z",
            "kind": "daily",
        }
    )
    sample = NOW - timedelta(minutes=1)
    engine.repository.save_snapshot(sample, "minute", engine.overview(sample))
    assert engine.curve("today")["points"][0]["time_weighted_return"] is None
    engine.repository.add_price(
        {
            "market": "A",
            "symbol": "600000",
            "price": "100",
            "occurred_at": "2026-11-04T02:00:00Z",
            "kind": "daily",
        }
    )
    repaired = engine.curve("today")["points"][0]
    assert repaired["time_weighted_return"] == "0.5"
    assert repaired["twr_estimated"]


def test_daily_rebuild_uses_next_day_08_source_cutoff(engine):
    engine.create_record(record("opening_position", quantity="1", price="10"))
    engine.repository.add_price(
        {
            "market": "A",
            "symbol": "600000",
            "price": "99",
            "occurred_at": "2026-11-04T01:00:00Z",
            "kind": "daily",
        }
    )
    engine.rebuild_history()
    snapshots = engine.repository.snapshots(kind="daily")
    assert snapshots[0]["occurred_at"] == "2026-11-04T00:00:00.000000+00:00"
    assert snapshots[0]["payload"]["total"] is None
    assert snapshots[1]["payload"]["total"] == "99"
    assert all(parse_time(row["occurred_at"]) <= NOW for row in snapshots)


def test_daily_close_excludes_newer_intraday_quotes_and_preserves_fx_source(engine):
    engine.repository.add_fx(
        {
            "currency": "USD",
            "rate": "7",
            "occurred_at": DAY,
            "kind": "historical",
            "source": "historical",
        }
    )
    engine.create_record(
        record(
            "opening_position", market="US", symbol="TEST", currency="USD", quantity="1", price="10"
        )
    )
    engine.repository.add_price(
        {"market": "US", "symbol": "TEST", "price": "11", "occurred_at": DAY, "kind": "daily"}
    )
    engine.repository.add_price(
        {
            "market": "US",
            "symbol": "TEST",
            "price": "100",
            "occurred_at": "2026-11-03T23:00:00Z",
            "kind": "quote",
        }
    )
    engine.rebuild_history()
    snapshots = engine.repository.snapshots(kind="daily")
    assert snapshots[0]["payload"]["total"] == "77"
    assert engine.overview(NOW)["total"] == "700"
    assert engine.positions(NOW)["items"][0]["fx_at"] == timestamp(DAY)
    engine.repository.set_setting("migration", {"inception": "2026-10-20"})
    assert "2026-10-20" in engine.overview(NOW)["history_note"]
    engine.update_record(engine.list_records()[0]["id"], {"quantity": "2", "amount": "20"})
    assert engine.repository.snapshots(kind="daily")[0]["payload"]["total"] == "154"


def test_crud_adds_historical_daily_points_and_only_invalidates_changed_coverage(engine):
    engine.repository.set_setting("history_day", "2026-11-05")
    cash = engine.create_record(record("opening_cash", amount="10"))
    assert len(engine.repository.snapshots(kind="daily")) == 2
    assert engine.repository.get_setting("history_day") is None
    engine.repository.set_setting("history_day", "2026-11-05")
    engine.update_record(cash["id"], {"amount": "20"})
    assert engine.repository.get_setting("history_day") == "2026-11-05"
    engine.create_record(record("opening_position", symbol="600001", quantity="1", price="1"))
    assert engine.repository.get_setting("history_day") is None


def test_repository_additive_tables_atomic_batch_cutoffs_and_retention(database):
    repository = InvestmentRepository(database)
    initialize(database)
    assert database.one("PRAGMA user_version")["user_version"] == 1
    database.set_setting("platform", {"preserved": True})
    repository.set_setting("platform", "separate")
    assert database.get_setting("platform") == {"preserved": True}
    with pytest.raises(ValueError):
        repository.save_prices(
            [
                {"market": "A", "symbol": "1", "price": "1", "occurred_at": DAY},
                {"market": "A", "symbol": "2", "price": "NaN", "occurred_at": DAY},
            ]
        )
    assert repository.prices() == []
    repository.add_fx({"currency": "USD", "rate": "7", "occurred_at": DAY, "kind": "quote"})
    repository.add_fx(
        {"currency": "USD", "rate": "8", "occurred_at": "2026-11-04T00:00:00Z", "kind": "quote"}
    )
    assert repository.latest_fx("USD", DAY, historical=True)["rate"] == "7"
    assert repository.latest_fx("USD", "2026-11-02T00:00:00Z", historical=True) is None
    repository.save_import("preview", "hash", {"rows": [1]})
    repository.confirm_import("preview")
    assert repository.find_import("hash", confirmed=True)["payload"] == {"rows": [1]}
    old = NOW - timedelta(days=100)
    repository.save_snapshot(old, "minute", {"total": "1"})
    repository.save_snapshot(old, "daily", {"total": "1"})
    repository.prune_minutes(NOW - timedelta(days=90))
    assert repository.snapshots(kind="minute") == []
    assert len(repository.snapshots(kind="daily")) == 1


async def test_empty_portfolio_never_contacts_provider(engine):
    assert (await engine.refresh())["total"] == "0"
    assert engine.provider.calls == []


@pytest.mark.parametrize(
    ("currency", "market", "rate", "expected_cash"),
    [("USD", "US", "7.2", "720"), ("HKD", "HK", "0.9", "90")],
)
async def test_foreign_cash_only_samples_fx_during_its_market_session(
    engine, monkeypatch, currency, market, rate, expected_cash
):
    engine.repository.add_fx(
        {"currency": currency, "rate": "1", "occurred_at": DAY, "kind": "historical"}
    )
    engine.create_record(
        record(
            "opening_cash",
            market="CASH",
            symbol="",
            currency=currency,
            settlement_currency=currency,
            amount="100",
        )
    )
    engine.repository.set_setting("history_day", "2026-11-05")

    async def fx_rates():
        engine.provider.calls.append("fx_rates")
        return [
            {"currency": currency, "rate": rate, "occurred_at": NOW.isoformat(), "source": "fake"}
        ]

    engine.provider.fx_rates = fx_rates
    # An unrelated open market must not create cash-only samples.
    await engine.refresh(active_only=True)
    assert "fx_rates" not in engine.provider.calls
    assert engine.repository.snapshots(kind="minute") == []

    engine.provider.sessions = lambda at: [{"market": market, "state": "open", "next_open": None}]
    value = await engine.refresh(active_only=True)
    assert value["cash"] == expected_cash
    assert engine.provider.calls.count("fx_rates") == 1
    assert "quotes" not in engine.provider.calls
    assert len(engine.repository.snapshots(kind="minute")) == 1

    engine.provider.sessions = lambda at: [{"market": market, "state": "closed", "next_open": None}]
    monkeypatch.setattr(
        service_module,
        "parse_time",
        lambda value=None: NOW + timedelta(minutes=1) if value is None else parse_time(value),
    )
    await engine.refresh(active_only=True)
    assert engine.provider.calls.count("fx_rates") == 1
    assert len(engine.repository.snapshots(kind="minute")) == 1


async def test_partial_history_failure_retains_other_instruments_and_no_future_prices(engine):
    engine.provider.fail_history = True
    engine.create_record(record("opening_position", quantity="1", price="10"))
    engine.create_record(record("opening_position", symbol="600001", quantity="1", price="10"))
    value = await engine.refresh()
    assert value["total"] == "24"
    assert any(warning["code"] == "history_refresh_failed" for warning in value["warnings"])
    assert engine.repository.prices("A", "600001", kind="daily")
    assert not engine.repository.prices(end="2026-11-06T08:00:00Z")[-1]["source"] == "future"
    count = engine.provider.calls.count("history:600000")
    await engine.refresh()
    assert engine.provider.calls.count("history:600000") == count


async def test_failed_history_retries_after_ten_minutes_then_completes_day(engine, monkeypatch):
    engine.provider.fail_history = True
    engine.create_record(record("opening_position", quantity="1", price="10"))
    await engine.refresh()
    assert engine.repository.get_setting("history_day") is None
    assert engine.repository.get_setting("history_retry_after") == timestamp(
        NOW + timedelta(minutes=10)
    )
    assert engine.provider.calls.count("history:600000") == 1

    engine.provider.fail_history = False
    monkeypatch.setattr(
        service_module,
        "parse_time",
        lambda value=None: NOW + timedelta(minutes=9) if value is None else parse_time(value),
    )
    waiting = await engine.refresh()
    assert engine.provider.calls.count("history:600000") == 1
    assert engine.repository.get_setting("history_day") is None
    assert any(warning["code"] == "history_refresh_failed" for warning in waiting["warnings"])

    monkeypatch.setattr(
        service_module,
        "parse_time",
        lambda value=None: NOW + timedelta(minutes=10) if value is None else parse_time(value),
    )
    successful = await engine.refresh()
    assert engine.provider.calls.count("history:600000") == 2
    assert engine.repository.get_setting("history_day") == "2026-11-05"
    assert engine.repository.get_setting("history_retry_after") is None
    assert engine.repository.get_setting("history_warnings") == []
    assert not any(
        warning["code"] == "history_refresh_failed" for warning in successful["warnings"]
    )
    await engine.refresh()
    assert engine.provider.calls.count("history:600000") == 2


async def test_closed_market_loop_skips_minute_but_refreshes_daily_after_08(engine):
    engine.provider.state = "closed"
    engine.create_record(record("opening_cash", amount="10"))
    await engine.refresh(active_only=True)
    assert "quotes" not in engine.provider.calls
    assert engine.repository.snapshots(kind="minute") == []
    assert engine.repository.get_setting("history_day") == "2026-11-05"
    await engine.refresh()
    assert len(engine.repository.snapshots(kind="minute")) == 1


async def test_history_waits_until_beijing_08_and_stale_prices_warn(engine, monkeypatch):
    before = datetime(2026, 11, 4, 23, tzinfo=UTC)
    monkeypatch.setattr(
        service_module,
        "parse_time",
        lambda value=None: before if value is None else parse_time(value),
    )
    engine.create_record(record("opening_cash", amount="10"))
    await engine.refresh(active_only=True)
    assert engine.repository.get_setting("history_day") is None
    monkeypatch.setattr(
        service_module, "parse_time", lambda value=None: NOW if value is None else parse_time(value)
    )
    await engine.refresh(active_only=True)
    assert engine.repository.get_setting("history_day") == "2026-11-05"
    engine.create_record(record("opening_position", quantity="1", price="10"))
    engine.repository.add_price(
        {"market": "A", "symbol": "600000", "price": "12", "occurred_at": DAY}
    )
    assert engine.positions(NOW)["items"][0]["stale"]
    engine._sessions = [{"market": "A", "state": "closed"}]
    assert not engine.positions(NOW)["items"][0]["stale"]


async def test_refresh_lock_and_shutdown_cancel_manual_task(engine):
    engine.create_record(record("opening_cash", amount="10"))
    started, cancelled = asyncio.Event(), asyncio.Event()

    async def slow_sessions(at):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    engine.provider.sessions = slow_sessions
    engine._manual_refresh = asyncio.create_task(engine.refresh())
    await started.wait()
    assert engine._refresh_lock.locked()
    await engine.stop()
    assert cancelled.is_set()
    assert engine._manual_refresh is None
    assert not engine._refresh_lock.locked()
