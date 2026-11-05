"""Bounded, read-only file inspection and explicit investment import confirmation.

Preview indices are the original one-based worksheet/CSV row numbers (header is 1).
Decimal values are stored as strings; a missing optional value differs from an invalid value.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from xml.etree.ElementTree import ParseError
from zoneinfo import ZoneInfo

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_ROWS = 50000
MAX_COLUMNS = 200
SHANGHAI = ZoneInfo("Asia/Shanghai")
UTC = timezone.utc
OPENING_ACTIONS = {"opening_position", "opening_capital", "opening_cash"}
FIELDS = {
    "occurred_at": ("日期", "交易日期", "成交日期", "时间", "date", "datetime"),
    "symbol": ("代码", "证券代码", "股票代码", "证券代号", "code"),
    "market": ("市场", "交易市场"),
    "action": ("动作", "交易类型", "买卖方向", "业务名称", "type"),
    "quantity": ("数量", "成交数量", "持仓数量", "qty"),
    "price": ("价格", "成交价格", "成本价", "cost_price"),
    "amount": ("金额", "成交金额", "发生金额"),
    "fee": ("费用", "手续费", "fee_amount"),
    "currency": ("币种", "报价币种", "币种代码"),
    "settlement_currency": ("结算币种", "目标币种"),
    "settlement_amount": ("结算金额", "人民币结算金额", "目标金额"),
    "settlement_fee": ("结算费用", "结算手续费", "人民币结算费用"),
    "external_id": ("流水号", "成交编号", "交易编号", "合同编号"),
    "name": ("名称", "证券名称"),
    "asset_class": ("资产类别",),
    "note": ("备注", "说明", "memo"),
    "fx_rate": ("汇率",),
}
ALIASES = {alias: key for key, aliases in FIELDS.items() for alias in (key, *aliases)}
ACTIONS = {
    "买入": "buy",
    "证券买入": "buy",
    "卖出": "sell",
    "证券卖出": "sell",
    "转入": "deposit",
    "入金": "deposit",
    "存入": "deposit",
    "转出": "withdraw",
    "出金": "withdraw",
    "取出": "withdraw",
    "分红": "dividend",
    "股息": "dividend",
    "费用": "fee",
    "手续费": "fee",
    "换汇": "exchange",
    "兑换": "exchange",
    "拆股": "split",
    "期初持仓": "opening_position",
    "期初投入": "opening_capital",
    "期初现金": "opening_cash",
}
VALID_ACTIONS = set(ACTIONS.values())
MARKETS = {
    "港股": "HK",
    "美股": "US",
    "A股": "A",
    "基金": "FUND",
    "现金": "CASH",
    "其他": "OTHER",
    "手工": "OTHER",
    "MANUAL": "OTHER",
}


def decimal_string(value, field, *, required=False, positive=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise ValueError(f"{field}: required numeric value / 必须填写数值")
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field}: invalid numeric value")
    text = str(value).strip()
    if "," in text:
        if not re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", text):
            raise ValueError(f"{field}: invalid thousands grouping")
        text = text.replace(",", "")
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"{field}: invalid numeric value / 数值无效") from exc
    if (
        not number.is_finite()
        or len(number.as_tuple().digits) > 80
        or abs(number.adjusted()) > 80
        or number < 0
        or (positive and number <= 0)
    ):
        raise ValueError(f"{field}: must be {'positive' if positive else 'nonnegative'} and finite")
    return format(number, "f")


def occurred_at(value):
    """Date-only rows belong to the end of that Shanghai day, expressed in UTC."""
    date_only = isinstance(value, date) and not isinstance(value, datetime)
    if isinstance(value, datetime):
        stamp = value
        date_only = stamp.time() == time.min and stamp.tzinfo is None
    elif isinstance(value, date):
        stamp = datetime.combine(value, time.max)
    elif isinstance(value, str):
        text = value.strip()
        date_only = bool(re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{8}", text))
        if date_only:
            parsed = None
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    pass
            if parsed is None:
                raise ValueError("occurred_at: invalid date")
            stamp = parsed
        else:
            try:
                stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("occurred_at: invalid date or timestamp") from exc
    else:
        raise ValueError("occurred_at: required date or timestamp")
    if date_only:
        stamp = datetime.combine(stamp.date(), time.max, SHANGHAI)
    elif stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=SHANGHAI)
    return stamp.astimezone(UTC).isoformat(timespec="microseconds"), date_only


def normalize_symbol(value, market):
    symbol = str(value or "").strip().upper()
    # Excel numeric security codes are often serialized with a trailing .0.
    if re.fullmatch(r"\d+\.0+", symbol):
        symbol = symbol.split(".")[0]
    for prefix in ("HK", "SH", "SZ", "BJ", "US"):
        tail = symbol[len(prefix) :].lstrip(".:")
        if symbol.startswith(prefix) and (
            tail.isdigit() or (prefix == "US" and symbol.startswith(("US:", "US.")))
        ):
            symbol = tail
            break
    symbol = re.sub(r"\.(HK|SH|SZ|SS|US)$", "", symbol)
    if market in {"HK", "A", "FUND", "INDEX"}:
        width = 5 if market == "HK" else 6
        if not symbol.isdigit() or len(symbol) > width:
            raise ValueError("symbol: invalid security code for market")
        symbol = symbol.zfill(width)
    elif not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]*", symbol):
        raise ValueError("symbol: invalid security code")
    return symbol


def normalize_record(fields):
    """Validate mapped fields without applying legacy opening-note heuristics."""
    raw_action = str(fields.get("action") or "").strip()
    action = ACTIONS.get(raw_action, raw_action.lower())
    if action not in VALID_ACTIONS:
        raise ValueError("action: unsupported transaction action")
    stamp, date_only = occurred_at(fields.get("occurred_at"))
    market_text = str(fields.get("market") or "").strip()
    market = MARKETS.get(market_text, market_text.upper())
    position = action in {"buy", "sell", "opening_position", "split"}
    if not market:
        if position:
            raise ValueError("market: required for a position")
        market = "OTHER"
    if market not in {"HK", "US", "A", "FUND", "INDEX", "OTHER", "CASH"}:
        raise ValueError("market: unsupported market")
    if not position and market == "CASH":
        market = "OTHER"
    if position and market in {"CASH", "INDEX"}:
        raise ValueError("market: unsupported position market")
    symbol = (
        normalize_symbol(fields.get("symbol"), market) if position or fields.get("symbol") else ""
    )
    currency = (
        str(fields.get("currency") or {"HK": "HKD", "US": "USD"}.get(market, "CNY")).strip().upper()
    )
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise ValueError("currency: expected three-letter code")
    if (
        position
        and market in {"HK", "US", "A", "FUND"}
        and currency != {"HK": "HKD", "US": "USD"}.get(market, "CNY")
    ):
        raise ValueError("currency: must match the security quotation currency")
    quantity = decimal_string(
        fields.get("quantity"), "quantity", required=position, positive=position
    )
    price = decimal_string(
        fields.get("price"),
        "price",
        required=action in {"buy", "sell", "opening_position"},
        positive=action in {"buy", "sell", "opening_position"},
    )
    amount = decimal_string(
        fields.get("amount"),
        "amount",
        required=not position and action != "fee",
        positive=not position and action != "fee",
    )
    if amount is None and quantity is not None and price is not None:
        with localcontext() as context:
            context.prec = 100
            amount = format(Decimal(quantity) * Decimal(price), "f")
    fee = decimal_string(fields.get("fee"), "fee")
    if action == "fee" and Decimal(amount or "0") + Decimal(fee or "0") <= 0:
        raise ValueError("fee: requires a positive amount or fee")
    settlement_currency = (
        str(fields.get("settlement_currency") or ("CNY" if market == "HK" else currency))
        .strip()
        .upper()
    )
    if not re.fullmatch(r"[A-Z]{3}", settlement_currency):
        raise ValueError("settlement_currency: expected three-letter code")
    settlement_amount = decimal_string(
        fields.get("settlement_amount"),
        "settlement_amount",
        required=action == "exchange",
        positive=action == "exchange",
    )
    settlement_fee = decimal_string(fields.get("settlement_fee"), "settlement_fee")
    if action == "exchange" and settlement_currency == currency:
        raise ValueError(
            "settlement_currency: exchange requires distinct source and target currencies"
        )
    fx_rate = decimal_string(fields.get("fx_rate"), "fx_rate", positive=True)
    result = {
        "action": action,
        "occurred_at": stamp,
        "date_only": date_only,
        "market": market,
        "symbol": symbol,
        "currency": currency,
        "quantity": quantity or "0",
        "price": price or "0",
        "amount": amount or "0",
        "fee": fee if fee is not None else "0",
        "settlement_currency": settlement_currency,
        "settlement_amount": settlement_amount,
        "settlement_fee": settlement_fee,
    }
    if currency != settlement_currency and (
        settlement_amount is None or (Decimal(fee or "0") != 0 and settlement_fee is None)
    ):
        result["estimated"] = True
    if market == "OTHER" and symbol:
        result["estimated"] = True
        result["valuation_kind"] = "manual_estimate"
    if fx_rate is not None:
        result["fx_rate"] = fx_rate
    for key in ("external_id", "name", "asset_class", "note"):
        if fields.get(key) is not None and str(fields[key]).strip():
            result[key] = str(fields[key]).strip()
    return result


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (float, Decimal)):
        return str(value)
    return value


def fingerprint(record):
    keys = (
        "action",
        "occurred_at",
        "market",
        "symbol",
        "currency",
        "quantity",
        "price",
        "amount",
        "fee",
        "settlement_currency",
        "settlement_amount",
        "settlement_fee",
    )
    numeric = {"quantity", "price", "amount", "fee", "settlement_amount", "settlement_fee"}
    values = {key: record.get(key) for key in keys}
    for key in numeric:
        if values[key] is not None:
            with localcontext() as context:
                context.prec = 100
                values[key] = str(Decimal(str(values[key])).normalize())
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _sheet(name, rows, formula_rows=None):
    if not rows:
        return {"name": name, "columns": [], "rows": []}
    columns = [str(value or "").strip() for value in rows[0]]
    while columns and not columns[-1]:
        columns.pop()
    if (
        len(columns) > MAX_COLUMNS
        or not columns
        or any(not c for c in columns)
        or len(set(columns)) != len(columns)
    ):
        raise ValueError(f"{name}: blank, duplicate, or excessive columns")
    parsed = []
    for index, values in enumerate(rows[1:], 2):
        if not any(value is not None and str(value).strip() for value in values):
            # An uncached formula still occupies a real row.
            if not formula_rows or index not in formula_rows:
                continue
        if index > MAX_ROWS + 1:
            raise ValueError(f"{name}: exceeds {MAX_ROWS} rows")
        if any(v is not None and str(v).strip() for v in values[len(columns) :]):
            raise ValueError(f"{name}: row {index} exceeds header width")
        raw = {
            column: json_value(values[i]) if i < len(values) else None
            for i, column in enumerate(columns)
        }
        parsed.append(
            {"index": index, "raw": raw, "errors": list((formula_rows or {}).get(index, []))}
        )
    return {"name": name, "columns": columns, "rows": parsed}


def parse_file(raw_bytes, filename):
    if not isinstance(raw_bytes, bytes):
        raise ValueError("file: expected raw bytes")
    if len(raw_bytes) > MAX_FILE_BYTES:
        raise ValueError("file exceeds the explicit 10 MB limit / 文件超过 10MB")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        try:
            text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw_bytes.decode("gb18030")
            except UnicodeDecodeError as exc:
                raise ValueError("CSV: unsupported text encoding") from exc
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        try:
            rows = []
            for row in csv.reader(io.StringIO(text, newline=""), dialect, strict=True):
                if len(rows) > MAX_ROWS or len(row) > MAX_COLUMNS:
                    raise ValueError("CSV: row or column limit exceeded")
                rows.append(row)
            return [_sheet("CSV", rows)]
        except csv.Error as exc:
            raise ValueError(f"CSV: invalid file: {exc}") from exc
    if suffix != ".xlsx":
        raise ValueError("file: only .csv and .xlsx are supported")
    try:
        with zipfile.ZipFile(io.BytesIO(raw_bytes)) as archive:
            if sum(item.file_size for item in archive.infolist()) > MAX_EXPANDED_BYTES:
                raise ValueError("XLSX: expanded workbook exceeds 64 MB")
        from openpyxl import load_workbook

        values_book = load_workbook(
            io.BytesIO(raw_bytes), read_only=True, data_only=True, keep_links=False
        )
        try:
            formula_book = load_workbook(
                io.BytesIO(raw_bytes), read_only=True, data_only=False, keep_links=False
            )
            try:
                sheets = []
                for sheet in values_book:
                    sheet.reset_dimensions()
                    formula_book[sheet.title].reset_dimensions()
                    rows, errors = [], {}
                    for index, (cells, formulas) in enumerate(
                        zip(sheet.iter_rows(), formula_book[sheet.title].iter_rows(), strict=True),
                        1,
                    ):
                        if index > MAX_ROWS + 1 or len(cells) > MAX_COLUMNS:
                            raise ValueError(f"{sheet.title}: row or column limit exceeded")
                        row_errors = []
                        for cell, formula in zip(cells, formulas, strict=True):
                            if cell.data_type == "e":
                                row_errors.append(
                                    f"{cell.coordinate}: spreadsheet error {cell.value}"
                                )
                            if formula.data_type == "f" and cell.value is None:
                                row_errors.append(
                                    f"{formula.coordinate}: formula has no cached value / 公式缺少缓存值"
                                )
                        if index == 1 and row_errors:
                            raise ValueError(
                                f"{sheet.title}: invalid header: {'; '.join(row_errors)}"
                            )
                        if row_errors:
                            errors[index] = row_errors
                        rows.append([cell.value for cell in cells])
                    sheets.append(_sheet(sheet.title, rows, errors))
                return sheets
            finally:
                formula_book.close()
        finally:
            values_book.close()
    except (zipfile.BadZipFile, KeyError, OSError, TypeError, csv.Error, ParseError) as exc:
        raise ValueError(f"XLSX: invalid workbook: {exc}") from exc


def column_mapping(columns, mapping=None):
    result = {ALIASES[column]: column for column in columns if column in ALIASES}
    for key, value in (mapping or {}).items():
        if key in FIELDS:
            field, column = key, value
        elif value in FIELDS:
            field, column = value, key
        else:
            raise ValueError(f"mapping: unknown field {key}")
        if column is None or column == "":
            result.pop(field, None)
        elif column not in columns:
            raise ValueError(f"mapping: unknown column {column}")
        else:
            result[field] = column
    return result


class ImportService:
    """Synchronous import sessions; the repository owns record persistence.

    Inspection and preview are persisted so confirmation remains possible after
    constructing a new service instance. Selection is all-or-nothing.
    """

    def __init__(self, repository):
        if hasattr(repository, "connection"):
            from .repository import InvestmentRepository

            repository = InvestmentRepository(repository)
        self.repository = repository

    def inspect(self, raw_bytes, filename):
        sheets = parse_file(raw_bytes, filename)
        digest = hashlib.sha256(raw_bytes).hexdigest()
        import_id = "import_" + digest
        with self.repository.database.lock:
            previous = self._get_import(import_id)
            if previous is None:
                payload = {
                    "filename": Path(filename).name,
                    "sheets": sheets,
                    "preview": None,
                    "confirmed_rows": {},
                    "hash": digest,
                }
                self._save_import(import_id, digest, payload, False)
            else:
                payload = previous["payload"]
        return {
            "id": import_id,
            "hash": digest,
            "duplicate_file": previous is not None,
            "sheets": [
                {
                    "name": sheet["name"],
                    "columns": sheet["columns"],
                    "rows": [row["raw"] for row in sheet["rows"][:10]],
                    "row_count": len(sheet["rows"]),
                }
                for sheet in payload["sheets"]
            ],
        }

    def preview(self, id, sheet, mapping, mode="transactions", snapshot_date=None):
        session = self._get_import(id)
        if session is None:
            raise ValueError("import session not found")
        payload = session["payload"]
        selected_sheet = next((item for item in payload["sheets"] if item["name"] == sheet), None)
        if selected_sheet is None:
            raise ValueError("import sheet not found")
        if mode not in {"transactions", "snapshot", "reconcile"}:
            raise ValueError("mode: expected transactions, snapshot, or reconcile")
        if mode in {"snapshot", "reconcile"} and snapshot_date is None:
            raise ValueError("snapshot_date: explicit opening snapshot date is required")
        mapping = column_mapping(selected_sheet["columns"], mapping)
        existing = self._records()
        # A retry/partial confirmation may refer to opening rows from this session.
        others = [
            record
            for record in existing
            if record.get("provenance", {}).get("import_id") != id
            or record["action"] not in OPENING_ACTIONS
        ]
        opening_error = self._snapshot_error(others) if mode == "snapshot" else None
        fingerprints = {fingerprint(record) for record in existing if not record.get("voided")}
        external = {
            record.get("external_id"): record for record in existing if record.get("external_id")
        }
        seen, seen_external, rows = set(), {}, []
        for item in selected_sheet["rows"]:
            errors, record = list(item["errors"]), None
            fields = {field: item["raw"].get(column) for field, column in mapping.items()}
            if mode in {"snapshot", "reconcile"}:
                fields["occurred_at"] = snapshot_date
                fields["action"] = fields.get("action") or "opening_position"
                if (
                    fields["action"] not in OPENING_ACTIONS
                    and ACTIONS.get(fields["action"]) not in OPENING_ACTIONS
                ):
                    errors.append("snapshot: only explicit opening records are allowed")
                if mode == "snapshot" and opening_error:
                    errors.append(opening_error)
            try:
                record = normalize_record(fields)
                record["id"] = (
                    "import_row_"
                    + hashlib.sha256(f"{id}\0{sheet}\0{item['index']}".encode()).hexdigest()
                )
                record["source"] = "import"
                record["provenance"] = {
                    "kind": "import",
                    "import_id": id,
                    "file": payload["filename"],
                    "hash": payload["hash"],
                    "sheet": sheet,
                    "row_index": item["index"],
                    "raw": item["raw"],
                }
                if mode == "transactions" and record["action"] in OPENING_ACTIONS:
                    errors.append("opening records require explicit snapshot mode")
                from .ledger import normalize_record as ledger_normalize

                record = ledger_normalize(record)
                if mode == "snapshot" and record["action"] == "opening_position":
                    # One capital record per selected position permits partial selection,
                    # including several currencies, without counting unselected holdings.
                    capital = {
                        **record,
                        "id": record["id"] + "_capital",
                        "action": "opening_capital",
                        "symbol": "",
                        "quantity": "0",
                        "price": "0",
                        "fee": "0",
                        "external_id": None,
                        "settlement_fee": "0",
                        "amount": format(Decimal(record["amount"]) + Decimal(record["fee"]), "f"),
                        "note": "Opening invested capital synthesized from snapshot position cost",
                        "provenance": {**record["provenance"], "derived": "opening_position_cost"},
                    }
                    if record["settlement_amount"] is not None:
                        if record["settlement_fee"] is None and Decimal(record["fee"]) != 0:
                            capital["settlement_amount"] = None
                        else:
                            capital["settlement_amount"] = format(
                                Decimal(record["settlement_amount"])
                                + Decimal(record["settlement_fee"] or "0"),
                                "f",
                            )
                    record["snapshot_capital"] = ledger_normalize(capital)
            except ValueError as exc:
                errors.append(str(exc))
                if record is None:
                    record = {}
            suspected, duplicate = False, False
            if record is not None and not errors:
                key = fingerprint(record)
                ext = record.get("external_id")
                if ext:
                    prior = external.get(ext) or seen_external.get(ext)
                    if prior is not None:
                        if fingerprint(prior) != key:
                            errors.append("external_id: conflicts with another record")
                        else:
                            duplicate = True
                    seen_external[ext] = record
                else:
                    suspected = key in fingerprints or key in seen
                seen.add(key)
            rows.append(
                {
                    "index": item["index"],
                    "record": record,
                    "errors": errors,
                    "suspected_duplicate": suspected,
                    "external_duplicate": duplicate,
                }
            )
        payload["preview"] = {
            "sheet": sheet,
            "mapping": mapping,
            "mode": mode,
            "snapshot_date": snapshot_date,
            "rows": rows,
        }
        if mode == "snapshot" and any(
            row["record"].get("action") == "opening_position" for row in rows
        ):
            for row in rows:
                if row["record"].get("action") == "opening_capital":
                    row["errors"].append(
                        "snapshot: position cost automatically establishes capital; use opening_cash only for additional cash"
                    )
        self._save_import(id, payload["hash"], payload, session["confirmed"])
        result = {"id": id, "sheet": sheet, "mode": mode, "rows": rows}
        if mode == "reconcile":
            from .ledger import Ledger

            at = occurred_at(snapshot_date)[0]
            state = Ledger().replay(existing, at=at)
            discrepancies = []
            seen_keys = set()
            for row in rows:
                if row["errors"] or row["record"] is None:
                    continue
                record = row["record"]
                key = record["market"], record["symbol"]
                seen_keys.add(key)
                holding = state.holdings.get(key)
                actual = holding.quantity if holding else Decimal("0")
                expected = Decimal(record["quantity"])
                if actual != expected:
                    discrepancies.append(
                        {
                            "index": row["index"],
                            "market": key[0],
                            "symbol": key[1],
                            "imported_quantity": format(expected, "f"),
                            "ledger_quantity": format(actual, "f"),
                        }
                    )
            for key, holding in state.holdings.items():
                if key not in seen_keys and holding.quantity:
                    discrepancies.append(
                        {
                            "index": None,
                            "market": key[0],
                            "symbol": key[1],
                            "imported_quantity": "0",
                            "ledger_quantity": format(holding.quantity, "f"),
                        }
                    )
            result.update({"validation_only": True, "discrepancies": discrepancies})
        return result

    @staticmethod
    def _snapshot_error(records):
        if any(
            record["action"] in OPENING_ACTIONS and not record.get("voided") for record in records
        ):
            return "snapshot: an opening snapshot already exists"
        if any(not record.get("voided") for record in records):
            return "snapshot: existing transactions overlap; import into an empty ledger"
        return None

    def _records(self):
        return self.repository.list_records(include_voided=True)

    def _get_import(self, id):
        return self.repository.get_import(id)

    def _save_import(self, id, digest, payload, confirmed):
        with self.repository.database.lock:
            previous = self.repository.get_import(id)
            if previous:
                payload["confirmed_rows"] = {
                    **payload.get("confirmed_rows", {}),
                    **previous["payload"].get("confirmed_rows", {}),
                }
                confirmed = confirmed or previous["confirmed"]
            return self.repository.save_import(id, digest, payload, confirmed)

    def confirm(self, id, indices):
        if not isinstance(indices, (list, tuple)) or any(
            type(index) is not int for index in indices
        ):
            raise ValueError("indices: expected original integer row numbers")
        indices = list(dict.fromkeys(indices))
        with self.repository.database.lock:
            session = self._get_import(id)
            if session is None or not session["payload"].get("preview"):
                raise ValueError("import preview not found")
            payload, preview = session["payload"], session["payload"]["preview"]
            if preview["mode"] == "reconcile":
                raise ValueError("reconcile is validation-only and cannot be confirmed")
            rows = {row["index"]: row for row in preview["rows"]}
            if any(index not in rows for index in indices):
                raise ValueError("indices: unknown preview row")
            if any(rows[index]["errors"] or rows[index]["record"] is None for index in indices):
                raise ValueError("selected rows contain validation errors")
            existing = self._records()
            markers = payload.setdefault("confirmed_rows", {})
            if preview["mode"] == "snapshot":
                error = self._snapshot_error(
                    [
                        record
                        for record in existing
                        if record.get("provenance", {}).get("import_id") != id
                        or record["action"] not in OPENING_ACTIONS
                    ]
                )
                if error:
                    raise ValueError(error)
                selected_positions = [
                    rows[index]["record"]
                    for index in indices
                    if rows[index]["record"]["action"] == "opening_position"
                ]
                keys = [(record["market"], record["symbol"]) for record in selected_positions]
                if len(keys) != len(set(keys)):
                    raise ValueError("snapshot: duplicate security rows would replace each other")
                prior_keys = {
                    (record["market"], record["symbol"])
                    for record in existing
                    if record["action"] == "opening_position" and not record.get("voided")
                }
                if any(
                    (rows[index]["record"]["market"], rows[index]["record"]["symbol"]) in prior_keys
                    for index in indices
                    if rows[index]["record"]["action"] == "opening_position"
                    and f"{preview['sheet']}:{index}" not in markers
                ):
                    raise ValueError("snapshot: this security already has an opening position")
            external = {
                record["external_id"]: record for record in existing if record.get("external_id")
            }
            prepared, record_ids, skipped, already = [], [], [], []
            for index in indices:
                marker = f"{preview['sheet']}:{index}"
                if marker in markers:
                    already.append(index)
                    record_ids.extend(markers[marker])
                    continue
                value = dict(rows[index]["record"])
                capital = value.pop("snapshot_capital", None)
                ext = value.get("external_id")
                if ext and ext in external:
                    prior = external[ext]
                    if fingerprint(prior) != fingerprint(value):
                        raise ValueError("external_id: conflicts with an existing record")
                    markers[marker] = [prior["id"]]
                    skipped.append(index)
                    record_ids.append(prior["id"])
                    continue
                if ext:
                    external[ext] = value
                values = [value, capital] if capital else [value]
                prepared.extend(values)
                markers[marker] = [item["id"] for item in values]
                record_ids.extend(markers[marker])
            # Replay the exact selection before writing. Oversells and invalid splits
            # fail here; negative cash is a reportable account state, not an oversell.
            from .ledger import Ledger

            all_records = existing + prepared
            cutoff = max((record["occurred_at"] for record in all_records), default=None)
            Ledger(
                lambda currency, at: self.repository.latest_fx(currency, at, historical=True)
            ).replay(all_records, cutoff)
            _persist_batch(
                self.repository,
                prepared,
                import_session={
                    "id": id,
                    "hash": payload["hash"],
                    "payload": payload,
                    "confirmed": True,
                },
            )
            return {
                "id": id,
                "confirmed": True,
                "inserted": len(prepared),
                "record_ids": record_ids,
                "skipped_external_duplicates": skipped,
                "already_confirmed": already,
                "indices": indices,
            }


def _persist_batch(repository, records=(), *, prices=(), fx=(), settings=None, import_session=None):
    """Use the verified shared schema and repository revision hook in one transaction.

    Repository convenience methods each commit; calling them inside a transaction
    would allow partial confirmation. This batch deliberately uses the same shared
    connection, normalization and revision implementation, without nested commits.
    """
    from .ledger import normalize_record as ledger_normalize
    from .ledger import timestamp
    from .repository import _json

    prepared = [ledger_normalize(record) for record in records]
    database = repository.database
    with database.lock, database.connection:
        connection = database.connection
        for record in prepared:
            connection.execute(
                "INSERT INTO investment_records(id,payload,voided,occurred_at) VALUES(?,?,?,?)",
                (record["id"], _json(record), int(record["voided"]), record["occurred_at"]),
            )
            repository._revision(connection, record, "create")
        for row in prices:
            connection.execute(
                "INSERT OR REPLACE INTO investment_prices(market,symbol,occurred_at,price,previous_close,source,market_date,kind) VALUES(?,?,?,?,?,?,?,?)",
                (
                    row["market"],
                    row["symbol"],
                    timestamp(row["occurred_at"]),
                    row["price"],
                    row.get("previous_close"),
                    row["source"],
                    row.get("market_date"),
                    row["kind"],
                ),
            )
        for row in fx:
            connection.execute(
                "INSERT OR REPLACE INTO investment_fx(currency,occurred_at,rate,source,kind) VALUES(?,?,?,?,?)",
                (
                    row["currency"],
                    timestamp(row["occurred_at"]),
                    row["rate"],
                    row["source"],
                    row["kind"],
                ),
            )
        for key, value in (settings or {}).items():
            connection.execute(
                "INSERT OR REPLACE INTO investment_settings(id,value) VALUES(?,?)",
                (key, _json(value)),
            )
        if import_session:
            row = import_session
            connection.execute(
                "INSERT OR REPLACE INTO investment_imports(id,hash,payload,confirmed) VALUES(?,?,?,?)",
                (row["id"], row["hash"], _json(row["payload"]), int(row["confirmed"])),
            )
