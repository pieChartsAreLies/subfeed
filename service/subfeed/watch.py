"""Progress and watched rules."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Literal

from . import db

WATCHED_FRACTION = 0.95
TAIL_S = 75.0
TAIL_MIN_DURATION_S = 300.0
CONTINUE_DAYS = 14
IN_PROGRESS_MIN_S = 30.0  # furthest content position (max_position_s) must be strictly greater than this, everywhere; raw position_s is only the resume point
LIVE_STATUSES = ("is_live", "is_upcoming", "post_live")


def is_watched(content_pos_s: float, content_duration_s: float, ended: bool) -> bool:
    """Positions are content positions (sponsor segments already removed from both)."""
    if ended:
        return True
    if content_duration_s <= 0:
        return False
    if content_pos_s <= IN_PROGRESS_MIN_S:
        return False
    if content_pos_s >= WATCHED_FRACTION * content_duration_s:
        return True
    return content_duration_s >= TAIL_MIN_DURATION_S and content_duration_s - content_pos_s <= TAIL_S


def classify(
    watched: bool, position_s: float, duration_s: float | None, live: bool,
    last_played_at: str | None, now: datetime,
) -> Literal["fresh", "in_progress", "watched", "stale"]:
    """stale = played, unfinished, last played more than CONTINUE_DAYS ago."""
    if watched:
        return "watched"
    if live or not duration_s or position_s <= IN_PROGRESS_MIN_S or not last_played_at:
        return "fresh"
    if now - db.parse_iso(last_played_at) > timedelta(days=CONTINUE_DAYS):
        return "stale"
    return "in_progress"


def record_progress(
    conn: sqlite3.Connection, video_id: str, position_s: float, duration_s: float,
    sponsor_s: float, ended: bool = False, content_position_s: float | None = None,
) -> bool:
    """Returns watched. position_s is the raw player position (resume point). The watched rule
    runs on content position: content_position_s when the player sends it, else position_s.
    max_position_s holds the furthest content position and only ever grows (a retried older post
    cannot move it back); watched never flips to false here (only an explicit mark-unwatched
    does); sponsor_s freezes at the first non-zero value posted. Duration comes from yt-dlp when
    we have it. played_at (once) and last_played_at (every post) are set only when the content
    position is past IN_PROGRESS_MIN_S."""
    now = db.iso(db.utcnow())
    content_pos = float(position_s if content_position_s is None else content_position_s)
    with db.tx(conn):
        vid = conn.execute("SELECT duration_s, live_status FROM videos WHERE id=?", (video_id,)).fetchone()
        old = conn.execute("SELECT * FROM watch WHERE video_id=?", (video_id,)).fetchone()
        dur = float(vid["duration_s"]) if vid and vid["duration_s"] else float(duration_s)
        max_pos = max(content_pos, old["max_position_s"] if old else 0.0)
        frozen = old["sponsor_s"] if old and old["sponsor_s"] else None
        sponsor = frozen if frozen is not None else float(sponsor_s)
        live = bool(vid and vid["live_status"] in LIVE_STATUSES)
        # live streams and premieres are never auto-watched or in progress (explicit marks still work)
        watched = bool(old and old["watched"]) or (not live and is_watched(max_pos, dur - sponsor, ended))
        played = content_pos > IN_PROGRESS_MIN_S and not live
        played_at = (old["played_at"] if old else None) or (now if played else None)
        last_played_at = now if played else (old["last_played_at"] if old else None)
        conn.execute(
            """INSERT INTO watch(video_id, position_s, max_position_s, duration_s, sponsor_s, watched,
                                 updated_at, played_at, last_played_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET position_s=excluded.position_s,
                 max_position_s=excluded.max_position_s, duration_s=excluded.duration_s,
                 sponsor_s=excluded.sponsor_s, watched=excluded.watched, updated_at=excluded.updated_at,
                 played_at=excluded.played_at, last_played_at=excluded.last_played_at""",
            (video_id, position_s, max_pos, dur, sponsor, int(watched), now, played_at, last_played_at),
        )
    return watched


def set_watched(conn: sqlite3.Connection, video_id: str, watched: bool) -> None:
    """Explicit override. Unwatching also clears progress, or the next progress post
    would immediately re-mark the video watched."""
    now = db.iso(db.utcnow())
    with db.tx(conn):
        if watched:
            conn.execute(
                """INSERT INTO watch(video_id, watched, updated_at) VALUES(?,1,?)
                   ON CONFLICT(video_id) DO UPDATE SET watched=1, updated_at=excluded.updated_at""",
                (video_id, now),
            )
        else:
            conn.execute(
                """INSERT INTO watch(video_id, watched, updated_at) VALUES(?,0,?)
                   ON CONFLICT(video_id) DO UPDATE SET watched=0, position_s=0, max_position_s=0,
                     sponsor_s=NULL, updated_at=excluded.updated_at""",
                (video_id, now),
            )
