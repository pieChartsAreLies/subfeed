"""Final fix wave: sign-in failures are visible, re-sign-in works, /detail priority,
DISCOVERY pacing, shallow sync backfill, sync lock, save format and captions options."""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from subfeed import db, google_auth, pool, saving, scheduler, subscriptions
from subfeed.api import create_app

from .conftest import AUTH
from .test_subscriptions import ACCESS, REFRESH, fake_google, item, uc


@pytest.fixture
def gsettings(settings, tmp_path):
    settings.google_client_id = "cid"
    settings.google_client_secret = "csecret"
    settings.google_token_path = str(tmp_path / "google-token.json")
    return settings


@pytest.fixture
def signed_in(gsettings):
    google_auth.save_refresh_token(gsettings, REFRESH)
    return gsettings


def kv(conn, key):
    return db.get_kv(conn, key)


# --- device flow: every failure leaves a sentence ------------------------------------------

@pytest.mark.parametrize("status,body,want", [
    (428, {"error": "access_denied"}, google_auth.DENIED),
    (400, {"error": "expired_token"}, google_auth.CODE_EXPIRED),
    (401, {"error": "invalid_client"}, google_auth.MISCONFIGURED),
    (400, {"error": "invalid_client"}, google_auth.MISCONFIGURED),
    (400, {"error": "something_new"}, "Google sign-in failed (something_new). Choose Sign in to try again."),
])
def test_poll_failure_writes_sentence(gsettings, conn, monkeypatch, status, body, want):
    flow = google_auth.DeviceFlow(gsettings, gsettings.db_path)
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (status, body))
    assert flow.poll_once("dev") == "failed"
    assert kv(conn, "auth_error") == want


def test_google_5xx_without_error_is_pending(gsettings, conn, monkeypatch):
    flow = google_auth.DeviceFlow(gsettings, gsettings.db_path)
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (503, {}))
    assert flow.poll_once("dev") == "pending"
    assert kv(conn, "auth_error") is None


def test_poll_gives_up_after_repeated_exceptions(gsettings, conn, monkeypatch):
    flow = google_auth.DeviceFlow(gsettings, gsettings.db_path)
    calls = []

    def boom(url, data):
        calls.append(1)
        raise ConnectionError("ConnectTimeout")

    monkeypatch.setattr(google_auth, "_post", boom)
    flow._poll("dev", 0, 60, threading.Event())   # interval 0: runs straight through
    assert len(calls) == google_auth.MAX_POLL_EXCEPTIONS
    assert kv(conn, "auth_error") == google_auth.UNREACHABLE


def test_poll_deadline_says_code_expired(gsettings, conn, monkeypatch):
    flow = google_auth.DeviceFlow(gsettings, gsettings.db_path)
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (428, {"error": "authorization_pending"}))
    flow._poll("dev", 0, 0, threading.Event())
    assert kv(conn, "auth_error") == google_auth.CODE_EXPIRED


def test_start_without_user_code_is_502_sentence(gsettings, monkeypatch):
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (200, {"device_code": "D", "interval": 5}))
    with TestClient(create_app(gsettings), headers=AUTH) as c:
        r = c.post("/api/auth/device/start")
    assert r.status_code == 502 and r.json() == {"sentence": google_auth.NO_CODE}


def test_start_with_bad_client_is_502_sentence(gsettings, monkeypatch):
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (401, {"error": "invalid_client"}))
    with TestClient(create_app(gsettings), headers=AUTH) as c:
        r = c.post("/api/auth/device/start")
    assert r.status_code == 502 and r.json() == {"sentence": google_auth.MISCONFIGURED}


# --- re-sign-in: signing_in gates the TV, REVOKED forgets the token -------------------------

def test_signing_in_true_until_attempt_finishes(signed_in, conn, monkeypatch):
    """With an old token file present, signed_in is already true; signing_in stays true until the
    new attempt ends, and a denial then shows as the error."""
    gate = threading.Event()
    answers = iter([(200, {"device_code": "D", "user_code": "ABCD", "interval": 0, "expires_in": 60})])

    def post(url, data):
        if url == google_auth.DEVICE_URL:
            return next(answers)
        gate.wait(5)
        return 428, {"error": "access_denied"}

    monkeypatch.setattr(google_auth, "_post", post)
    db.set_kv(conn, "auth_error", "old error")
    with TestClient(create_app(signed_in), headers=AUTH) as c:
        assert c.post("/api/auth/device/start").status_code == 200
        s = c.get("/api/auth/status").json()
        assert s["signing_in"] is True and s["signed_in"] is True and s["error"] is None
        gate.set()
        for _ in range(100):
            s = c.get("/api/auth/status").json()
            if not s["signing_in"]:
                break
            time.sleep(0.02)
    assert s["signing_in"] is False and s["error"] == google_auth.DENIED


