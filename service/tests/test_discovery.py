import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from subfeed import breaker, cli, db, discovery, pool, scheduler, ytdl
from subfeed.discovery import next_poll_delay, poll_channel


class NoJitter(random.Random):
    def uniform(self, a, b):
        return 0.0


class FakeYtdl:
    """Replaces ytdl.channel_uploads / video_details. Entries are flat dicts; details are
    what the per-video call returns."""

    def __init__(self):
        self.listing = {}     # channel_id -> list[dict] | Exception
        self.details = {}     # video_id -> dict | Exception
        self.detail_calls = []
        self.listing_calls = []

    def channel_uploads(self, channel_id, limit, meta=None):
        self.listing_calls.append(channel_id)
        r = self.listing[channel_id]
        if isinstance(r, Exception):
            raise r
        if meta is not None:
            meta.update({"name": "Real Name", "description": "About the channel"})
        return r[:limit]

    def video_details(self, video_id, priority=pool.DISCOVERY):
        self.detail_calls.append(video_id)
        r = self.details[video_id]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def fake(monkeypatch):
    f = FakeYtdl()
    monkeypatch.setattr(ytdl, "channel_uploads", f.channel_uploads)
    monkeypatch.setattr(ytdl, "video_details", f.video_details)
    return f


def flat(vid, duration=600, **kw):
    return {"id": vid, "title": f"T {vid}", "duration": duration, "live_status": None, "availability": None,
            "timestamp": None, "release_timestamp": None, "url": f"https://www.youtube.com/watch?v={vid}", **kw}


def detail(vid, ts, duration=600, **kw):
    return {"id": vid, "title": f"T {vid}", "duration": duration, "live_status": "not_live",
            "availability": "public", "age_limit": 0, "timestamp": ts, "release_timestamp": None,
            "upload_date": None, "channel_id": "x", "url": f"https://www.youtube.com/watch?v={vid}",
            "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", **kw}


NOW = db.parse_iso("2026-10-25T12:00:00Z")
TS = int(NOW.timestamp())


def setup_channel(make, n_videos, fake, cid=None, shorts=0):
    ch = make.channel(cid=cid)
    entries = []
    for i in range(n_videos):
        vid = f"{ch[-6:]}v{i:03d}"
        entries.append(flat(vid))
        fake.details[vid] = detail(vid, TS - i * 3600)
    for i in range(shorts):
        sid = f"{ch[-6:]}s{i:03d}"
        entries.append(flat(sid, duration=None, url=f"https://www.youtube.com/shorts/{sid}"))
    fake.listing[ch] = entries
    return ch


def test_poll_is_idempotent(conn, make, fake):
    ch = setup_channel(make, 5, fake, shorts=2)
    assert poll_channel(conn, ch, now=NOW, rng=NoJitter()) == 5
    first = [tuple(r) for r in conn.execute("SELECT * FROM videos ORDER BY id")]
    calls = len(fake.detail_calls)
    assert calls == 5  # shorts were settled from the listing alone, never enriched
    assert poll_channel(conn, ch, now=NOW + timedelta(hours=1), rng=NoJitter()) == 0
    assert [tuple(r) for r in conn.execute("SELECT * FROM videos ORDER BY id")] == first
    assert len(fake.detail_calls) == calls  # known ids are not enriched again
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE eligible=1").fetchone()[0] == 5
    row = conn.execute("SELECT * FROM channels WHERE id=?", (ch,)).fetchone()
    assert row["name"] == "Real Name" and row["description"] == "About the channel"


def test_poll_stores_enriched_fields(conn, make, fake):
    ch = setup_channel(make, 1, fake)
    poll_channel(conn, ch, now=NOW, rng=NoJitter())
    v = conn.execute("SELECT * FROM videos").fetchone()
    assert v["published_at"] == "2026-10-25T12:00:00Z"
    assert v["duration_s"] == 600 and v["thumbnail_url"].endswith("hqdefault.jpg")
    assert v["eligible"] == 1 and v["live_status"] == "not_live"


