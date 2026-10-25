"""Health fields and the hourly canary.

The canary extracts a known public video (Fireship MtxP2pyCvYA) and checks a playable
video+audio pair comes back. It detects yt-dlp/YouTube breakage; it never upgrades yt-dlp.
A bot-check error trips the breaker at once; two failures in a row also trip it."""
from __future__ import annotations

import logging
import sqlite3
import threading
import time

from . import breaker, db, playback, pool, saving, storage, ytdl

log = logging.getLogger("uvicorn.error")
CANARY_VIDEO = "MtxP2pyCvYA"
CANARY_EVERY_S = 3600
FIRST_DELAY_S = 90


_mount_cache: dict = {}
_mount_lock = threading.Lock()
MOUNT_CACHE_S = 10.0


def cached_mount(path: str):
    """check_mount, remembered for 10 s so polling /api/health cannot stack up probe threads
    behind a hung NFS server."""
    now = time.monotonic()
    with _mount_lock:
        hit = _mount_cache.get(path)
        if hit and now - hit[0] < MOUNT_CACHE_S:
            return hit[1]
    result = storage.check_mount(path)
    with _mount_lock:
        _mount_cache[path] = (time.monotonic(), result)
    return result


def health_fields(conn: sqlite3.Connection, settings) -> dict:
    mount = cached_mount(settings.saved_dir)
    canary_ok = db.get_kv(conn, "canary_ok")
    return {
        "last_canary": db.get_kv(conn, "last_canary"),
        "canary_ok": None if canary_ok is None else canary_ok == "1",
        "saved_bytes": saving.used_bytes(conn),
        "saved_quota_bytes": settings.saved_quota_bytes,
        "nas_mounted": mount is not None,
        "jobs": pool.get_pool().running_jobs(),    # heartbeat: priority and age of each running job
    }


def run_canary(conn: sqlite3.Connection, extract=None) -> bool:
    """One canary pass. Skipped (returns True, nothing recorded) while the breaker is open."""
    if breaker.is_open(conn):
        return True
    extract = extract or (lambda vid: ytdl.play_info(vid, priority=pool.DISCOVERY))
    ok, reason = False, ""
    try:
        info = extract(CANARY_VIDEO)
        ok = playback.select(info.get("formats") or [], ["avc1", "vp9"], 1080) is not None
        reason = "" if ok else "no playable video+audio pair"
    except ytdl.BotCheckError as exc:
        breaker.trip(conn, f"canary: {exc}")
        reason = str(exc)
    except Exception as exc:  # noqa: BLE001
        reason = str(exc)
    breaker.note_canary(conn, ok)
    with db.tx(conn):
        db.set_kv(conn, "last_canary", db.iso(db.utcnow()))
        db.set_kv(conn, "canary_ok", "1" if ok else "0")
        db.set_kv(conn, "canary_reason", reason[:200])
    if not ok:
        log.warning("canary failed: %s", reason)
    return ok


class CanaryThread:
    def __init__(self, db_path: str, delay_s: float = FIRST_DELAY_S, every_s: float = CANARY_EVERY_S):
        self.db_path, self.delay_s, self.every_s = db_path, delay_s, every_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="subfeed-canary", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        wait = self.delay_s
        while not self._stop.wait(wait):
            try:
                conn = db.connect(self.db_path)
                try:
                    run_canary(conn)
                finally:
                    conn.close()
            except Exception:  # noqa: BLE001
                log.exception("canary: loop error")
            wait = self.every_s
