"""SQLite access: WAL, foreign keys, versioned migrations from schema.sql."""
from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """Microsecond ISO-8601 UTC; fixed width so string comparison orders correctly."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def iso_s(dt: datetime) -> str:
    """Second-precision ISO-8601 UTC (video published_at)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _migrations() -> list[tuple[int, str]]:
    text = resources.files("subfeed").joinpath("schema.sql").read_text()
    parts = re.split(r"^-- migration (\d+)\s*$", text, flags=re.M)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    cur = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
    for version, sql in _migrations():
        if version > cur:
            conn.execute("BEGIN IMMEDIATE")
            try:
                # re-check inside the lock: another process may have migrated
                now = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] or 0
                if version > now:
                    for stmt in _split(sql):
                        conn.execute(stmt)
                    conn.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    return conn


def _split(sql: str) -> list[str]:
    return [s.strip() for s in sql.split(";\n") if s.strip().rstrip(";")]


@contextmanager
def tx(conn: sqlite3.Connection):
    """Short write transaction (BEGIN IMMEDIATE ... COMMIT). Connections are autocommit."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def get_kv(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_kv(conn: sqlite3.Connection, key: str, value: str | None) -> None:
    conn.execute(
        "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