def test_poll_drops_ineligible_after_enrichment(conn, make, fake):
    ch = make.channel()
    fake.listing[ch] = [flat("aaa"), flat("bbb"), flat("ccc")]
    fake.details["aaa"] = detail("aaa", TS, availability="subscriber_only")
    fake.details["bbb"] = detail("bbb", TS, age_limit=18)
    fake.details["ccc"] = detail("ccc", TS, live_status="is_live", duration=None)
    assert poll_channel(conn, ch, now=NOW, rng=NoJitter()) == 0
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE eligible=1").fetchone()[0] == 0
    assert {r[0] for r in conn.execute("SELECT ineligible_reason FROM videos")} == {
        "not_public", "age_restricted", "live"}


def test_retention_evicts_watched_first_never_saved(conn, make):
    ch = make.channel()
    ids = [make.video(ch, age_min=i) for i in range(30)]   # ids[0] newest ... ids[29] oldest
    watched_newish = ids[3]
    saved_oldest = ids[29]
    saved_progress = ids[28]
    make.progress(watched_newish, 500, watched=1)
    make.progress(ids[10], 500, watched=1)
    make.save(saved_oldest, "ready")
    make.save(saved_progress, "downloading")
    # 28 non-saved eligible, keep 25 -> evict 3: both watched first, then the oldest unwatched
    evicted = discovery.enforce_retention(conn, ch)
    assert evicted == 3
    gone = {r[0] for r in conn.execute("SELECT id FROM videos WHERE evicted=1")}
    assert gone == {watched_newish, ids[10], ids[27]}
    assert [r[0] for r in conn.execute(
        "SELECT evicted FROM videos WHERE id IN (?,?)", (saved_oldest, saved_progress))] == [0, 0]
    # saved videos sit outside the 25
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE evicted=0").fetchone()[0] == 27
    assert discovery.enforce_retention(conn, ch) == 0  # stable


def test_retention_all_unwatched_evicts_oldest(conn, make):
    ch = make.channel()
    ids = [make.video(ch, age_min=i) for i in range(26)]
    assert discovery.enforce_retention(conn, ch) == 1
    assert conn.execute("SELECT id FROM videos WHERE evicted=1").fetchone()[0] == ids[25]


def test_evicted_video_is_not_readded_by_next_poll(conn, make, fake):
    ch = make.channel()
    ids = [f"vid{i:03d}" for i in range(27)]
    fake.listing[ch] = [flat(i) for i in ids]
    for n, i in enumerate(ids):
        fake.details[i] = detail(i, TS - n * 60)
    assert poll_channel(conn, ch, limit=27, now=NOW, rng=NoJitter()) == 27
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE evicted=0").fetchone()[0] == 25
    calls = len(fake.detail_calls)
    assert poll_channel(conn, ch, limit=27, now=NOW + timedelta(hours=1), rng=NoJitter()) == 0
    assert len(fake.detail_calls) == calls
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE evicted=0").fetchone()[0] == 25


def test_backoff_on_error(conn, make, fake):
    ch = make.channel()
    fake.listing[ch] = ytdl.YtdlError("HTTP Error 500")
    delays = []
    for n in range(10):
        assert poll_channel(conn, ch, now=NOW, rng=NoJitter()) == 0
        row = conn.execute("SELECT next_poll_at, error_count, last_error FROM channels WHERE id=?", (ch,)).fetchone()
        delays.append((db.parse_iso(row["next_poll_at"]) - NOW).total_seconds())
        assert row["error_count"] == n + 1 and "500" in row["last_error"]
    assert delays[:4] == [900, 1800, 3600, 7200]
    assert max(delays) == 86400  # capped at 24 h
    # a success resets the backoff
    fake.listing[ch] = []
    poll_channel(conn, ch, now=NOW, rng=NoJitter())
    row = conn.execute("SELECT error_count, last_error FROM channels WHERE id=?", (ch,)).fetchone()
    assert row["error_count"] == 0 and row["last_error"] is None


