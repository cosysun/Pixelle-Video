"""Synthetic import/migration fixtures; no private portfolio values belong here."""

import csv
import io
import json
from datetime import datetime
from decimal import Decimal

import pytest
from openpyxl import Workbook

from pixelle_video.investment.imports import (
    MAX_FILE_BYTES,
    ImportService,
    column_mapping,
    normalize_record,
    parse_file,
)
from pixelle_video.investment.migration import (
    _cache_rows,
    _legacy_records,
    _load_sources,
    migrate_legacy,
)
from pixelle_video.investment.repository import InvestmentRepository
from pixelle_video.platform.database import Database


def csv_bytes(columns, rows):
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(columns)
    writer.writerows(rows)
    return stream.getvalue().encode("utf-8-sig")


def write_csv(path, columns, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(csv_bytes(columns, rows))


def trade(**changes):
    return {
        "occurred_at": "2026-10-20",
        "action": "buy",
        "market": "HK",
        "symbol": "700",
        "quantity": "2",
        "price": "10.25",
        **changes,
    }


@pytest.mark.parametrize(
    ("market", "symbol", "expected"),
    [
        ("HK", "hk700", "00700"),
        ("HK", "700.0", "00700"),
        ("A", "SH600519", "600519"),
        ("FUND", "123", "000123"),
        ("US", "BRK.B", "BRK.B"),
        ("US", "USFD", "USFD"),
        ("HK", "HK:700", "00700"),
    ],
)
def test_normalized_symbols_and_end_of_day(market, symbol, expected):
    record = normalize_record(trade(market=market, symbol=symbol))
    assert record["symbol"] == expected
    assert record["date_only"] is True
    assert record["occurred_at"] == "2026-10-20T15:59:59.999999+00:00"
    assert record["amount"] == "20.50"
    assert record["settlement_amount"] is None
    assert record["settlement_currency"] == ("USD" if market == "US" else "CNY")


def test_settlement_fees_dividend_attribution_and_exchange():
    record = normalize_record(trade(settlement_fee="0.25", settlement_amount="18"))
    assert record["settlement_fee"] == "0.25"
    dividend = normalize_record(trade(action="dividend", amount="3"))
    assert dividend["symbol"] == "00700"
    fee = normalize_record(
        {"occurred_at": "2026-10-20", "action": "fee", "amount": "0", "fee": "2"}
    )
    assert fee["amount"] == "0"
    exchange = normalize_record(
        {
            "occurred_at": "2026-10-20",
            "action": "exchange",
            "currency": "CNY",
            "amount": "700",
            "settlement_currency": "USD",
            "settlement_amount": "100",
        }
    )
    assert exchange["currency"] == "CNY" and exchange["amount"] == "700"
    assert exchange["settlement_currency"] == "USD" and exchange["settlement_amount"] == "100"


@pytest.mark.parametrize(
    "changes",
    [
        {"quantity": "oops"},
        {"quantity": ""},
        {"price": "NaN"},
        {"fee": "-1"},
        {"settlement_fee": "#VALUE!"},
        {"price": "1e1000000"},
        {"amount": "12,34"},
        {"occurred_at": "2026-02-30"},
        {"market": "INDEX"},
        {"symbol": "700.5"},
    ],
)
def test_invalid_values_are_rejected(changes):
    with pytest.raises(ValueError):
        normalize_record(trade(**changes))


def test_read_only_xlsx_formula_errors_and_real_row_indices():
    book = Workbook()
    sheet = book.active
    sheet.title = "交易"
    sheet.append(["日期", "动作", "市场", "代码", "数量", "价格", "结算费用"])
    sheet.append([datetime(2026, 10, 20), "买入", "HK", 700, 2, 10.25, 0.2])
    sheet.append([None] * 7)
    sheet.append(["2026-10-21", "买入", "HK", 700, 2, "=10+2", 0])
    sheet.append(["2026-10-21", "买入", "HK", 700, 2, "#VALUE!", 0])
    stream = io.BytesIO()
    book.save(stream)
    parsed = parse_file(stream.getvalue(), "holdings.xlsx")[0]
    assert parsed["name"] == "交易"
    assert [row["index"] for row in parsed["rows"]] == [2, 4, 5]
    assert "formula has no cached value" in parsed["rows"][1]["errors"][0]
    assert "spreadsheet error" in parsed["rows"][2]["errors"][0]
    assert column_mapping(parsed["columns"])["settlement_fee"] == "结算费用"


def test_csv_headers_size_and_mapping():
    parsed = parse_file(
        csv_bytes(["日期", "代码", "成交价格"], [["2026-10-20", "00700", "1.00"]]), "a.csv"
    )[0]
    assert parsed["rows"][0]["raw"]["代码"] == "00700"
    assert column_mapping(parsed["columns"])["price"] == "成交价格"
    assert column_mapping(parsed["columns"], {"price": "代码"})["price"] == "代码"
    assert column_mapping(parsed["columns"], {"代码": "price"})["price"] == "代码"
    with pytest.raises(ValueError, match="10 MB"):
        parse_file(b"a" * (MAX_FILE_BYTES + 1), "x.csv")
    with pytest.raises(ValueError, match="duplicate"):
        parse_file(b"a,a\n1,2\n", "x.csv")
    with pytest.raises(ValueError, match="only"):
        parse_file(b"hello", "x.xls")


@pytest.fixture
def legacy_root(tmp_path):
    root = tmp_path / "legacy"
    root.mkdir()
    (root / "config.json").write_text(
        json.dumps({"inception": "2026-10-20", "base_currency": "CNY", "fx_fallback": {"HKD": 9.9}})
    )
    columns = ["日期", "代码", "市场", "动作", "数量", "价格", "金额", "费用", "币种", "备注"]
    write_csv(
        root / "data/transactions.csv",
        columns,
        [
            ["2026-10-20", "", "CASH", "转入", 0, 1, 130, 0, "CNY", "期初建仓转入（CNY）"],
            ["2026-10-20", "", "CASH", "转入", 0, 1, 20, 0, "HKD", "期初建仓转入（HKD）"],
            ["2026-10-20", "1", "A", "买入", 10, 10, 100, 0, "CNY", "期初建仓买入"],
            ["2026-10-20", "700", "HK", "买入", 2, 10, 20, 0, "HKD", "期初建仓买入"],
            ["2026-11-03", "700", "HK", "卖出", 1, 12, 12, 1, "HKD", "later sale"],
            ["2026-11-03", "", "CASH", "转入", 0, 1, 5, 0, "USD", "later deposit"],
        ],
    )
    write_csv(
        root / "data/instruments.csv",
        ["名称", "代码", "市场", "资产类别", "币种"],
        [["Synthetic HK", "700", "HK", "stock", "HKD"]],
    )
    write_csv(
        root / "data/holdings_snapshot.csv",
        ["代码", "市场", "数量", "成本价"],
        [["700", "HK", 2, 10]],
    )
    write_csv(
        root / "data/manual_prices.csv",
        ["日期", "代码", "市场", "价格"],
        [["2026-10-20", "SYNTH", "OTHER", "1.25"]],
    )
    write_csv(
        root / "data/cache/prices/FX_HKDCNH.csv",
        ["date", "price"],
        [["2026-10-20", "0.9"], ["2026-11-03", "0.8"]],
    )
    write_csv(
        root / "data/cache/prices/FX_USDCNH.csv",
        ["date", "price"],
        [["2026-10-20", "7"], ["2026-11-03", "7.1"]],
    )
    write_csv(
        root / "data/cache/prices/US_BRK.B.csv",
        ["date", "price"],
        [["2026-10-20", "100"], ["2026-11-03", "110"]],
    )
    (root / "data/cache/fx.json").write_text(
        json.dumps(
            {
                "_latest": "2026-11-05",
                "2026-11-05": {"HKD": 9.9, "_ts": "2026-11-05T08:00:00+08:00"},
            }
        )
    )
    return root


def test_legacy_opening_partition_and_historical_fx(legacy_root):
    sources, hashes, _ = _load_sources(legacy_root)
    warnings = []
    prices, fx = _cache_rows(sources, warnings)
    records, _ = _legacy_records(sources, hashes, fx, warnings)
    capitals = [row for row in records if row["action"] == "opening_capital"]
    assert sorted(Decimal(row["amount"]) for row in capitals) == [Decimal("18"), Decimal("100")]
    assert all(row["currency"] == "CNY" for row in capitals)
    cash = [row for row in records if row["action"] == "opening_cash"]
    assert len(cash) == 1 and cash[0]["amount"] == "30"
    positions = [row for row in records if row["action"] == "opening_position"]
    assert len(positions) == 2
    hk = next(row for row in positions if row["market"] == "HK")
    assert hk["name"] == "Synthetic HK"
    assert hk["fx_rate"] == "0.9" and hk["settlement_currency"] == "CNY"
    sale = next(row for row in records if row["action"] == "sell")
    assert sale["settlement_amount"] is None and sale["fx_rate"] == "0.8"
    assert next(row for row in records if row["action"] == "deposit")["currency"] == "USD"
    assert (
        next(row for row in fx if row["rate"] == "0.9")["occurred_at"]
        == "2026-10-20T15:59:59.999999+00:00"
    )
    november_us = next(row for row in prices if row["market_date"] == "2026-11-03")
    assert november_us["symbol"] == "BRK.B"
    assert november_us["occurred_at"] == "2026-11-03T21:00:00.000000+00:00"
    assert not warnings


@pytest.fixture
def repository(tmp_path):
    database = Database(tmp_path / "investments.sqlite")
    yield InvestmentRepository(database)
    database.close()


def inspected(service, rows, columns=None, filename="x.csv"):
    columns = columns or ["日期", "动作", "市场", "代码", "数量", "价格", "金额", "币种", "流水号"]
    result = service.inspect(csv_bytes(columns, rows), filename)
    preview = service.preview(result["id"], "CSV", {})
    return result, preview


def test_inspection_preview_and_confirmation_survive_restart(repository):
    service = ImportService(repository)
    result, preview = inspected(
        service, [["2026-10-20", "买入", "A", "1", "2", "3.1", "", "CNY", ""]]
    )
    assert preview["rows"][0]["record"]["symbol"] == "000001"
    confirmed = ImportService(repository).confirm(result["id"], [2])
    assert confirmed["inserted"] == 1
    record = repository.list_records()[0]
    assert record["provenance"]["raw"]["数量"] == "2"
    assert len(repository.revisions(record["id"])) == 1
    again = service.confirm(result["id"], [2])
    assert again["inserted"] == 0 and again["already_confirmed"] == [2]
    raw = csv_bytes(
        ["日期", "动作", "市场", "代码", "数量", "价格", "金额", "币种", "流水号"],
        [["2026-10-20", "买入", "A", "1", "2", "3.1", "", "CNY", ""]],
    )
    assert service.inspect(raw, "renamed.csv")["duplicate_file"] is True


def test_no_id_identical_trades_are_suspected_but_user_can_select_both(repository):
    service = ImportService(repository)
    row = ["2026-10-20", "买入", "A", "1", "2", "3", "", "CNY", ""]
    result, preview = inspected(service, [row, row])
    assert not preview["rows"][0]["suspected_duplicate"]
    assert preview["rows"][1]["suspected_duplicate"]
    assert service.confirm(result["id"], [2, 3])["inserted"] == 2


def test_external_duplicates_no_double_insert_and_conflicts_rejected(repository):
    service = ImportService(repository)
    row = ["2026-10-20", "买入", "A", "1", "2", "3", "", "CNY", "broker-1"]
    result, preview = inspected(service, [row, row])
    assert preview["rows"][1]["external_duplicate"]
    assert service.confirm(result["id"], [2, 3])["inserted"] == 1
    # Different source-file bytes, identical external trade.
    result, preview = inspected(service, [row], filename="other.csv")
    assert preview["rows"][0]["external_duplicate"]
    assert service.confirm(result["id"], [2])["inserted"] == 0
    changed = row.copy()
    changed[4] = "3"
    result, preview = inspected(service, [changed])
    assert "external_id" in preview["rows"][0]["errors"][0]
    with pytest.raises(ValueError, match="validation"):
        service.confirm(result["id"], [2])


def test_external_duplicate_detected_again_at_confirm(repository):
    service = ImportService(repository)
    row = ["2026-10-20", "买入", "A", "1", "2", "3", "", "CNY", "broker-race"]
    result, preview = inspected(service, [row])
    concurrent = {**preview["rows"][0]["record"], "id": "concurrent"}
    repository.add_record(concurrent)
    assert service.confirm(result["id"], [2])["inserted"] == 0
    assert len(repository.list_records()) == 1


def test_invalid_selected_row_and_batch_failure_are_atomic(repository, monkeypatch):
    service = ImportService(repository)
    row = ["2026-10-20", "买入", "A", "1", "2", "3", "", "CNY", ""]
    bad = row.copy()
    bad[4] = "bad"
    result, _ = inspected(service, [row, bad])
    with pytest.raises(ValueError, match="validation"):
        service.confirm(result["id"], [2, 3])
    assert not repository.list_records()
    result, _ = inspected(service, [row, row])
    original = repository._revision
    count = 0

    def fail_second(connection, record, operation):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("injected revision failure")
        return original(connection, record, operation)

    monkeypatch.setattr(repository, "_revision", fail_second)
    with pytest.raises(RuntimeError, match="injected"):
        service.confirm(result["id"], [2, 3])
    assert not repository.list_records()
    assert not repository.database.all("SELECT * FROM investment_revisions")
    assert not repository.get_import(result["id"])["confirmed"]


def test_invalid_oversell_selection_is_rejected_before_persistence(repository):
    service = ImportService(repository)
    result, _ = inspected(service, [["2026-10-20", "卖出", "A", "1", "2", "3", "", "CNY", ""]])
    with pytest.raises(ValueError, match="Sell exceeds"):
        service.confirm(result["id"], [2])
    assert not repository.list_records()


def test_snapshot_synthesizes_principal_and_reconciliation_is_read_only(repository):
    from pixelle_video.investment.ledger import Ledger

    service = ImportService(repository)
    result = service.inspect(
        csv_bytes(
            ["代码", "市场", "数量", "成本价"], [["1", "A", "2", "10"], ["2", "A", "3", "20"]]
        ),
        "snapshot.csv",
    )
    preview = service.preview(result["id"], "CSV", {}, mode="snapshot", snapshot_date="2026-10-20")
    assert all(not row["errors"] for row in preview["rows"])
    assert service.confirm(result["id"], [2])["inserted"] == 2
    assert service.confirm(result["id"], [3])["inserted"] == 2
    state = Ledger().replay(repository.list_records())
    assert state.net_invested == Decimal("80")
    assert state.cash == {}
    assert len(state.holdings) == 2
    assert service.confirm(result["id"], [2, 3])["inserted"] == 0
    other = service.inspect(
        csv_bytes(["代码", "市场", "数量", "成本价"], [["1", "A", "4", "10"]]), "compare.csv"
    )
    blocked = service.preview(other["id"], "CSV", {}, mode="snapshot", snapshot_date="2026-10-21")
    assert any("already exists" in error for error in blocked["rows"][0]["errors"])
    compared = service.preview(other["id"], "CSV", {}, mode="reconcile", snapshot_date="2026-10-21")
    assert compared["validation_only"]
    assert len(compared["discrepancies"]) == 2
    with pytest.raises(ValueError, match="validation-only"):
        service.confirm(other["id"], [2])
    assert len(repository.list_records()) == 4


def test_cash_snapshot_counts_cash_and_principal_once(repository):
    from pixelle_video.investment.ledger import Ledger

    service = ImportService(repository)
    result = service.inspect(
        csv_bytes(["日期", "动作", "金额", "币种"], [["2026-10-20", "opening_cash", "25", "USD"]]),
        "cash.csv",
    )
    service.preview(result["id"], "CSV", {}, mode="snapshot", snapshot_date="2026-10-20")
    service.confirm(result["id"], [2])
    state = Ledger(lambda currency, at: "7").replay(repository.list_records())
    assert state.cash["USD"] == Decimal("25")
    assert state.net_invested == Decimal("175")


def test_migration_is_atomic_idempotent_and_preserves_source_files(repository, legacy_root):
    before = {
        p.relative_to(legacy_root): p.read_bytes() for p in legacy_root.rglob("*") if p.is_file()
    }
    report = migrate_legacy(repository, legacy_root)
    assert report["source_range"] == {"start": "2026-10-20", "end": "2026-11-03"}
    assert report["history"]["preopening_sold_pnl_available"] is False
    assert repository.get_setting("inception") == "2026-10-20"
    assert repository.get_setting("migration")["source_hash"] == report["source_hash"]
    records = repository.list_records()
    assert len(records) == report["record_count"]
    assert all(len(repository.revisions(record["id"])) == 1 for record in records)
    assert migrate_legacy(repository, legacy_root)["already_migrated"] is True
    assert len(repository.list_records()) == len(records)
    after = {
        p.relative_to(legacy_root): p.read_bytes() for p in legacy_root.rglob("*") if p.is_file()
    }
    assert after == before
    config = legacy_root / "config.json"
    config.write_text(config.read_text() + "\n")
    with pytest.raises(ValueError, match="sources changed"):
        migrate_legacy(repository, legacy_root)
    assert len(repository.list_records()) == len(records)


def test_migration_failure_rolls_back_all_tables(repository, legacy_root, monkeypatch):
    def fail(*args):
        raise RuntimeError("revision failed")

    monkeypatch.setattr(repository, "_revision", fail)
    with pytest.raises(RuntimeError, match="revision failed"):
        migrate_legacy(repository, legacy_root)
    assert not repository.list_records()
    assert not repository.prices()
    assert not repository.fx()
    assert repository.get_setting("migration") is None


def test_cache_no_future_lookahead_at_next_day_morning(repository, legacy_root):
    migrate_legacy(repository, legacy_root)
    at = "2026-11-04T00:00:00.000000+00:00"  # Nov 4, 08:00 Shanghai.
    row = repository.latest_price("US", "BRK.B", at)
    assert row["market_date"] == "2026-11-03"
    assert row["occurred_at"] == "2026-11-03T21:00:00.000000+00:00"
    assert (
        repository.latest_fx("HKD", "2026-11-03T00:00:00+00:00", historical=True)["rate"] == "0.9"
    )
    assert repository.latest_fx("HKD", at, historical=True)["rate"] == "0.8"


def test_migration_current_fallback_cannot_replace_missing_historical_fx(repository, legacy_root):
    (legacy_root / "data/cache/prices/FX_HKDCNH.csv").unlink()
    with pytest.raises(ValueError, match="historical source-day FX"):
        migrate_legacy(repository, legacy_root)
    assert not repository.list_records()


def test_import_keeps_collected_snapshot_times(repository):
    repository.save_snapshot(
        {"occurred_at": "2026-10-20T00:07:00+00:00", "kind": "minute", "payload": {"total": "0"}}
    )
    service = ImportService(repository)
    result, _ = inspected(service, [["2026-10-20", "转入", "CASH", "", "", "", "10", "CNY", ""]])
    service.confirm(result["id"], [2])
    assert repository.snapshots()[0]["occurred_at"] == "2026-10-20T00:07:00.000000+00:00"


def test_future_oversell_is_still_validated(repository):
    service = ImportService(repository)
    result, _ = inspected(service, [["2099-10-20", "卖出", "A", "1", "2", "3", "", "CNY", ""]])
    with pytest.raises(ValueError, match="Sell exceeds"):
        service.confirm(result["id"], [2])
    assert not repository.list_records()


def test_migration_report_excludes_unrelated_config(repository, legacy_root):
    config = legacy_root / "config.json"
    config.write_text(
        json.dumps(
            {
                "inception": "2026-10-20",
                "base_currency": "CNY",
                "wecom": {"doc_url": "synthetic-private-connector"},
                "library": {"token": "synthetic-token"},
            }
        )
    )
    report = migrate_legacy(repository, legacy_root)
    assert report["config"] == {"base_currency": "CNY", "inception": "2026-10-20"}
    assert "synthetic-private-connector" not in json.dumps(report)
    assert "synthetic-token" not in json.dumps(report)


def test_snapshot_partial_retry_cannot_replace_same_security(repository):
    service = ImportService(repository)
    result = service.inspect(
        csv_bytes(
            ["代码", "市场", "数量", "成本价"], [["1", "A", "2", "10"], ["1", "A", "3", "20"]]
        ),
        "snapshot.csv",
    )
    service.preview(result["id"], "CSV", {}, mode="snapshot", snapshot_date="2026-10-20")
    service.confirm(result["id"], [2])
    with pytest.raises(ValueError, match="already has an opening"):
        service.confirm(result["id"], [3])
    assert len(repository.list_records()) == 2


def test_snapshot_hkd_principal_uses_historical_rate_without_phantom_cash(repository):
    from pixelle_video.investment.ledger import Ledger

    repository.add_fx(
        {
            "currency": "HKD",
            "occurred_at": "2026-10-20T12:00:00+00:00",
            "rate": "0.9",
            "kind": "historical",
        }
    )
    service = ImportService(repository)
    result = service.inspect(
        csv_bytes(["代码", "市场", "数量", "成本价"], [["700", "HK", "2", "10"]]), "hk-snapshot.csv"
    )
    service.preview(result["id"], "CSV", {}, mode="snapshot", snapshot_date="2026-10-20")
    service.confirm(result["id"], [2])
    state = Ledger(lambda currency, at: repository.latest_fx(currency, at, historical=True)).replay(
        repository.list_records()
    )
    assert state.net_invested == Decimal("18")
    assert state.cash == {}
    assert state.holdings[("HK", "00700")].cost == Decimal("18")
    assert all(record["estimated"] for record in repository.list_records())


def test_explicit_capital_cannot_double_count_snapshot_positions(repository):
    service = ImportService(repository)
    result = service.inspect(
        csv_bytes(
            ["动作", "代码", "市场", "数量", "成本价", "金额"],
            [
                ["opening_position", "1", "A", "2", "10", ""],
                ["opening_capital", "", "CASH", "", "", "20"],
            ],
        ),
        "snapshot.csv",
    )
    preview = service.preview(result["id"], "CSV", {}, mode="snapshot", snapshot_date="2026-10-20")
    assert not preview["rows"][0]["errors"]
    assert "automatically establishes capital" in preview["rows"][1]["errors"][0]
    with pytest.raises(ValueError, match="validation"):
        service.confirm(result["id"], [2, 3])


def initial_snapshot_readme(root):
    (root / "README.md").write_text(
        "# Synthetic portfolio\n\n"
        "## 第一次：用持仓快照一键建仓\n\n"
        "只填 data/holdings_snapshot.csv，再运行 scripts/init_from_snapshot.py 生成期初流水。\n\n"
        "## 之后：每笔交易加一行流水\n\n后续买卖记录到 transactions.csv。\n",
        encoding="utf-8",
    )


def test_migration_reconciles_initial_snapshot_without_applying_later_sales(
    repository, legacy_root
):
    initial_snapshot_readme(legacy_root)
    write_csv(
        legacy_root / "data/holdings_snapshot.csv",
        ["代码", "市场", "数量", "成本价"],
        [
            ["000001", "A", "10", "10"],
            ["HK:700", "港股", "2.000", "10"],
        ],
    )
    _, _, digest = _load_sources(legacy_root)
    report = migrate_legacy(repository, legacy_root)
    comparison = report["opening_snapshot_reconciliation"]
    assert comparison["role"] == "initial"
    assert comparison["role_evidence"]["basis"] == "readme_initial_snapshot"
    assert comparison["role_evidence"]["file"] == "README.md"
    assert comparison["snapshot_date"] == "2026-10-20"
    assert comparison["validation_only"] is True
    assert comparison["matched"] is True
    assert comparison["discrepancies"] == []
    assert comparison["snapshot_positions"] == comparison["opening_positions"] == 2
    assert report["source_hash"] == digest
    assert "README.md" not in report["source_files"]
    from pixelle_video.investment.ledger import Ledger

    state = Ledger(lambda currency, at: repository.latest_fx(currency, at, historical=True)).replay(
        repository.list_records()
    )
    assert state.holdings[("HK", "00700")].quantity == Decimal("1")
    assert migrate_legacy(repository, legacy_root)["opening_snapshot_reconciliation"] == comparison


def test_migration_reports_quantity_and_missing_position_discrepancies(repository, legacy_root):
    initial_snapshot_readme(legacy_root)
    write_csv(
        legacy_root / "data/holdings_snapshot.csv",
        ["代码", "市场", "数量", "成本价"],
        [
            ["700", "HK", "3", "10"],
            ["2", "A", "4", "5"],
        ],
    )
    report = migrate_legacy(repository, legacy_root)
    comparison = report["opening_snapshot_reconciliation"]
    discrepancies = {row["symbol"]: row for row in comparison["discrepancies"]}
    assert comparison["complete"] is True and comparison["matched"] is False
    assert discrepancies["00700"] == {
        "code": "quantity_mismatch",
        "market": "HK",
        "symbol": "00700",
        "snapshot_quantity": "3",
        "opening_quantity": "2",
        "difference": "-1",
        "snapshot_indices": [2],
        "opening_record_id": next(
            row["id"]
            for row in repository.list_records()
            if row["action"] == "opening_position" and row["market"] == "HK"
        ),
    }
    assert discrepancies["000001"]["code"] == "missing_snapshot_position"
    assert discrepancies["000001"]["snapshot_indices"] == []
    assert discrepancies["000002"]["code"] == "missing_opening_position"
    assert discrepancies["000002"]["opening_record_id"] is None
    assert (
        sum(
            warning["code"] == "opening_snapshot_quantity_discrepancy"
            for warning in report["warnings"]
        )
        == 3
    )
    assert len(repository.list_records()) == report["record_count"] == 7
    assert not any(row["symbol"] == "000002" for row in repository.list_records())


@pytest.mark.parametrize(
    ("heading", "role"), [("当前持仓快照", "current"), ("文件说明", "unknown")]
)
def test_migration_does_not_compare_current_or_unspecified_snapshot_as_initial(
    repository, legacy_root, heading, role
):
    (legacy_root / "README.md").write_text(
        f"# Synthetic portfolio\n\n## {heading}\n\ndata/holdings_snapshot.csv\n", encoding="utf-8"
    )
    report = migrate_legacy(repository, legacy_root)
    comparison = report["opening_snapshot_reconciliation"]
    assert comparison["role"] == role
    assert comparison["status"] == "not_compared"
    assert not comparison["complete"]
    assert comparison["discrepancies"] == []
    assert any(
        warning["code"] == "opening_snapshot_role_unverified" for warning in report["warnings"]
    )


def test_existing_migration_enriches_only_report_from_original_opening_sources(
    repository, legacy_root, monkeypatch
):
    initial_snapshot_readme(legacy_root)
    report = migrate_legacy(repository, legacy_root)
    old_report = {
        key: value for key, value in report.items() if key != "opening_snapshot_reconciliation"
    }
    old_report["warnings"] = [
        warning
        for warning in old_report["warnings"]
        if not warning["code"].startswith("opening_snapshot_")
    ]
    repository.set_setting("migration", old_report)
    # Later record edits must not rewrite the original migration reconciliation.
    opening = next(
        row
        for row in repository.list_records()
        if row["action"] == "opening_position" and row["market"] == "HK"
    )
    repository.update_record(opening["id"], {"quantity": "9"})
    repository.save_snapshot(
        {"occurred_at": "2026-11-03T00:07:00+00:00", "kind": "minute", "payload": {"total": "42"}}
    )
    tables = (
        "investment_records",
        "investment_revisions",
        "investment_prices",
        "investment_fx",
        "investment_snapshots",
        "investment_imports",
    )
    before = {table: repository.database.all(f"SELECT * FROM {table}") for table in tables}
    before_settings = repository.database.all(
        "SELECT * FROM investment_settings WHERE id!='migration'"
    )

    def fail_financial_conversion(*args):
        raise AssertionError("report enrichment must not replay migration financial conversion")

    monkeypatch.setattr("pixelle_video.investment.migration._cache_rows", fail_financial_conversion)
    monkeypatch.setattr(
        "pixelle_video.investment.migration._legacy_records", fail_financial_conversion
    )
    enriched = migrate_legacy(repository, legacy_root)
    assert enriched["already_migrated"] is True
    assert enriched["source_hash"] == old_report["source_hash"]
    assert enriched["created_at"] == old_report["created_at"]
    assert enriched["record_count"] == old_report["record_count"]
    # HK snapshot quantity 2 still matches its source opening 2; only absent A is reported.
    comparison = enriched["opening_snapshot_reconciliation"]
    assert [row["symbol"] for row in comparison["discrepancies"]] == ["000001"]
    assert repository.get_setting("migration")["opening_snapshot_reconciliation"] == comparison
    assert {table: repository.database.all(f"SELECT * FROM {table}") for table in tables} == before
    assert (
        repository.database.all("SELECT * FROM investment_settings WHERE id!='migration'")
        == before_settings
    )

    def fail_write(*args):
        raise AssertionError("repeat enrichment must not write")

    monkeypatch.setattr(repository, "set_setting", fail_write)
    repeated = migrate_legacy(repository, legacy_root)
    assert repeated == enriched


def test_invalid_snapshot_quantity_is_reported_without_replacing_opening(repository, legacy_root):
    initial_snapshot_readme(legacy_root)
    write_csv(
        legacy_root / "data/holdings_snapshot.csv",
        ["代码", "市场", "数量", "成本价"],
        [["700", "HK", "invalid", "10"]],
    )
    report = migrate_legacy(repository, legacy_root)
    comparison = report["opening_snapshot_reconciliation"]
    assert not comparison["complete"]
    assert comparison["errors"][0]["index"] == 2
    assert "quantity" in comparison["errors"][0]["message"]
    assert any(warning["code"] == "opening_snapshot_invalid_row" for warning in report["warnings"])
    assert (
        next(
            row["quantity"]
            for row in repository.list_records()
            if row["action"] == "opening_position" and row["market"] == "HK"
        )
        == "2"
    )


def test_snapshot_reconciliation_aggregates_decimal_rows_and_ignores_later_opening(
    repository, legacy_root
):
    from pixelle_video.investment.migration import _snapshot_reconciliation

    initial_snapshot_readme(legacy_root)
    write_csv(
        legacy_root / "data/holdings_snapshot.csv",
        ["代码", "市场", "数量"],
        [
            ["1", "A", "10"],
            ["700", "HK", "0.125"],
            ["00700", "HK", "1.875"],
        ],
    )
    sources, hashes, _ = _load_sources(legacy_root)
    _, fx = _cache_rows(sources, [])
    records, _ = _legacy_records(sources, hashes, fx, [])
    records.append(
        {
            **next(
                row
                for row in records
                if row["action"] == "opening_position" and row["market"] == "HK"
            ),
            "id": "later-opening",
            "occurred_at": "2026-11-03T15:59:59.999999+00:00",
            "quantity": "9",
        }
    )
    comparison, warnings = _snapshot_reconciliation(legacy_root, sources, records, "2026-10-20")
    assert comparison["matched"] is True
    assert comparison["discrepancies"] == []
    assert comparison["opening_positions"] == 2
    assert any(warning["code"] == "opening_snapshot_duplicate_security" for warning in warnings)
