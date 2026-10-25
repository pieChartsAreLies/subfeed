"""Polling: list a channel's Videos tab, enrich unseen entries, apply eligibility and retention."""
from __future__ import annotations

import random
import sqlite3
from datetime import datetime, timedelta

from . import breaker, db, eligibility, ytdl
from .watch import IN_PROGRESS_MIN_S

RETAIN_PER_CHANNEL = 25
RETAIN_HIDDEN = 100               # hidden channels are a history library: keep more
BACKFILL_LIMIT = 15
ACTIVE_WINDOW = timedelta(days=14)
ACTIVE_INTERVAL_S = 3600          # uploaded in the last 14 days: hourly
QUIET_INTERVAL_S = 86400          # otherwise daily
ERROR_BASE_S = 900                # first failure waits 15 min, doubling
BACKOFF_CAP_S = 86400
JITTER = 0.2
GIVE_UP_RECHECK = timedelta(days=3)
TOMBSTONE_KEEP = timedelta(days=90)


def next_poll_delay(last_upload_at: str | None, now: datetime, error_count: int = 0,
                    rng: random.Random | None = None) -> float:
    """Seconds until the next poll: hourly if the channel uploaded within 14 days else daily,
    doubling from 15 min per consecutive failure (cap 24 h), then +/-20 % jitter."""
    rng = rng or random
    if error_count > 0:
        base = min(BACKOFF_CAP_S, ERROR_BASE_S * (2 ** (error_count - 1)))
    elif last_upload_at and now - db.parse_iso(last_upload_at) <= ACTIVE_WINDOW:
        base = ACTIVE_INTERVAL_S
    else:
        base = QUIET_INTERVAL_S
    return base * (1 + rng.uniform(-JITTER, JITTER))


def _flat_entry(e: dict) -> dict:
    return {k: e.get(k) for k in ("id", "title", "duration", "live_status", "availability",
                                  "url", "timestamp", "release_timestamp")}


def _recheck_at(entry: dict, reason: str, now: datetime) -> str | None:
    if reason not in eligibility.RECHECK_REASONS:
        return None
    rel = entry.get("release_timestamp")
    if rel:
        release = datetime.fromtimestamp(int(rel), now.tzinfo)
        if now - release > GIVE_UP_RECHECK:
            return None
        if release > now:
            return db.iso(release + timedelta(minutes=5))
    return db.iso(now + timedelta(hours=1))