def test_next_poll_delay_hourly_when_active_daily_when_quiet():
    recent = db.iso(NOW - timedelta(days=3))
    old = db.iso(NOW - timedelta(days=30))
    assert next_poll_delay(recent, NOW, 0, NoJitter()) == 3600
    assert next_poll_delay(old, NOW, 0, NoJitter()) == 86400
    assert next_poll_delay(None, NOW, 0, NoJitter()) == 86400
    rng = random.Random(7)
    samples = [next_poll_delay(recent, NOW, 0, rng) for _ in range(500)]
    assert min(samples) >= 3600 * 0.8 and max(samples) <= 3600 * 1.2
    assert max(samples) - min(samples) > 600  # jitter really varies


def test_bot_check_trips_global_breaker_and_pauses_polling(conn, make, fake):
    a = make.channel()
    b = make.channel()
    fake.listing[a] = ytdl.BotCheckError("Sign in to confirm you're not a bot")
    fake.listing[b] = []
    assert poll_channel(conn, a, now=NOW, rng=NoJitter()) == 0
    assert breaker.is_open(conn, NOW)
    assert db.parse_iso(db.get_kv(conn, "breaker_until")) == NOW + timedelta(hours=6)
    # the second channel is not even attempted while the breaker is open
    assert poll_channel(conn, b, now=NOW + timedelta(minutes=5), rng=NoJitter()) == 0
    assert fake.listing_calls == [a]
    # parked until the breaker closes, with no per-channel error penalty
    row = conn.execute("SELECT next_poll_at, error_count FROM channels WHERE id=?", (a,)).fetchone()
    assert row["next_poll_at"] == db.get_kv(conn, "breaker_until") and row["error_count"] == 0
    # reopening after the pause polls normally
    assert not breaker.is_open(conn, NOW + timedelta(hours=6, seconds=1))
    poll_channel(conn, b, now=NOW + timedelta(hours=7), rng=NoJitter())
    assert fake.listing_calls == [a, b]


def test_bot_check_during_enrichment_trips_breaker(conn, make, fake):
    ch = make.channel()
    fake.listing[ch] = [flat("aaa"), flat("bbb")]
    fake.details["aaa"] = ytdl.BotCheckError("Sign in to confirm you're not a bot")
    fake.details["bbb"] = detail("bbb", TS)
    poll_channel(conn, ch, now=NOW, rng=NoJitter())
    assert breaker.is_open(conn, NOW) and fake.detail_calls == ["aaa"]


def test_breaker_doubles_and_caps():
    # 6 h, 12 h, 24 h, 24 h
    import sqlite3
    c = sqlite3.connect(":memory:", isolation_level=None)
    c.row_factory = sqlite3.Row
    c.executescript("CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT)")
    t = NOW
    spans = []
    for _ in range(4):
        until = breaker.trip(c, "bot check", t)
        spans.append((until - t).total_seconds() / 3600)
        t = until + timedelta(minutes=1)
    assert spans == [6, 12, 24, 24]
    breaker.note_success(c, t)
    assert (breaker.trip(c, "again", t) - t).total_seconds() / 3600 == 6


def test_two_consecutive_canary_failures_trip_breaker(conn):
    breaker.note_canary(conn, False, NOW)
    assert not breaker.is_open(conn, NOW)
    breaker.note_canary(conn, True, NOW)
    breaker.note_canary(conn, False, NOW)
    assert not breaker.is_open(conn, NOW)
    breaker.note_canary(conn, False, NOW)
    assert breaker.is_open(conn, NOW)


