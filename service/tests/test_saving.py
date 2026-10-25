import os

import pytest
from fastapi.testclient import TestClient

from subfeed import backup, breaker, health, saving, storage, ytdl
from subfeed.api import create_app

from .conftest import AUTH

GB = 1024**3
VID = "abcdefghijk"


@pytest.fixture
def env(settings, conn, make, monkeypatch):
    os.makedirs(settings.saved_dir)
    mounted = {"v": {"free": 100 * GB, "total": 250 * GB}}
    monkeypatch.setattr(storage, "check_mount", lambda path, timeout=2.0: mounted["v"])
    ch = make.channel()
    make.video(ch, vid=VID, duration=600)
    with TestClient(create_app(settings), headers=AUTH) as c:
        c.mounted, c.dir, c.settings = mounted, settings.saved_dir, settings
        yield c


def worker(settings, runner, fetch_thumb=lambda url: b"JPG"):
    return saving.SaveWorker(settings, runner=runner, fetch_thumb=fetch_thumb)


def good_runner(vid, quality, saved_dir):
    with open(os.path.join(saved_dir, f"{vid}.partial.mp4"), "wb") as f:
        f.write(b"MP4DATA" * 100)
    with open(os.path.join(saved_dir, f"{vid}.partial.en.vtt"), "w") as f:
        f.write("WEBVTT")
    return {"title": "T", "duration": 600, "height": 1080, "chapters": []}


def test_save_refuses_when_not_mounted(env, conn):
    env.mounted["v"] = None
    r = env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    assert r.status_code == 503 and r.json() == {"sentence": "Can't reach the NAS, so saving is paused."}
    assert conn.execute("SELECT COUNT(*) FROM saves").fetchone()[0] == 0
    assert os.listdir(env.dir) == []                      # nothing written anywhere


def test_save_refuses_over_quota(env, conn):
    env.mounted["v"] = {"free": 1 * GB, "total": 250 * GB}           # NAS nearly full
    r = env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    assert r.status_code == 507 and r.json() == {"sentence": "Your saved-videos space is full."}
    env.mounted["v"] = {"free": 100 * GB, "total": 250 * GB}
    conn.execute("INSERT INTO videos(id,channel_id,title,published_at,discovered_at) "
                 "SELECT 'otherother1', channel_id, 't', published_at, discovered_at FROM videos")
    conn.execute("INSERT INTO saves(video_id,quality,state,bytes,updated_at) VALUES('otherother1','1080p','ready',?, 'x')",
                 (249 * GB,))                                         # our own saves fill the quota
    assert env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"}).status_code == 507


def test_save_queues_and_is_idempotent(env, conn):
    assert env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"}).status_code == 202
    assert env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"}).json() == {"state": "queued"}
    assert conn.execute("SELECT COUNT(*) FROM saves").fetchone()[0] == 1
    assert env.post(f"/api/videos/{VID}/save", json={"quality": "8k"}).status_code == 422
    assert env.post("/api/videos/nope1234567/save", json={}).status_code == 404


def test_save_atomic_rename_and_sidecars(env, conn):
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    assert worker(env.settings, good_runner).run_once(conn) is True
    row = conn.execute("SELECT state, bytes, error FROM saves WHERE video_id=?", (VID,)).fetchone()
    assert row["state"] == "ready" and row["bytes"] > 700 and row["error"] is None
    assert sorted(os.listdir(env.dir)) == [f"{VID}.en.vtt", f"{VID}.info.json", f"{VID}.jpg", f"{VID}.mp4"]
    assert open(os.path.join(env.dir, f"{VID}.jpg"), "rb").read() == b"JPG"
    assert not any("partial" in n for n in os.listdir(env.dir))


def test_runner_failure_leaves_no_mp4(env, conn):
    def half(vid, q, d):
        open(os.path.join(d, f"{vid}.partial.mp4"), "wb").write(b"x")
        raise ytdl.YtdlError("network died")
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    worker(env.settings, half).run_once(conn)
    assert conn.execute("SELECT state FROM saves").fetchone()[0] == "failed"
    assert os.listdir(env.dir) == []                              # partial cleaned, no .mp4


