"""Global circuit breaker for background YouTube work.

A bot-check or sign-in-to-confirm error, or two consecutive canary failures, pauses all
background extraction (polling, saving) for 6 h, doubling on repeat, capped at 24 h.
User-initiated /play is not gated by this; it still tries once.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import db

BASE_HOURS = 6
CAP_HOURS = 24
LIMITED_SENTENCE = "YouTube is limiting us right now. Your saved videos still work."


def is_open(conn: sqlite3.Connection, now: datetime | None = None) -> bool:
    until = db.get_kv(conn, "breaker_until")
    if not until:
        return False
    return (now or db.utcnow()) < db.parse_iso(until)


def status(conn: sqlite3.Connection, now: datetime | None = None) -> dict:
    open_ = is_open(conn, now)
    return {
        "limited": open_,
        "until": db.get_kv(conn, "breaker_until") if open_ else None,
        "message": LIMITED_SENTENCE if open_ else None,
    }


def trip(conn: sqlite3.Connection, reason: str, now: datetime | None = None) -> datetime:
    """Open the breaker. Returns the time it will close. Failures that arrive while it is
    already open (queued work finishing) do not escalate it."""
    now = now or db.utcnow()
    if is_open(conn, now):
        return db.parse_iso(db.get_kv(conn, "breaker_until"))
    level = int(db.get_kv(conn, "breaker_level", "0") or 0)
    hours = min(CAP_HOURS, BASE_HOURS * (2 ** level))
    until = now + timedelta(hours=hours)
    with db.tx(conn):
        db.set_kv(conn, "breaker_until", db.iso(until))
        db.set_kv(conn, "breaker_level", str(level + 1))
        db.set_kv(conn, "breaker_reason", reason[:200])
        db.set_kv(conn, "canary_fail_streak", "0")
    return until


def note_success(conn: sqlite3.Connection, now: datetime | None = None) -> None:
    """A background extraction worked after the pause ended: forget the escalation."""
    if not is_open(conn, now) and db.get_kv(conn, "breaker_level", "0") != "0":
        with db.tx(conn):
            db.set_kv(conn, "breaker_level", "0")


def note_canary(conn: sqlite3.Connection, ok: bool, now: datetime | None = None) -> None:
    if ok:
        with db.tx(conn):
            db.set_kv(conn, "canary_fail_streak", "0")
        note_success(conn, now)
        return
    streak = int(db.get_kv(conn, "canary_fail_streak", "0") or 0) + 1
    with db.tx(conn):
        db.set_kv(conn, "canary_fail_streak", str(streak))
    if streak >= 2:
        trip(conn, "two consecutive canary failures", now)
