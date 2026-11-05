"""Additive SQLite storage for investments, sharing Database's lock/connection.

All methods are synchronous. Writes use the shared lock and a transaction;
record/revision pairs are atomic. Timestamps are fixed-precision UTC strings.
Market reads always select source times on/before the requested cutoff.
"""

import json
import uuid

from .ledger import decimal_string, decimal_value, normalize_record, timestamp

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS investment_records (
        id TEXT PRIMARY KEY, payload TEXT NOT NULL, voided INTEGER NOT NULL DEFAULT 0,
        occurred_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS investment_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, record_id TEXT NOT NULL,
        occurred_at TEXT NOT NULL, operation TEXT NOT NULL, payload TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS investment_prices (
        market TEXT NOT NULL, symbol TEXT NOT NULL, occurred_at TEXT NOT NULL,
        price TEXT NOT NULL, previous_close TEXT, source TEXT NOT NULL,
        market_date TEXT, kind TEXT NOT NULL DEFAULT 'quote',
        PRIMARY KEY (market,symbol,occurred_at,kind))""",
    """CREATE TABLE IF NOT EXISTS investment_fx (
        currency TEXT NOT NULL, occurred_at TEXT NOT NULL, rate TEXT NOT NULL,
        source TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'quote',
        PRIMARY KEY (currency,occurred_at,kind))""",
    """CREATE TABLE IF NOT EXISTS investment_snapshots (
        occurred_at TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
        PRIMARY KEY (occurred_at,kind))""",
    """CREATE TABLE IF NOT EXISTS investment_imports (
        id TEXT PRIMARY KEY, hash TEXT NOT NULL, payload TEXT NOT NULL,
        confirmed INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS investment_settings (
        id TEXT PRIMARY KEY, value TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS investment_records_time_idx ON investment_records(occurred_at)",
    "CREATE INDEX IF NOT EXISTS investment_revisions_record_idx ON investment_revisions(record_id,id)",
)


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, default=decimal_string)


def initialize(database):
    """Create investment tables without touching platform tables or user_version."""
    with database.lock, database.connection:
        for statement in SCHEMA:
            database.connection.execute(statement)


class InvestmentRepository:
    """Documented, JSON-payload repository; usable by imports and migration directly."""

    def __init__(self, database):
        self.database = database
        initialize(database)

    def _one(self, sql, params=()):
        with self.database.lock:
            row = self.database.connection.execute(sql, params).fetchone()
            return dict(row) if row else None

    def _all(self, sql, params=()):
        with self.database.lock:
            return [dict(row) for row in self.database.connection.execute(sql, params).fetchall()]

    def _write(self, sql, params=()):
        with self.database.lock, self.database.connection:
            return self.database.connection.execute(sql, params)

    @staticmethod
    def _record(row):
        return (
            {
                **json.loads(row["payload"]),
                "id": row["id"],
                "voided": bool(row["voided"]),
                "occurred_at": row["occurred_at"],
            }
            if row
            else None
        )

    def get_record(self, record_id):
        """Return a record including voided ones, or None."""
        return self._record(self._one("SELECT * FROM investment_records WHERE id=?", (record_id,)))

    def list_records(self, at=None, include_voided=False, *, start=None, end=None):
        """Return stable event order, preserving insertion order for equal times."""
        clauses, params = [], []
        if not include_voided:
            clauses.append("voided=0")
        if at is not None or end is not None:
            clauses.append("occurred_at<=?")
            params.append(timestamp(at if at is not None else end))
        if start is not None:
            clauses.append("occurred_at>=?")
            params.append(timestamp(start))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [
            self._record(row)
            for row in self._all(
                "SELECT * FROM investment_records" + where + " ORDER BY occurred_at,rowid", params
            )
        ]

    @staticmethod
    def _revision(connection, record, operation):
        connection.execute(
            "INSERT INTO investment_revisions(record_id,occurred_at,operation,payload) VALUES(?,?,?,?)",
            (record["id"], timestamp(), operation, _json(record)),
        )

    def add_record(self, record):
        """Insert one normalized record and its creation revision atomically."""
        value = normalize_record(record)
        with self.database.lock, self.database.connection:
            self.database.connection.execute(
                "INSERT INTO investment_records(id,payload,voided,occurred_at) VALUES(?,?,?,?)",
                (value["id"], _json(value), int(value["voided"]), value["occurred_at"]),
            )
            self._revision(self.database.connection, value, "create")
        return value

    create_record = add_record

    def update_record(self, record_id, changes):
        """Merge changes and retain the full revised record as an audit revision."""
        with self.database.lock, self.database.connection:
            previous = self.get_record(record_id)
            if previous is None:
                raise ValueError("投资记录不存在")
            value = normalize_record({**previous, **changes, "id": record_id})
            self.database.connection.execute(
                "UPDATE investment_records SET payload=?,voided=?,occurred_at=? WHERE id=?",
                (_json(value), int(value["voided"]), value["occurred_at"], record_id),
            )
            self._revision(self.database.connection, value, "update")
        return value

    def void_record(self, record_id):
        """Soft-delete; repeated voids are idempotent and do not create revisions."""
        with self.database.lock, self.database.connection:
            value = self.get_record(record_id)
            if value is None:
                raise ValueError("投资记录不存在")
            if value["voided"]:
                return value
            value["voided"] = True
            self.database.connection.execute(
                "UPDATE investment_records SET payload=?,voided=1 WHERE id=?",
                (_json(value), record_id),
            )
            self._revision(self.database.connection, value, "void")
        return value

    def revisions(self, record_id):
        """Chronological audit entries containing the full payload after each edit."""
        return [
            {**row, "payload": json.loads(row["payload"])}
            for row in self._all(
                "SELECT * FROM investment_revisions WHERE record_id=? ORDER BY id", (record_id,)
            )
        ]

    list_revisions = revisions

    def add_price(self, row=None, **values):
        """Upsert a source-time quote; accepts a dict or named fields."""
        row = {**(row or {}), **values}
        price = decimal_value(row.get("price"), "price")
        previous = decimal_value(row.get("previous_close"), "previous_close", nullable=True)
        if price < 0 or (previous is not None and previous < 0):
            raise ValueError("行情价格不能为负数")
        row = {
            "market": row["market"],
            "symbol": str(row["symbol"]),
            "occurred_at": timestamp(row["occurred_at"]),
            "price": decimal_string(price),
            "previous_close": decimal_string(previous),
            "source": row.get("source", "manual"),
            "market_date": row.get("market_date"),
            "kind": row.get("kind", "quote"),
        }
        self._write(
            "INSERT OR REPLACE INTO investment_prices(market,symbol,occurred_at,price,previous_close,source,market_date,kind) VALUES(?,?,?,?,?,?,?,?)",
            tuple(
                row[key]
                for key in (
                    "market",
                    "symbol",
                    "occurred_at",
                    "price",
                    "previous_close",
                    "source",
                    "market_date",
                    "kind",
                )
            ),
        )
        return row

    def save_prices(self, rows):
        """Validate then atomically upsert a batch of quotes."""
        with self.database.lock, self.database.connection:
            # add_price uses Database's reentrant lock; avoid nested transaction
            # contexts by validating in memory before executing the batch.
            prepared = []
            for row in rows:
                price = decimal_value(row["price"], "price")
                previous = decimal_value(row.get("previous_close"), "previous_close", nullable=True)
                if price < 0 or (previous is not None and previous < 0):
                    raise ValueError("行情价格不能为负数")
                prepared.append(
                    (
                        row["market"],
                        str(row["symbol"]),
                        timestamp(row["occurred_at"]),
                        decimal_string(price),
                        decimal_string(previous),
                        row.get("source", "manual"),
                        row.get("market_date"),
                        row.get("kind", "quote"),
                    )
                )
            self.database.connection.executemany(
                "INSERT OR REPLACE INTO investment_prices(market,symbol,occurred_at,price,previous_close,source,market_date,kind) VALUES(?,?,?,?,?,?,?,?)",
                prepared,
            )
        return len(prepared)

    def prices(self, market=None, symbol=None, start=None, end=None, kind=None):
        """List cached quotes by source time; optional inclusive bounds."""
        clauses, params = [], []
        for key, value in (("market", market), ("symbol", symbol), ("kind", kind)):
            if value is not None:
                clauses.append(key + "=?")
                params.append(value)
        for operator, bound in ((">=", start), ("<=", end)):
            if bound is not None:
                clauses.append("occurred_at" + operator + "?")
                params.append(timestamp(bound))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._all(
            "SELECT * FROM investment_prices" + where + " ORDER BY occurred_at,market,symbol,kind",
            params,
        )

    def latest_price(self, market, symbol, at=None, *, daily=False):
        """Latest available quote, never a future quote; manual/daily win ties."""
        restriction = " AND kind IN ('daily','manual')" if daily else ""
        return self._one(
            "SELECT * FROM investment_prices WHERE market=? AND symbol=? AND occurred_at<=?"
            + restriction
            + " ORDER BY occurred_at DESC, CASE kind WHEN 'manual' THEN 0 WHEN 'daily' THEN 1 ELSE 2 END LIMIT 1",
            (market, symbol, timestamp(at)),
        )

    def add_fx(self, row=None, **values):
        """Upsert CNY-per-unit currency FX at its real source timestamp."""
        row = {**(row or {}), **values}
        rate = decimal_value(row.get("rate"), "rate")
        if rate <= 0:
            raise ValueError("汇率必须大于零")
        value = {
            "currency": row["currency"].upper(),
            "occurred_at": timestamp(row["occurred_at"]),
            "rate": decimal_string(rate),
            "source": row.get("source", "manual"),
            "kind": row.get("kind", "quote"),
        }
        self._write(
            "INSERT OR REPLACE INTO investment_fx(currency,occurred_at,rate,source,kind) VALUES(?,?,?,?,?)",
            tuple(value[key] for key in ("currency", "occurred_at", "rate", "source", "kind")),
        )
        return value

    def save_fx(self, rows):
        """Atomically persist a validated FX batch."""
        prepared = []
        for row in rows:
            rate = decimal_value(row["rate"], "rate")
            if rate <= 0:
                raise ValueError("汇率必须大于零")
            prepared.append(
                (
                    row["currency"].upper(),
                    timestamp(row["occurred_at"]),
                    decimal_string(rate),
                    row.get("source", "manual"),
                    row.get("kind", "quote"),
                )
            )
        with self.database.lock, self.database.connection:
            self.database.connection.executemany(
                "INSERT OR REPLACE INTO investment_fx(currency,occurred_at,rate,source,kind) VALUES(?,?,?,?,?)",
                prepared,
            )
        return len(prepared)

    def fx(self, currency=None, start=None, end=None, kind=None):
        """List FX rows with optional source-time bounds and kind filter."""
        clauses, params = [], []
        for key, value in (("currency", currency), ("kind", kind)):
            if value is not None:
                clauses.append(key + "=?")
                params.append(value)
        for operator, bound in ((">=", start), ("<=", end)):
            if bound is not None:
                clauses.append("occurred_at" + operator + "?")
                params.append(timestamp(bound))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return self._all(
            "SELECT * FROM investment_fx" + where + " ORDER BY occurred_at,currency,kind", params
        )

    def latest_fx(self, currency, at=None, historical=False):
        """Previous-known FX including quotes sampled before the event cutoff."""
        if currency == "CNY":
            return {
                "currency": "CNY",
                "occurred_at": timestamp(at),
                "rate": "1",
                "source": "identity",
                "kind": "historical",
            }
        # A sampled quote is historical evidence at/after its source time too.
        # The cutoff, never the lookup's wall clock, prevents future FX leakage.
        restriction = ""
        return self._one(
            "SELECT * FROM investment_fx WHERE currency=? AND occurred_at<=?"
            + restriction
            + " ORDER BY occurred_at DESC,kind LIMIT 1",
            (currency, timestamp(at)),
        )

    def save_snapshot(self, row=None, kind=None, payload=None, *, occurred_at=None):
        """Upsert snapshot; accepts a row dict or (occurred_at, kind, payload)."""
        if isinstance(row, dict):
            occurred_at, kind = row["occurred_at"], row.get("kind", kind or "daily")
            payload = row.get("payload", row)
        elif row is not None:
            occurred_at = row
        occurred_at = timestamp(occurred_at)
        kind = kind or "daily"
        self._write(
            "INSERT OR REPLACE INTO investment_snapshots(occurred_at,kind,payload) VALUES(?,?,?)",
            (occurred_at, kind, _json(payload)),
        )
        return {"occurred_at": occurred_at, "kind": kind, "payload": payload}

    add_snapshot = save_snapshot

    def snapshots(self, start=None, end=None, kind=None):
        """Return timestamp/kind/payload wrappers in ascending order."""
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind=?")
            params.append(kind)
        for operator, bound in ((">=", start), ("<=", end)):
            if bound is not None:
                clauses.append("occurred_at" + operator + "?")
                params.append(timestamp(bound))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return [
            {**row, "payload": json.loads(row["payload"])}
            for row in self._all(
                "SELECT * FROM investment_snapshots" + where + " ORDER BY occurred_at,kind", params
            )
        ]

    def clear_snapshots(self, kind=None):
        """Invalidate derived snapshots after ledger or source-data edits."""
        self._write(
            "DELETE FROM investment_snapshots" + (" WHERE kind=?" if kind else ""),
            (kind,) if kind else (),
        )

    def prune_minutes(self, before):
        """Remove minute snapshots/quotes/FX older than the supplied cutoff."""
        with self.database.lock, self.database.connection:
            for table in ("investment_snapshots", "investment_prices", "investment_fx"):
                self.database.connection.execute(
                    f"DELETE FROM {table} WHERE kind='minute' AND occurred_at<?",
                    (timestamp(before),),
                )

    def save_import(self, import_id=None, file_hash=None, payload=None, confirmed=False, **values):
        """Store an import preview/provenance wrapper; accept dict or named fields."""
        if isinstance(import_id, dict):
            values = {**import_id, **values}
            import_id = values.get("id")
            file_hash, payload = values.get("hash", file_hash), values.get("payload", payload)
            confirmed = values.get("confirmed", confirmed)
        import_id = import_id or values.get("id") or uuid.uuid4().hex
        file_hash = file_hash or values.get("hash", "")
        self._write(
            "INSERT OR REPLACE INTO investment_imports(id,hash,payload,confirmed) VALUES(?,?,?,?)",
            (import_id, file_hash, _json(payload or {}), int(confirmed)),
        )
        return self.get_import(import_id)

    add_import = save_import

    def get_import(self, import_id):
        """Get the import wrapper or None."""
        row = self._one("SELECT * FROM investment_imports WHERE id=?", (import_id,))
        return (
            {**row, "payload": json.loads(row["payload"]), "confirmed": bool(row["confirmed"])}
            if row
            else None
        )

    def find_import(self, file_hash, confirmed=None):
        """Find the most recently inserted preview/confirmation of an exact hash."""
        restriction = " AND confirmed=?" if confirmed is not None else ""
        row = self._one(
            "SELECT id FROM investment_imports WHERE hash=?"
            + restriction
            + " ORDER BY rowid DESC LIMIT 1",
            (file_hash, int(confirmed)) if confirmed is not None else (file_hash,),
        )
        return self.get_import(row["id"]) if row else None

    def list_imports(self):
        return [
            self.get_import(row["id"])
            for row in self._all("SELECT id FROM investment_imports ORDER BY rowid DESC")
        ]

    def confirm_import(self, import_id, payload=None):
        """Mark an existing import confirmed, preserving payload unless supplied."""
        value = self.get_import(import_id)
        if value is None:
            raise ValueError("导入预览不存在")
        return self.save_import(
            import_id, value["hash"], value["payload"] if payload is None else payload, True
        )

    def get_setting(self, key, default=None):
        row = self._one("SELECT value FROM investment_settings WHERE id=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        self._write(
            "INSERT OR REPLACE INTO investment_settings(id,value) VALUES(?,?)", (key, _json(value))
        )


Repository = InvestmentRepository