def _store(conn: sqlite3.Connection, channel_id: str, entry: dict, reason: str | None,
           now: datetime, thumbnail_url: str | None = None) -> bool:
    """Insert or update one video row. Returns True when it is a newly eligible video."""
    vid = entry["id"]
    published = ytdl.published_at(entry) or db.iso_s(now)
    recheck = _recheck_at(entry, reason, now) if reason else None
    existing = conn.execute("SELECT eligible, evicted FROM videos WHERE id=?", (vid,)).fetchone()
    with db.tx(conn):
        if existing is None:
            cur = conn.execute(
                """INSERT OR IGNORE INTO videos(id, channel_id, title, published_at, duration_s, thumbnail_url,
                       eligible, discovered_at, live_status, availability, ineligible_reason, recheck_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vid, channel_id, entry.get("title") or vid, published, entry.get("duration"),
                 thumbnail_url, 0 if reason else 1, db.iso(now), entry.get("live_status"),
                 entry.get("availability"), reason, recheck),
            )
            return bool(cur.rowcount) and reason is None
        conn.execute(
            """UPDATE videos SET title=?, published_at=?, duration_s=?, thumbnail_url=COALESCE(?, thumbnail_url),
                   eligible=?, live_status=?, availability=?, ineligible_reason=?, recheck_at=? WHERE id=?""",
            (entry.get("title") or vid, published, entry.get("duration"), thumbnail_url,
             0 if reason else 1, entry.get("live_status"), entry.get("availability"), reason, recheck, vid),
        )
        return reason is None and not existing["eligible"]


def retention_for(conn: sqlite3.Connection, channel_id: str) -> int:
    row = conn.execute("SELECT hidden FROM channels WHERE id=?", (channel_id,)).fetchone()
    return RETAIN_HIDDEN if row and row["hidden"] else RETAIN_PER_CHANNEL


def enforce_retention(conn: sqlite3.Connection, channel_id: str, keep: int = RETAIN_PER_CHANNEL,
                      now: datetime | None = None, listed: set[str] | None = None) -> int:
    """Keep the newest `keep` non-saved eligible videos. Over the limit, evict watched ones
    first (oldest first), then the oldest unwatched. Never evicted: videos with any save row
    (they sit outside the count), played videos (any watch row with played_at) and in-progress videos (position > 30 s, unwatched).
    Evicted rows stay as tombstones (with `evicted_at`) so the next poll does not re-add them.
    Tombstones and ineligible rows are pruned 90 days after eviction/discovery, and never
    while the id is still in `listed` (the channel's current newest-N listing)."""
    now = now or db.utcnow()
    now_iso = db.iso(now)
    listed = listed or set()
    with db.tx(conn):
        rows = conn.execute(
            """SELECT v.id, COALESCE(w.watched, 0) AS watched,
                      (COALESCE(w.watched, 0) = 0 AND COALESCE(w.max_position_s, 0) > ?) AS in_progress
               FROM videos v LEFT JOIN watch w ON w.video_id = v.id
               WHERE v.channel_id=? AND v.eligible=1 AND v.evicted=0
                 AND NOT EXISTS (SELECT 1 FROM saves s WHERE s.video_id = v.id)
                 AND NOT EXISTS (SELECT 1 FROM watch w2 WHERE w2.video_id = v.id AND w2.played_at IS NOT NULL)
               ORDER BY v.published_at ASC, v.id""",
            (IN_PROGRESS_MIN_S, channel_id),
        ).fetchall()
        excess = len(rows) - keep
        evicted = 0
        if excess > 0:
            cands = [r for r in rows if not r["in_progress"]]
            order = [r["id"] for r in cands if r["watched"]] + [r["id"] for r in cands if not r["watched"]]
            for vid in order[:excess]:
                conn.execute("UPDATE videos SET evicted=1, evicted_at=? WHERE id=?", (now_iso, vid))
                evicted += 1
        cutoff = db.iso(now - TOMBSTONE_KEEP)
        stale = conn.execute(
            """SELECT id FROM videos WHERE channel_id=? AND recheck_at IS NULL
                 AND id NOT IN (SELECT video_id FROM saves)
                 AND ((evicted=1 AND COALESCE(evicted_at, discovered_at) < ?)
                      OR (evicted=0 AND eligible=0 AND discovered_at < ?))""",
            (channel_id, cutoff, cutoff),
        ).fetchall()
        for r in stale:
            if r["id"] not in listed:
                conn.execute("DELETE FROM videos WHERE id=?", (r["id"],))
    return evicted


def _schedule(conn: sqlite3.Connection, channel_id: str, now: datetime, error: str | None,
              rng: random.Random | None, until: str | None = None) -> None:
    ch = conn.execute("SELECT last_upload_at, error_count FROM channels WHERE id=?", (channel_id,)).fetchone()
    errors = (ch["error_count"] + 1) if error else 0
    if until:
        nxt = until
    else:
        nxt = db.iso(now + timedelta(seconds=next_poll_delay(ch["last_upload_at"], now, errors, rng)))
    with db.tx(conn):
        conn.execute(
            "UPDATE channels SET next_poll_at=?, error_count=?, last_error=? WHERE id=?",
            (nxt, errors if not until else ch["error_count"], error, channel_id),
        )


def poll_channel(conn: sqlite3.Connection, channel_id: str, limit: int = BACKFILL_LIMIT,
                 now: datetime | None = None, rng: random.Random | None = None) -> int:
    """Poll one channel. Returns the number of newly eligible videos. Failures never raise:
    they are recorded on the channel and back off exponentially; a bot-check also trips the
    global breaker and parks the channel until it closes."""
    now = now or db.utcnow()
    if not conn.execute("SELECT 1 FROM channels WHERE id=?", (channel_id,)).fetchone():
        return 0
    if breaker.is_open(conn, now):
        _schedule(conn, channel_id, now, None, rng, until=db.get_kv(conn, "breaker_until"))
        return 0

    meta: dict = {}
    try:
        entries = [_flat_entry(e) for e in ytdl.channel_uploads(channel_id, limit, meta)]
    except ytdl.BotCheckError as exc:
        until = breaker.trip(conn, str(exc), now)
        _schedule(conn, channel_id, now, None, rng, until=db.iso(until))
        return 0
    except Exception as exc:  # noqa: BLE001
        _schedule(conn, channel_id, now, f"{type(exc).__name__}: {exc}"[:300], rng)
        return 0

    known = {r["id"] for r in conn.execute(
        "SELECT id FROM videos WHERE id IN (%s)" % ",".join("?" * len(entries)),
        [e["id"] for e in entries])} if entries else set()
    due = [r["id"] for r in conn.execute(
        "SELECT id FROM videos WHERE channel_id=? AND recheck_at IS NOT NULL AND recheck_at<=? AND evicted=0",
        (channel_id, db.iso(now)))]

    new_count = 0
    failures = 0
    stored = 0
    tripped_until = None
    due_set = set(due)
    listed = {e["id"] for e in entries}
    tasks: list[tuple[dict, bool]] = []
    for e in entries:
        if e["id"] in due_set:
            tasks.append((e, True))
        elif e["id"] not in known:
            tasks.append((e, False))
    tasks += [({"id": v}, True) for v in due if v not in listed]

    for e, is_recheck in tasks:
        if breaker.is_open(conn, now):  # another channel's poll may have tripped it meanwhile
            tripped_until = db.parse_iso(db.get_kv(conn, "breaker_until"))
            break
        if not is_recheck:
            reason = eligibility.ineligible_reason(e, flat=True)
            if reason is not None:
                # the listing alone settles it (Short, live, upcoming, members-only, ...)
                new_count += _store(conn, channel_id, e, reason, now)
                stored += 1
                continue
        try:
            details = ytdl.video_details(e["id"])
        except ytdl.BotCheckError as exc:
            tripped_until = breaker.trip(conn, str(exc), now)
            break
        except Exception:  # noqa: BLE001
            failures += 1
            continue
        merged = {**e, **{k: v for k, v in details.items() if v is not None}}
        reason = eligibility.ineligible_reason(merged)
        new_count += _store(conn, channel_id, merged, reason, now, details.get("thumbnail_url"))
        stored += 1

    if tripped_until:
        _schedule(conn, channel_id, now, None, rng, until=db.iso(tripped_until))
        return new_count

    enforce_retention(conn, channel_id, keep=retention_for(conn, channel_id), now=now, listed=listed)
    last_upload = conn.execute(
        "SELECT MAX(published_at) FROM videos WHERE channel_id=? AND eligible=1 AND evicted=0", (channel_id,)
    ).fetchone()[0]
    with db.tx(conn):
        conn.execute(
            """UPDATE channels SET last_polled_at=?, last_upload_at=COALESCE(?, last_upload_at),
                   name=COALESCE(NULLIF(?, ''), name), description=COALESCE(?, description) WHERE id=?""",
            (db.iso(now), last_upload, meta.get("name"), meta.get("description"), channel_id),
        )
        db.set_kv(conn, "last_poll", db.iso(now))
    # every enrichment failing is a failed poll; some failing just retries those next time
    failed = failures > 0 and stored == 0
    _schedule(conn, channel_id, now, "video details failed" if failed else None, rng)
    if not failed and (entries or stored):
        breaker.note_success(conn, now)
    return new_count
