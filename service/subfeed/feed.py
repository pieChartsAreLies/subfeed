"""Builds the /api/feed payload in one query and one pass."""
from __future__ import annotations

import sqlite3

from . import db
from . import watch

_SELECT_FROM = """
SELECT v.id, v.channel_id, c.name AS channel_title, c.category_id, c.hidden, c.subscribed,
       v.title, v.published_at, v.duration_s, v.thumbnail_url, v.eligible, v.unavailable_reason,
       v.discovered_at, v.evicted, v.live_status,
       COALESCE(w.position_s, 0) AS position_s, COALESCE(w.max_position_s, 0) AS max_position_s,
       COALESCE(w.watched, 0) AS watched, w.updated_at AS watch_updated_at,
       w.played_at, w.last_played_at,
       s.state AS save_state
FROM videos v
JOIN channels c ON c.id = v.channel_id
LEFT JOIN watch w ON w.video_id = v.id
LEFT JOIN saves s ON s.video_id = v.id
"""
# eligible or saved videos that are not evicted, plus anything that was ever played
ADMIT = "((v.evicted = 0 AND (v.eligible = 1 OR s.state IS NOT NULL)) OR w.played_at IS NOT NULL)"
_QUERY = _SELECT_FROM + "WHERE " + ADMIT + "\nORDER BY v.published_at DESC, v.id\n"


HISTORY_LIMIT = 200


def _state(r, now) -> str:
    """Playback state from furthest content position (max_position_s); position_s is resume only."""
    return watch.classify(
        bool(r["watched"]), r["max_position_s"], r["duration_s"],
        r["live_status"] in watch.LIVE_STATUSES, r["last_played_at"], now,
    )


def channel_videos(conn: sqlite3.Connection, channel_id: str, offset: int, limit: int) -> tuple[int, list]:
    """(total, page of video dicts with "state") for one channel, newest upload first."""
    total = conn.execute(
        "SELECT COUNT(*) FROM videos v LEFT JOIN watch w ON w.video_id = v.id "
        "LEFT JOIN saves s ON s.video_id = v.id WHERE v.channel_id = ? AND " + ADMIT,
        (channel_id,),
    ).fetchone()[0]
    rows = conn.execute(
        _SELECT_FROM + "WHERE v.channel_id = ? AND " + ADMIT +
        "\nORDER BY v.published_at DESC, v.id LIMIT ? OFFSET ?",
        (channel_id, limit, offset),
    ).fetchall()
    now = db.utcnow()
    return total, [{**_video(r), "state": _state(r, now)} for r in rows]


def _order(rows: list) -> list:
    """Unwatched newest first, then watched (dimmed) newest first. `rows` arrives newest-first."""
    return [r for r in rows if not r["watched"]] + [r for r in rows if r["watched"]]


def build_feed(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(_QUERY).fetchall()
    now = db.utcnow()
    states = {r["id"]: _state(r, now) for r in rows}
    cats = conn.execute(
        "SELECT id, name, approved FROM categories ORDER BY position, id"
    ).fetchall()
    seen = {r["tab"]: r["seen_at"] for r in conn.execute("SELECT tab, seen_at FROM tab_seen")}
    approved = {c["id"]: c for c in cats if c["approved"]}

    in_feed = [
        r for r in rows
        if r["eligible"] and not r["evicted"] and not r["hidden"] and r["subscribed"]
        and states[r["id"]] != "in_progress"
    ]
    # a non-failed save shows in Saved even when its channel is hidden or unsubscribed
    saved = [
        r for r in rows
        if not r["evicted"] and r["save_state"] is not None and r["save_state"] != "failed"
    ]
    # Continue and History take any played row, whatever its channel state or eviction
    by_played = sorted(rows, key=lambda r: r["last_played_at"] or "", reverse=True)
    continue_rows = [r for r in by_played if states[r["id"]] == "in_progress"]
    history = [r for r in by_played if r["played_at"]][:HISTORY_LIMIT]

    by_cat: dict[int, list] = {cid: [] for cid in approved}
    other: list = []
    for r in in_feed:
        if r["category_id"] in approved:
            by_cat[r["category_id"]].append(r)
        else:
            other.append(r)

    tab_defs = [("all", "All", "all", in_feed), ("continue", "Continue Watching", "continue", continue_rows)]
    tab_defs += [(f"cat-{cid}", approved[cid]["name"], "category", by_cat[cid]) for cid in approved]
    tab_defs += [("other", "Other", "other", other), ("saved", "Saved", "saved", saved),
                 ("history", "History", "history", history)]

    videos: dict[str, dict] = {}
    tabs = []
    for tab_id, name, kind, members in tab_defs:
        ordered = members if kind in ("continue", "history") else _order(members)
        seen_at = seen.get(tab_id, "")
        new_count = 0 if kind in ("continue", "history") else sum(
            1 for r in members if not r["watched"] and r["discovered_at"] > seen_at)
        ids = [r["id"] for r in ordered]
        tabs.append({
            "id": tab_id, "name": name, "kind": kind, "new_count": new_count,
            "video_ids": ids, "continue_ids": [],
        })
        for r in ordered:
            if r["id"] not in videos:
                videos[r["id"]] = _video(r)
    return {"tabs": tabs, "videos": videos, "generated_at": db.iso(db.utcnow())}


def _video(r) -> dict:
    return {
        "id": r["id"],
        "channel_id": r["channel_id"],
        "channel_title": r["channel_title"],
        "category_id": r["category_id"],
        "title": r["title"],
        "published_at": r["published_at"],
        "duration_s": r["duration_s"],
        "thumbnail_url": r["thumbnail_url"] or f"https://i.ytimg.com/vi/{r['id']}/hqdefault.jpg",
        "position_s": r["position_s"],
        "max_position_s": r["max_position_s"],
        "watched": bool(r["watched"]),
        "save_state": r["save_state"],
        "unavailable_reason": r["unavailable_reason"],
    }