def test_upcoming_is_rechecked_after_release(conn, make, fake):
    ch = make.channel()
    fake.listing[ch] = [flat("prem", duration=None, live_status="is_upcoming")]
    assert poll_channel(conn, ch, now=NOW, rng=NoJitter()) == 0
    row = conn.execute("SELECT eligible, ineligible_reason, recheck_at FROM videos WHERE id='prem'").fetchone()
    assert row["eligible"] == 0 and row["ineligible_reason"] == "upcoming" and row["recheck_at"]
    assert fake.detail_calls == []
    # later it has aired and is an ordinary finished premiere
    fake.details["prem"] = detail("prem", TS + 3600, duration=1200, release_timestamp=TS + 3600)
    fake.listing[ch] = [flat("prem", duration=1200)]
    assert poll_channel(conn, ch, now=NOW + timedelta(hours=3), rng=NoJitter()) == 1
    row = conn.execute("SELECT eligible, recheck_at, duration_s FROM videos WHERE id='prem'").fetchone()
    assert row["eligible"] == 1 and row["recheck_at"] is None and row["duration_s"] == 1200


def test_same_video_on_two_channels_is_stored_once(conn, make, fake):
    a, b = make.channel(), make.channel()
    fake.listing[a] = [flat("dup")]
    fake.listing[b] = [flat("dup")]
    fake.details["dup"] = detail("dup", TS)
    assert poll_channel(conn, a, now=NOW, rng=NoJitter()) == 1
    assert poll_channel(conn, b, now=NOW, rng=NoJitter()) == 0
    assert conn.execute("SELECT channel_id FROM videos WHERE id='dup'").fetchall()[0][0] == a


def test_failed_details_are_retried_next_poll(conn, make, fake):
    ch = make.channel()
    fake.listing[ch] = [flat("aaa"), flat("bbb")]
    fake.details["aaa"] = ytdl.YtdlError("temporary")
    fake.details["bbb"] = detail("bbb", TS)
    assert poll_channel(conn, ch, now=NOW, rng=NoJitter()) == 1
    fake.details["aaa"] = detail("aaa", TS - 60)
    assert poll_channel(conn, ch, now=NOW + timedelta(hours=1), rng=NoJitter()) == 1
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE eligible=1").fetchone()[0] == 2


def test_pick_thumbnail_fallback_chain():
    vid = "abc123"
    base = "https://i.ytimg.com/vi/abc123/"
    assert ytdl.pick_thumbnail(vid, head=lambda u: True) == base + "maxresdefault.jpg"
    assert ytdl.pick_thumbnail(vid, head=lambda u: "sddefault" in u) == base + "sddefault.jpg"
    assert ytdl.pick_thumbnail(vid, head=lambda u: False) == base + "hqdefault.jpg"


def test_published_at_prefers_timestamp_then_release_then_upload_date():
    assert ytdl.published_at({"timestamp": 1792845298}) == "2026-10-24T12:34:58Z"
    assert ytdl.published_at({"timestamp": None, "release_timestamp": 1792845298}) == "2026-10-24T12:34:58Z"
    assert ytdl.published_at({"upload_date": "20261024"}) == "2026-10-24T00:00:00Z"
    assert ytdl.published_at({}) == ""


def test_pool_runs_play_before_save_before_discovery():
    gate = threading.Event()
    order = []
    p = pool.PriorityPool(workers=1, executor_factory=lambda n: ThreadPoolExecutor(max_workers=n))
    blocker = p.submit(pool.DISCOVERY, gate.wait)
    time.sleep(0.1)  # the single worker is now busy
    futs = [
        p.submit(pool.DISCOVERY, order.append, "discovery-1"),
        p.submit(pool.SAVE, order.append, "save"),
        p.submit(pool.DISCOVERY, order.append, "discovery-2"),
        p.submit(pool.PLAY, order.append, "play"),
    ]
    gate.set()
    for f in [blocker, *futs]:
        f.result(timeout=5)
    p.shutdown()
    assert order == ["play", "save", "discovery-1", "discovery-2"]


def test_pool_limits_concurrency_and_propagates_errors():
    running, peak, lock = 0, 0, threading.Lock()

    def work():
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.05)
        with lock:
            running -= 1

    def boom():
        raise ytdl.BotCheckError("bot")

    p = pool.PriorityPool(workers=2, executor_factory=lambda n: ThreadPoolExecutor(max_workers=8))
    futs = [p.submit(pool.DISCOVERY, work) for _ in range(8)]
    for f in futs:
        f.result(timeout=5)
    assert peak == 2
    with pytest.raises(ytdl.BotCheckError):
        p.submit(pool.PLAY, boom).result(timeout=5)
    p.shutdown()


