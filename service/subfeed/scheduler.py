"""Background polling. Starts with the app; SUBFEED_SCHEDULER=0 disables it.

Each tick picks channels whose persisted next_poll_at has passed (never-polled channels first)
and polls up to two at once. The process pool caps concurrent yt-dlp calls at 2 regardless.
Nothing here runs while the global circuit breaker is open.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import breaker, categorize, db, discovery, google_auth, subscriptions

log = logging.getLogger("uvicorn.error")  # lands in the journal under uvicorn
TICK_S = 30
CATEGORIZE_RETRY_S = 600
MAX_PER_TICK = 4
SYNC_EVERY_S = 24 * 3600
SYNC_RETRY_S = 3600


def due_channels(conn, limit: int = MAX_PER_TICK) -> list[str]:
    now = db.iso(db.utcnow())
    rows = conn.execute(
        """SELECT id FROM channels WHERE subscribed=1 AND (next_poll_at IS NULL OR next_poll_at <= ?)
           ORDER BY next_poll_at IS NOT NULL, next_poll_at LIMIT ?""",
        (now, limit),
    ).fetchall()
    return [r["id"] for r in rows]


class Scheduler:
    def __init__(self, db_path: str, poll_limit: int = discovery.BACKFILL_LIMIT, settings=None):
        self.db_path = db_path
        self.settings = settings
        self._sync_after = 0.0
        self.poll_limit = poll_limit
        self._stop = threading.Event()
        self._cat_after = 0.0
        self._thread = threading.Thread(target=self._loop, name="subfeed-scheduler", daemon=True)
        self._workers = ThreadPoolExecutor(max_workers=2, thread_name_prefix="subfeed-poll")

    def start(self) -> "Scheduler":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._workers.shutdown(wait=False, cancel_futures=True)

    def tick(self) -> int:
        """One scheduling pass; returns how many channels were polled."""
        self._sync_subscriptions()
        conn = db.connect(self.db_path)
        try:
            if breaker.is_open(conn):
                return 0
            ids = due_channels(conn)
        finally:
            conn.close()
        if ids:
            list(self._workers.map(self._poll_one, ids))
        self._categorize()
        return len(ids)

    def _sync_subscriptions(self) -> None:
        """Daily Google subscription sync, only once signed in. A failure keeps the last list and
        retries in an hour; sign-in problems also land in kv.auth_error (see subscriptions.sync)."""
        s = self.settings
        if not s or not google_auth.configured(s) or not google_auth.signed_in(s) or time.monotonic() < self._sync_after:
            return
        conn = db.connect(self.db_path)
        try:
            last = db.get_kv(conn, "last_sub_sync")
            if last and (db.utcnow() - db.parse_iso(last)).total_seconds() < SYNC_EVERY_S:
                return
            self._sync_after = time.monotonic() + SYNC_RETRY_S
            res = subscriptions.sync(conn, s)
            log.info("scheduler: subscription sync %s", res)
        except Exception as e:  # noqa: BLE001
            log.warning("scheduler: subscription sync failed (%s)", type(e).__name__)
        finally:
            conn.close()

    def _categorize(self) -> None:
        """Categorize polled channels that have no answer yet. Failures wait CATEGORIZE_RETRY_S."""
        if not os.environ.get("OPENROUTER_API_KEY") or time.monotonic() < self._cat_after:
            return
        conn = db.connect(self.db_path)
        try:
            if categorize.pending(conn):
                categorize.categorize_pending(conn)
                if categorize.pending(conn):
                    self._cat_after = time.monotonic() + CATEGORIZE_RETRY_S
        except Exception:  # noqa: BLE001
            log.exception("scheduler: categorize failed")
            self._cat_after = time.monotonic() + CATEGORIZE_RETRY_S
        finally:
            conn.close()

    def _poll_one(self, channel_id: str) -> int:
        conn = db.connect(self.db_path)
        try:
            row = conn.execute("SELECT name, poll_limit FROM channels WHERE id=?", (channel_id,)).fetchone()
            # channels added by a Google sync carry a shallow poll_limit (5); seed channels use ours (15)
            limit = (row["poll_limit"] if row and row["poll_limit"] else None) or self.poll_limit
            new = discovery.poll_channel(conn, channel_id, limit)
            name = row["name"] if row else channel_id
            log.info("scheduler: polled %s (%s), %d new", name, channel_id, new)
            return new
        except Exception:  # noqa: BLE001 - one bad channel must not kill the scheduler
            log.exception("scheduler: poll of %s failed", channel_id)
            return 0
        finally:
            conn.close()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(TICK_S)


def start(app) -> Scheduler:
    log.info("scheduler: started (tick %ds)", TICK_S)
    sched = Scheduler(app.state.settings.db_path, app.state.settings.poll_limit, app.state.settings).start()
    app.state.scheduler = sched
    return sched
