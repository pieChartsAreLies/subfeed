"""Fix round 1: tombstones, in-progress protection, breaker between enrichments, pool resilience."""
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import timedelta

import pytest

from subfeed import breaker, db, discovery, pool, ytdl
from subfeed.discovery import poll_channel

from .test_discovery import NOW, TS, NoJitter, detail, fake, flat  # noqa: F401  (fake is a fixture)


def test_old_watched_video_eviction_keeps_tombstone_and_watch_row(conn, make, fake):
    ch = make.channel()
    old = "oldwatched1"
    ids = [f"vid{i:03d}" for i in range(25)]
    fake.listing[ch] = [flat(i) for i in ids] + [flat(old)]
    for n, i in enumerate(ids):
        fake.details[i] = detail(i, TS - n * 60)
    fake.details[old] = detail(old, TS - 200 * 86400)   # published 200 days ago
    assert poll_channel(conn, ch, limit=30, now=NOW, rng=NoJitter()) == 26
    # the user watches the old video; the next retention pass must evict it (watched first)
    conn.execute("UPDATE videos SET evicted=0, evicted_at=NULL WHERE id=?", (old,))
    make.progress(old, 500, watched=1)
    assert discovery.enforce_retention(conn, ch, now=NOW, listed=set(ids) | {old}) == 1
    row = conn.execute("SELECT evicted, evicted_at FROM videos WHERE id=?", (old,)).fetchone()
    assert row["evicted"] == 1 and row["evicted_at"]            # not deleted despite being > 90 days old
    assert conn.execute("SELECT watched FROM watch WHERE video_id=?", (old,)).fetchone()[0] == 1
    calls = len(fake.detail_calls)
    assert poll_channel(conn, ch, limit=30, now=NOW + timedelta(hours=1), rng=NoJitter()) == 0
    assert len(fake.detail_calls) == calls                       # not re-added as fresh and unwatched
    assert conn.execute("SELECT watched FROM watch WHERE video_id=?", (old,)).fetchone()[0] == 1


def test_tombstones_pruned_90_days_after_eviction_unless_still_listed(conn, make):
    ch = make.channel()
    a, b, fresh = make.video(ch, age_min=1), make.video(ch, age_min=2), make.video(ch, age_min=3)
    for v in (a, b):
        conn.execute("UPDATE videos SET evicted=1, evicted_at=? WHERE id=?", (db.iso(NOW - timedelta(days=100)), v))
    conn.execute("UPDATE videos SET evicted=1, evicted_at=? WHERE id=?", (db.iso(NOW - timedelta(days=10)), fresh))
    discovery.enforce_retention(conn, ch, now=NOW, listed={b})
    assert {r[0] for r in conn.execute("SELECT id FROM videos")} == {b, fresh}


def test_in_progress_video_is_never_evicted_as_oldest_unwatched(conn, make):
    ch = make.channel()
    ids = [make.video(ch, age_min=i) for i in range(26)]
    make.progress(ids[25], 600)          # oldest, 10 minutes in, unwatched
    assert discovery.enforce_retention(conn, ch) == 1
    assert conn.execute("SELECT id FROM videos WHERE evicted=1").fetchone()[0] == ids[24]


def test_breaker_checked_between_enrichments(conn, make, fake, monkeypatch):
    ch = make.channel()
    fake.listing[ch] = [flat("aaa"), flat("bbb"), flat("ccc")]
    for v in ("aaa", "bbb", "ccc"):
        fake.details[v] = detail(v, TS)

    def trip_after_first(video_id, priority=pool.DISCOVERY):
        out = fake.video_details(video_id, priority)
        breaker.trip(conn, "another channel hit a bot check", NOW)   # as a concurrent poll would
        return out

    monkeypatch.setattr(ytdl, "video_details", trip_after_first)
    poll_channel(conn, ch, now=NOW, rng=NoJitter())
    assert fake.detail_calls == ["aaa"]
    row = conn.execute("SELECT next_poll_at FROM channels WHERE id=?", (ch,)).fetchone()
    assert row["next_poll_at"] == db.get_kv(conn, "breaker_until")


class FakeExecutor:
    """Stands in for ProcessPoolExecutor: runs jobs on threads; can be told to look broken."""
    instances = []

    def __init__(self, workers, broken=False):
        self.broken = broken
        self.shut = False
        self._ex = ThreadPoolExecutor(max_workers=8)
        FakeExecutor.instances.append(self)

    def submit(self, fn, *a, **k):
        if self.broken:
            f = Future()
            f.set_exception(BrokenProcessPool("a worker died"))
            return f
        return self._ex.submit(fn, *a, **k)

    def shutdown(self, wait=True, cancel_futures=False):
        self.shut = True


def test_pool_recovers_from_broken_process_pool():
    FakeExecutor.instances = []
    p = pool.PriorityPool(workers=2, executor_factory=lambda n: FakeExecutor(n, broken=not FakeExecutor.instances))
    assert p.submit(pool.DISCOVERY, lambda: 42).result(timeout=5) == 42    # retried on a fresh executor
    assert len(FakeExecutor.instances) == 2 and FakeExecutor.instances[0].shut
    assert p.submit(pool.PLAY, lambda: 7).result(timeout=5) == 7           # and stays healthy
    assert len(FakeExecutor.instances) == 2
    p.shutdown()


def test_pool_persistent_breakage_surfaces_after_one_retry():
    p = pool.PriorityPool(workers=1, executor_factory=lambda n: FakeExecutor(n, broken=True))
    with pytest.raises(BrokenProcessPool):
        p.submit(pool.PLAY, lambda: 1).result(timeout=5)
    p.shutdown()


def test_pool_deadline_kills_and_replaces_pool():
    FakeExecutor.instances = []
    p = pool.PriorityPool(workers=1, executor_factory=lambda n: FakeExecutor(n))
    hung = p.submit(pool.DISCOVERY, time.sleep, 3, timeout=0.2)
    with pytest.raises(TimeoutError):
        hung.result(timeout=5)
    assert FakeExecutor.instances[0].shut and len(FakeExecutor.instances) == 2
    t0 = time.monotonic()
    assert p.submit(pool.PLAY, lambda: "ok").result(timeout=5) == "ok"     # the slot is free again
    assert time.monotonic() - t0 < 1
    p.shutdown()


def test_pool_default_deadlines_and_heartbeat():
    assert pool.DEADLINES[pool.PLAY] == 60 and pool.DEADLINES[pool.DISCOVERY] == 120
    assert pool.DEADLINES[pool.SAVE] is None
    gate = threading.Event()
    p = pool.PriorityPool(workers=1, executor_factory=lambda n: ThreadPoolExecutor(max_workers=n))
    f = p.submit(pool.SAVE, gate.wait)
    time.sleep(0.15)
    jobs = p.running_jobs()
    assert len(jobs) == 1 and jobs[0]["priority"] == pool.SAVE and jobs[0]["age_s"] >= 0.1
    gate.set()
    f.result(timeout=5)
    p.shutdown()