def test_invalid_grant_forgets_token(signed_in, conn, monkeypatch):
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (400, {"error": "invalid_grant"}))
    with pytest.raises(google_auth.AuthError) as e:
        subscriptions.sync(conn, signed_in)
    assert str(e.value) == google_auth.REVOKED
    assert not os.path.exists(signed_in.google_token_path)
    assert not google_auth.signed_in(signed_in)
    assert kv(conn, "auth_error") == google_auth.REVOKED


def test_invalid_client_on_refresh_is_setup_problem_not_revoked(signed_in, conn, monkeypatch):
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (401, {"error": "invalid_client"}))
    with pytest.raises(google_auth.AuthError) as e:
        subscriptions.sync(conn, signed_in)
    assert str(e.value) == google_auth.MISCONFIGURED
    assert os.path.exists(signed_in.google_token_path)   # the grant itself may be fine


# --- sync_error ----------------------------------------------------------------------------

def _forbidden_get(reason):
    def get(path, params, token):
        return 403, {"error": {"code": 403, "errors": [{"reason": reason}]}}
    return get


@pytest.mark.parametrize("reason,want", [
    ("quotaExceeded", subscriptions.QUOTA),
    ("accessNotConfigured", subscriptions.API_OFF),
    ("insufficientPermissions", subscriptions.NO_SCOPE),
])
def test_403_stores_sync_error_and_status_returns_it(signed_in, conn, make, monkeypatch, reason, want):
    make.channel(cid=uc(1))
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (200, {"access_token": ACCESS}))
    monkeypatch.setattr(subscriptions, "_get", _forbidden_get(reason))
    with pytest.raises(ConnectionError):
        subscriptions.sync(conn, signed_in)
    assert kv(conn, "sync_error") == want
    assert conn.execute("SELECT subscribed FROM channels").fetchone()[0] == 1
    with TestClient(create_app(signed_in), headers=AUTH) as c:
        s = c.get("/api/auth/status").json()
    assert s["sync_error"] == want and s["error"] == want and s["signed_in"] is True


def test_network_failure_and_empty_list_store_sync_error(signed_in, conn, monkeypatch):
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (200, {"access_token": ACCESS}))

    def down(path, params, token):
        raise ConnectionError("ConnectTimeout")

    monkeypatch.setattr(subscriptions, "_get", down)
    with pytest.raises(ConnectionError):
        subscriptions.sync(conn, signed_in)
    assert kv(conn, "sync_error") == subscriptions.UNREACHABLE
    fake_google(monkeypatch, [[]])
    with pytest.raises(ConnectionError):
        subscriptions.sync(conn, signed_in)
    assert kv(conn, "sync_error") == subscriptions.EMPTY
    fake_google(monkeypatch, [[item(1)]])
    subscriptions.sync(conn, signed_in)
    assert kv(conn, "sync_error") is None


def test_sync_added_channels_backfill_shallow(signed_in, conn, make, monkeypatch):
    seed = make.channel(cid=uc(1))
    fake_google(monkeypatch, [[item(1), item(2)]])
    subscriptions.sync(conn, signed_in)
    limits = dict(conn.execute("SELECT id, poll_limit FROM channels").fetchall())
    assert limits == {seed: None, uc(2): subscriptions.SYNC_BACKFILL}
    assert subscriptions.SYNC_BACKFILL == 5


def test_scheduler_polls_with_channel_limit(settings, conn, make, monkeypatch):
    seed = make.channel(cid=uc(1))
    make.channel(cid=uc(2))
    conn.execute("UPDATE channels SET poll_limit=5 WHERE id=?", (uc(2),))
    seen = {}
    monkeypatch.setattr(scheduler.discovery, "poll_channel", lambda c, cid, limit: seen.setdefault(cid, limit) and 0)
    s = scheduler.Scheduler(settings.db_path, 15, settings)
    s._poll_one(seed)
    s._poll_one(uc(2))
    assert seen == {seed: 15, uc(2): 5}