def test_save_bot_check_retries_then_fails(env, conn):
    attempts = []

    def blocked(vid, q, d):
        attempts.append(1)
        raise ytdl.BotCheckError("Sign in to confirm you're not a bot")

    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    w = worker(env.settings, blocked)
    for n in (1, 2):                       # each bot-check re-queues the save and opens the breaker
        assert w.run_once(conn) is True
        assert conn.execute("SELECT state FROM saves").fetchone()[0] == "queued"
        assert breaker.is_open(conn) and len(attempts) == n
        assert w.run_once(conn) is False   # held while the breaker is open: no extra YouTube traffic
        assert len(attempts) == n
        conn.execute("DELETE FROM kv WHERE key IN ('breaker_until')")          # pause over
    assert w.run_once(conn) is True        # third attempt
    row = conn.execute("SELECT state, error FROM saves").fetchone()
    assert len(attempts) == 3 and row["state"] == "failed"
    assert row["error"] == "YouTube blocked this download. Try again later."
    assert os.listdir(env.dir) == []


def test_download_storage_errors_surface_sentences(env, conn):
    for code, sentence in (("full", "Your saved-videos space is full."), ("nas", "Can't reach the NAS, so saving is paused.")):
        conn.execute("DELETE FROM saves")
        env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})

        def boom(v, q, d, code=code):
            raise ytdl.StorageError(code)
        worker(env.settings, boom).run_once(conn)
        assert conn.execute("SELECT error FROM saves").fetchone()[0] == sentence


def test_classify_maps_oserror_to_storage_error():
    import errno
    class Wrapped(Exception):
        exc_info = (None, OSError(errno.ENOSPC, "No space left"), None)
    assert str(ytdl.classify(Wrapped("download failed"))) == "full"
    assert str(ytdl.classify(OSError(errno.EIO, "io"))) == "nas"
    assert not isinstance(ytdl.classify(Exception("HTTP Error 404")), ytdl.StorageError)


def test_nas_vanishing_before_finalize_fails_closed(env, conn):
    def vanish(vid, q, d):
        meta = good_runner(vid, q, d)
        env.mounted["v"] = None
        return meta
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    worker(env.settings, vanish).run_once(conn)
    assert conn.execute("SELECT state, error FROM saves").fetchone()[1] == "Can't reach the NAS, so saving is paused."
    assert os.listdir(env.dir) == []


def test_finalize_rename_failure_removes_sidecars(env, conn, monkeypatch):
    real = os.replace
    def flaky(src, dst):
        if dst.endswith(f"{VID}.mp4"):
            raise OSError(5, "I/O error")
        return real(src, dst)
    monkeypatch.setattr(saving.os, "replace", flaky)
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    worker(env.settings, good_runner).run_once(conn)
    assert conn.execute("SELECT state FROM saves").fetchone()[0] == "failed"
    assert os.listdir(env.dir) == []


def test_worker_waits_while_breaker_open(env, conn):
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    breaker.trip(conn, "x")
    assert worker(env.settings, good_runner).run_once(conn) is False
    assert conn.execute("SELECT state FROM saves").fetchone()[0] == "queued"


def test_worker_fails_closed_when_nas_vanishes(env, conn):
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    env.mounted["v"] = None
    worker(env.settings, good_runner).run_once(conn)
    row = conn.execute("SELECT state, error FROM saves").fetchone()
    assert row["state"] == "failed" and row["error"] == "Can't reach the NAS, so saving is paused."
    assert os.listdir(env.dir) == []


def test_unsave_deletes_files(env, conn):
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    worker(env.settings, good_runner).run_once(conn)
    open(os.path.join(env.dir, "keepkeepkee.mp4"), "wb").write(b"other")
    assert env.delete(f"/api/videos/{VID}/save").status_code == 204
    assert os.listdir(env.dir) == ["keepkeepkee.mp4"]
    assert conn.execute("SELECT COUNT(*) FROM saves").fetchone()[0] == 0
    assert env.delete(f"/api/videos/{VID}/save").status_code == 204        # idempotent


