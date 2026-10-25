"""SponsorBlock skip segments (category `sponsor` only) via the privacy-preserving hash-prefix
lookup. Any failure returns [] so playback never waits on or breaks because of it. Answers are
cached in kv for a day; a failed lookup is cached for 5 minutes only; an empty answer for a day."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

import httpx

from . import db

API = "https://sponsor.ajay.app/api/skipSegments"
TTL_S = 24 * 3600
FAIL_TTL_S = 300   # a failed lookup is not retried for 5 minutes (an outage must not cost 4 s per /play)


def parse(payload, video_id: str, duration_s: float | None) -> list[dict]:
    out: list[dict] = []
    for entry in payload if isinstance(payload, list) else []:
        if not isinstance(entry, dict) or entry.get("videoID") != video_id:
            continue
        for seg in entry.get("segments") or []:
            if seg.get("category") != "sponsor" or seg.get("actionType", "skip") != "skip":
                continue
            try:
                start, end = float(seg["segment"][0]), float(seg["segment"][1])
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            if start < 0 or end <= start:
                continue
            if duration_s and end > duration_s + 1:   # outside [0, duration]
                continue
            out.append({"start": start, "end": end})
    return sorted(out, key=lambda s: s["start"])


def fetch(video_id: str, duration_s: float | None, timeout: float = 4.0) -> list[dict] | None:
    """None means the lookup failed (do not cache); [] means there are no segments."""
    prefix = hashlib.sha256(video_id.encode()).hexdigest()[:4]
    try:
        r = httpx.get(f"{API}/{prefix}", params={"categories": '["sponsor"]', "actionTypes": '["skip"]'},
                      timeout=timeout)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        return parse(r.json(), video_id, duration_s)
    except Exception:  # noqa: BLE001
        return None


def segments(conn: sqlite3.Connection, video_id: str, duration_s: float | None) -> list[dict]:
    key = f"sb:{video_id}"
    cached = db.get_kv(conn, key)
    if cached:
        try:
            c = json.loads(cached)
            if time.time() - c["at"] < (FAIL_TTL_S if c.get("failed") else TTL_S):
                return c["segments"]
        except Exception:  # noqa: BLE001
            pass
    segs = fetch(video_id, duration_s)
    with db.tx(conn):
        db.set_kv(conn, key, json.dumps({"at": time.time(), "segments": segs or [], "failed": segs is None}))
    return segs or []
