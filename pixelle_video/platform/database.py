"""SQLite repositories for the shared creation platform."""

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        with self.connection:
            self.connection.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                PRAGMA busy_timeout=5000;
                CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS works (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL DEFAULT 'video', title TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    manifest TEXT, checkpoint TEXT, summary TEXT NOT NULL, search_text TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, work_id TEXT, batch_id TEXT,
                    status TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    params TEXT NOT NULL, result TEXT, error TEXT, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, cancel_requested INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, object_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
                    name TEXT NOT NULL, size INTEGER NOT NULL, content_type TEXT NOT NULL,
                    metadata TEXT NOT NULL, confirmed INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
                CREATE INDEX IF NOT EXISTS works_date_idx ON works(created_at);
                PRAGMA user_version=1;
            """)

    def execute(self, sql, params=()):
        with self.lock, self.connection:
            return self.connection.execute(sql, params)

    def one(self, sql, params=()):
        with self.lock:
            row = self.connection.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    def get_setting(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE id=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        self.execute(
            "INSERT OR REPLACE INTO settings(id,value) VALUES(?,?)",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def close(self):
        self.connection.close()