def test_sync_lock_serializes(signed_in, conn, monkeypatch):
    fake_google(monkeypatch, [[item(1)]])
    inside, peak = [0], [0]
    real = subscriptions.fetch_subscriptions

    def slow(token):
        inside[0] += 1
        peak[0] = max(peak[0], inside[0])
        time.sleep(0.1)
        inside[0] -= 1
        return real(token)

    monkeypatch.setattr(subscriptions, "fetch_subscriptions", slow)

    def run():
        c = db.connect(signed_in.db_path)
        try:
            subscriptions.sync(c, signed_in)
        finally:
            c.close()

    ts = [threading.Thread(target=run) for _ in range(3)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert peak[0] == 1


# --- the scheduler's daily sync (was untested) -----------------------------------------------

def test_scheduler_sync_subscriptions(signed_in, conn, monkeypatch):
    calls = []
    monkeypatch.setattr(subscriptions, "sync", lambda c, s: calls.append(1) or {"added": 0})
    sch = scheduler.Scheduler(signed_in.db_path, 15, signed_in)
    sch._sync_subscriptions()
    assert calls == [1]                         # never synced: runs now
    sch._sync_subscriptions()
    assert calls == [1]                         # retry window holds it back
    sch._sync_after = 0
    db.set_kv(conn, "last_sub_sync", db.iso(db.utcnow()))
    sch._sync_subscriptions()
    assert calls == [1]                         # synced less than a day ago
    db.set_kv(conn, "last_sub_sync", db.iso(db.utcnow() - timedelta(hours=25)))
    sch._sync_subscriptions()
    assert calls == [1, 1]                      # a day old: runs again


def test_scheduler_sync_skips_when_signed_out_and_survives_failure(gsettings, conn, monkeypatch):
    calls = []

    def fail(c, s):
        calls.append(1)
        raise ConnectionError("down")

    monkeypatch.setattr(subscriptions, "sync", fail)
    sch = scheduler.Scheduler(gsettings.db_path, 15, gsettings)
    sch._sync_subscriptions()
    assert calls == []                          # no token file: nothing to sync
    google_auth.save_refresh_token(gsettings, REFRESH)
    sch._sync_subscriptions()                   # raises inside; must not escape
    assert calls == [1] and sch._sync_after > time.monotonic() + scheduler.SYNC_RETRY_S - 5


# --- pool -----------------------------------------------------------------------------------

def test_detail_runs_after_play_before_save():
    assert pool.PLAY < pool.DETAIL < pool.SAVE < pool.DISCOVERY
    p = pool.PriorityPool(workers=1, executor_factory=lambda n: ThreadPoolExecutor(max_workers=n))
    gate, order = threading.Event(), []
    blocker = p.submit(pool.SAVE, gate.wait)
    time.sleep(0.05)
    futs = [p.submit(pool.SAVE, order.append, "save"), p.submit(pool.DETAIL, order.append, "detail"),
            p.submit(pool.PLAY, order.append, "play")]
    gate.set()
    for f in [blocker, *futs]:
        f.result(timeout=5)
    assert order == ["play", "detail", "save"]
    p.shutdown()


def test_video_meta_uses_detail_priority(monkeypatch):
    from subfeed import ytdl
    seen = []

    class P:
        def submit(self, prio, fn, *a, **k):
            seen.append(prio)
            f = __import__("concurrent.futures").futures.Future()
            f.set_result({})
            return f

    monkeypatch.setattr(pool, "get_pool", lambda: P())
    ytdl.video_meta("abc")
    assert seen == [pool.DETAIL]


def test_discovery_paced_globally(monkeypatch):
    monkeypatch.setattr(pool, "DISCOVERY_INTERVAL_S", 0.2)
    p = pool.PriorityPool(workers=2, executor_factory=lambda n: ThreadPoolExecutor(max_workers=n))
    starts = []
    lock = threading.Lock()

    def submit_one():
        p.submit(pool.DISCOVERY, lambda: None).result(timeout=5)
        with lock:
            starts.append(time.monotonic())

    t0 = time.monotonic()
    ts = [threading.Thread(target=submit_one) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert max(starts) - t0 >= 0.55            # 4 jobs, 0.2 s apart: the last starts >= 0.6 s in
    # plays are not paced
    t1 = time.monotonic()
    p.submit(pool.PLAY, lambda: None).result(timeout=5)
    assert time.monotonic() - t1 < 0.15
    p.shutdown()


# --- save options ----------------------------------------------------------------------------

def test_1080p_format_never_falls_back_to_av1():
    f = saving.FORMATS["1080p"]
    assert f.startswith("bv*[vcodec^=avc1]")
    for alt in f.split("/")[1:]:
        assert "[vcodec!^=av01]" in alt


def test_save_requests_auto_captions(monkeypatch, tmp_path):
    """The subtitle pass asks yt-dlp for auto-generated English (and en-orig) too."""
    captured = []

    class FakeYDL:
        def __init__(self, opts):
            captured.append(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            if not captured[-1].get("writesubtitles"):
                open(os.path.join(tmp_path, "vid00000001.partial.mp4"), "wb").write(b"\0" * 64)
            return {"title": "t"}

    import sys
    import types
    monkeypatch.setitem(sys.modules, "yt_dlp", types.SimpleNamespace(YoutubeDL=FakeYDL))
    monkeypatch.setattr(saving, "is_faststart", lambda p: True)
    saving._w_save("vid00000001", "1080p", str(tmp_path))
    sub = [o for o in captured if o.get("writesubtitles")][0]
    assert sub["writeautomaticsub"] is True and sub["subtitleslangs"][0] == "en"


def test_finalize_uses_en_orig_when_no_en(tmp_path):
    d = str(tmp_path)
    vid = "vid00000002"
    open(os.path.join(d, f"{vid}.partial.mp4"), "wb").write(b"x")
    open(os.path.join(d, f"{vid}.partial.en-orig.vtt"), "w").write("WEBVTT\n")
    saving.finalize(d, vid, {"title": "t"}, None)
    assert sorted(os.listdir(d)) == [f"{vid}.en.vtt", f"{vid}.info.json", f"{vid}.mp4"]