def test_startup_requeues_and_reaps_partials(env, conn):
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    conn.execute("UPDATE saves SET state='downloading'")
    open(os.path.join(env.dir, f"{VID}.partial.f137.mp4"), "wb").write(b"x")
    open(os.path.join(env.dir, "done000000a.mp4"), "wb").write(b"x")
    worker(env.settings, good_runner).startup(conn)
    assert conn.execute("SELECT state FROM saves").fetchone()[0] == "queued"
    assert os.listdir(env.dir) == ["done000000a.mp4"]


def test_is_faststart(tmp_path):
    def atom(kind, n=8):
        return (n).to_bytes(4, "big") + kind + b"\0" * (n - 8)
    fast, slow = tmp_path / "f.mp4", tmp_path / "s.mp4"
    fast.write_bytes(atom(b"ftyp", 16) + atom(b"moov", 20) + atom(b"mdat", 30))
    slow.write_bytes(atom(b"ftyp", 16) + atom(b"mdat", 30) + atom(b"moov", 20))
    assert saving.is_faststart(str(fast)) and not saving.is_faststart(str(slow))


def test_mount_check_fails_closed_for_plain_directory(tmp_path):
    assert storage.check_mount(str(tmp_path)) is None              # exists but is not a mount
    assert storage.check_mount(str(tmp_path / "missing")) is None


def test_mount_check_times_out(monkeypatch, tmp_path):
    import time
    monkeypatch.setattr(storage.os.path, "ismount", lambda p: time.sleep(5))
    t = time.monotonic()
    assert storage.check_mount(str(tmp_path), timeout=0.2) is None
    assert time.monotonic() - t < 1


def test_backup_keeps_14_and_refuses_unmounted(env, conn, tmp_path):
    from datetime import datetime, timedelta
    base = datetime(2026, 10, 1)
    for i in range(16):
        out = backup.backup(env.settings.db_path, env.dir, now=base + timedelta(days=i))
    names = sorted(os.listdir(os.path.join(env.dir, "_db")))
    assert len(names) == 14 and names[-1] == "subfeed-20261016.db" and names[0] == "subfeed-20261003.db"
    import sqlite3
    assert sqlite3.connect(out).execute("select count(*) from videos").fetchone()[0] == 1
    env.mounted["v"] = None
    with pytest.raises(RuntimeError):
        backup.backup(env.settings.db_path, env.dir)


def test_health_fields(env, conn):
    env.post(f"/api/videos/{VID}/save", json={"quality": "1080p"})
    worker(env.settings, good_runner).run_once(conn)
    h = env.get("/api/health", headers={}).json()
    assert h["nas_mounted"] is True and h["saved_bytes"] > 0 and h["saved_quota_bytes"] == 250 * GB
    assert h["last_canary"] is None and h["canary_ok"] is None and h["jobs"] == []
    ok_info = {"formats": [
        {"vcodec": "avc1.64", "acodec": "none", "ext": "mp4", "height": 1080, "protocol": "https", "url": "u"},
        {"vcodec": "none", "acodec": "mp4a.40.2", "ext": "m4a", "protocol": "https", "url": "u"}]}
    assert health.run_canary(conn, extract=lambda v: ok_info) is True
    h = env.get("/api/health", headers={}).json()
    assert h["canary_ok"] is True and h["last_canary"]


def test_canary_failures_trip_breaker_on_second(conn):
    def bad(v):
        raise ytdl.YtdlError("nope")
    assert health.run_canary(conn, extract=bad) is False and not breaker.is_open(conn)
    assert health.run_canary(conn, extract=bad) is False and breaker.is_open(conn)
    assert health.run_canary(conn, extract=bad) is True            # skipped while paused


def test_canary_bot_check_trips_immediately(conn):
    def bot(v):
        raise ytdl.BotCheckError("Sign in to confirm")
    health.run_canary(conn, extract=bot)
    assert breaker.is_open(conn)


def test_health_mount_probe_cached(env, monkeypatch):
    calls = []
    monkeypatch.setattr(storage, "check_mount", lambda path, timeout=2.0: calls.append(1))
    health._mount_cache.clear()
    for _ in range(5):
        env.get("/api/health", headers={})
    assert len(calls) == 1