def test_classify_bot_check_errors():
    assert isinstance(ytdl.classify(Exception("ERROR: [youtube] x: Sign in to confirm you’re not a bot")), ytdl.BotCheckError)
    assert isinstance(ytdl.classify(Exception("HTTP Error 429: Too Many Requests")), ytdl.BotCheckError)
    assert not isinstance(ytdl.classify(Exception("Video unavailable")), ytdl.BotCheckError)


def test_scheduler_picks_due_channels_and_respects_breaker(settings, conn, make, fake):
    never = setup_channel(make, 2, fake)
    later = make.channel()
    conn.execute("UPDATE channels SET next_poll_at=? WHERE id=?", (db.iso(db.utcnow() + timedelta(hours=1)), later))
    overdue = make.channel()
    conn.execute("UPDATE channels SET next_poll_at=? WHERE id=?", (db.iso(db.utcnow() - timedelta(hours=1)), overdue))
    unsub = make.channel(subscribed=0)
    assert scheduler.due_channels(conn) == [never, overdue]
    assert unsub not in scheduler.due_channels(conn)
    fake.listing[overdue] = []
    s = scheduler.Scheduler(settings.db_path)
    assert s.tick() == 2
    assert set(fake.listing_calls) == {never, overdue}
    breaker.trip(conn, "bot", db.utcnow())
    fake.listing_calls.clear()
    conn.execute("UPDATE channels SET next_poll_at=NULL")
    assert s.tick() == 0 and fake.listing_calls == []
    s.stop()


def test_scheduler_can_be_disabled(settings, monkeypatch):
    from fastapi.testclient import TestClient
    from subfeed.api import create_app
    started = []
    monkeypatch.setattr(scheduler, "start", lambda app: started.append(1))
    settings.scheduler = False
    with TestClient(create_app(settings)):
        pass
    assert started == []
    settings.scheduler = True
    monkeypatch.setattr(scheduler, "start", lambda app: started.append(1) or scheduler.Scheduler.__new__(scheduler.Scheduler))
    monkeypatch.setattr(scheduler.Scheduler, "stop", lambda self: None)
    with TestClient(create_app(settings)):
        pass
    assert started == [1]


def test_example_seed_file_parses_and_imports_idempotently(conn):
    example = cli.DEFAULT_SEED.with_name("channels.example.txt")
    channels = cli.parse_seed(example.read_text())
    n = len(channels)
    assert n >= 3
    assert len({c["id"] for c in channels}) == n and all(c["id"].startswith("UC") and len(c["id"]) == 24 for c in channels)
    assert cli.seed_channels(conn, channels) == n
    assert cli.seed_channels(conn, channels) == 0
    assert conn.execute("SELECT COUNT(*) FROM channels WHERE subscribed=1").fetchone()[0] == n
    # re-seeding must not undo a manual category choice
    cid = channels[0]["id"]
    conn.execute("UPDATE channels SET hidden=1 WHERE id=?", (cid,))
    cli.seed_channels(conn, channels)
    assert conn.execute("SELECT hidden FROM channels WHERE id=?", (cid,)).fetchone()[0] == 1


def test_default_seed_file_exists_and_parses():
    assert cli.DEFAULT_SEED.exists()
    assert cli.parse_seed(cli.DEFAULT_SEED.read_text())


def test_retention_in_progress_uses_content_position_not_raw(conn, make):
    ch = make.channel()
    ids = [make.video(ch, age_min=i) for i in range(26)]
    # raw position 40 but furthest content position 20 (sponsor-skipped): not in progress, evictable
    make.progress(ids[25], 40, max_position=20)
    assert discovery.enforce_retention(conn, ch) == 1
    assert conn.execute("SELECT id FROM videos WHERE evicted=1").fetchone()[0] == ids[25]
