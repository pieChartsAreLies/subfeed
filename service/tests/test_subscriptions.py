import json
import os
import stat

import pytest
from fastapi.testclient import TestClient

from subfeed import db, google_auth, subscriptions
from subfeed.api import create_app

from .conftest import AUTH

REFRESH = "refresh-secret-xyz"
ACCESS = "access-secret-abc"
NOT_SET_UP = "Google sign-in isn't set up yet."


def uc(n):
    return f"UC{n:022d}"


def item(n):
    return {"snippet": {"title": f"Sub {n}", "description": "d", "resourceId": {"channelId": uc(n)}}}


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


def fake_google(monkeypatch, pages, fail_at=None, status=200):
    """pages: list of item lists, served via nextPageToken. Token refresh always succeeds."""
    calls = []
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (200, {"access_token": ACCESS}))

    def get(path, params, token):
        calls.append((path, dict(params)))
        if path == "channels":
            return 200, {"items": [{"snippet": {"title": "Alex"}}]}
        i = int(params.get("pageToken", 0))
        if fail_at is not None and i == fail_at:
            return 500, {}
        if status != 200:
            return status, {}
        body = {"items": pages[i]}
        if i + 1 < len(pages):
            body["nextPageToken"] = str(i + 1)
        return 200, body

    monkeypatch.setattr(subscriptions, "_get", get)
    return calls


def test_sync_paginates(conn, signed_in, monkeypatch):
    calls = fake_google(monkeypatch, [[item(1), item(2)], [item(3)], [item(4)]])
    res = subscriptions.sync(conn, signed_in)
    assert res == {"added": 4, "removed": 0, "kept": 0}
    sub_calls = [p for path, p in calls if path == "subscriptions"]
    assert len(sub_calls) == 3 and sub_calls[0]["mine"] == "true" and sub_calls[0]["maxResults"] == 50
    assert conn.execute("SELECT COUNT(*) FROM channels WHERE subscribed=1").fetchone()[0] == 4
    # new channels are due for backfill immediately
    assert conn.execute("SELECT COUNT(*) FROM channels WHERE next_poll_at IS NULL").fetchone()[0] == 4
    assert db.get_kv(conn, "last_sub_sync")
    assert db.get_kv(conn, "google_account") == "Alex"


def test_unsubscribed_kept_for_saved(conn, make, signed_in, monkeypatch):
    gone = make.channel(cid=uc(9))
    v = make.video(gone)
    make.save(v)
    keep = make.channel(cid=uc(1))
    fake_google(monkeypatch, [[item(1)]])
    res = subscriptions.sync(conn, signed_in)
    assert res["removed"] == 1 and res["kept"] == 1
    assert conn.execute("SELECT subscribed FROM channels WHERE id=?", (gone,)).fetchone()[0] == 0
    assert conn.execute("SELECT subscribed FROM channels WHERE id=?", (keep,)).fetchone()[0] == 1
    assert conn.execute("SELECT state FROM saves WHERE video_id=?", (v,)).fetchone()[0] == "ready"
    assert conn.execute("SELECT COUNT(*) FROM videos WHERE id=?", (v,)).fetchone()[0] == 1


def test_auth_failure_keeps_list(conn, make, signed_in, monkeypatch):
    make.channel(cid=uc(1))
    make.channel(cid=uc(2))
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (400, {"error": "invalid_grant"}))
    with pytest.raises(google_auth.AuthError):
        subscriptions.sync(conn, signed_in)
    assert conn.execute("SELECT COUNT(*) FROM channels WHERE subscribed=1").fetchone()[0] == 2
    assert db.get_kv(conn, "auth_error")
    assert db.get_kv(conn, "last_sub_sync") is None


def test_401_from_subscriptions_sets_auth_error(conn, make, signed_in, monkeypatch):
    make.channel(cid=uc(1))
    fake_google(monkeypatch, [[item(1)]], status=401)
    with pytest.raises(google_auth.AuthError):
        subscriptions.sync(conn, signed_in)
    assert db.get_kv(conn, "auth_error")
    assert conn.execute("SELECT subscribed FROM channels").fetchone()[0] == 1


def test_midway_error_changes_nothing(conn, make, signed_in, monkeypatch):
    make.channel(cid=uc(9))
    fake_google(monkeypatch, [[item(1)], [item(2)], [item(3)]], fail_at=2)
    with pytest.raises(ConnectionError):
        subscriptions.sync(conn, signed_in)
    assert conn.execute("SELECT id, subscribed FROM channels").fetchall()[0]["subscribed"] == 1
    assert conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0] == 1


def test_zero_subscriptions_changes_nothing(conn, make, signed_in, monkeypatch):
    make.channel(cid=uc(9))
    fake_google(monkeypatch, [[]])
    with pytest.raises(ConnectionError):
        subscriptions.sync(conn, signed_in)
    assert conn.execute("SELECT subscribed FROM channels").fetchone()[0] == 1
    assert db.get_kv(conn, "last_sub_sync") is None


def test_seed_transition_dedupes_and_summarizes(conn, make, signed_in, monkeypatch):
    cat = make.category("AI", position=1)
    both = make.channel(cid=uc(1), category_id=cat, source="manual")
    v = make.video(both)
    make.progress(v, 120)
    seed_only = make.channel(cid=uc(2))
    fake_google(monkeypatch, [[item(1), item(3)]])
    res = subscriptions.sync(conn, signed_in)
    assert res == {"added": 1, "removed": 1, "kept": 1}
    row = conn.execute("SELECT category_id, category_source, subscribed FROM channels WHERE id=?", (both,)).fetchone()
    assert (row[0], row[1], row[2]) == (cat, "manual", 1)
    assert conn.execute("SELECT position_s FROM watch WHERE video_id=?", (v,)).fetchone()[0] == 120
    assert conn.execute("SELECT subscribed FROM channels WHERE id=?", (seed_only,)).fetchone()[0] == 0
    assert json.loads(db.get_kv(conn, "import_summary")) == res
    # a later sync does not rewrite the one-time summary
    fake_google(monkeypatch, [[item(1), item(3), item(4)]])
    again = subscriptions.sync(conn, signed_in)
    assert again["added"] == 1
    assert json.loads(db.get_kv(conn, "import_summary")) == res


def test_resubscribe_restores_channel(conn, make, signed_in, monkeypatch):
    c = make.channel(cid=uc(1), subscribed=0)
    fake_google(monkeypatch, [[item(1)]])
    subscriptions.sync(conn, signed_in)
    assert conn.execute("SELECT subscribed FROM channels WHERE id=?", (c,)).fetchone()[0] == 1


def test_token_stored_in_file_0600_not_db(conn, gsettings, monkeypatch):
    flow = google_auth.DeviceFlow(gsettings, gsettings.db_path)
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (200, {"refresh_token": REFRESH, "access_token": ACCESS}))
    assert flow.poll_once("dev") == "done"
    assert stat.S_IMODE(os.stat(gsettings.google_token_path).st_mode) == 0o600
    assert REFRESH in open(gsettings.google_token_path).read()
    dump = "\n".join(str(tuple(r)) for r in conn.execute("SELECT key, value FROM kv"))
    assert REFRESH not in dump and ACCESS not in dump


def test_device_poll_states(gsettings, monkeypatch):
    flow = google_auth.DeviceFlow(gsettings, gsettings.db_path)
    for err, want in [("authorization_pending", "pending"), ("slow_down", "slow_down"),
                      ("access_denied", "failed"), ("expired_token", "failed")]:
        monkeypatch.setattr(google_auth, "_post", lambda url, data, e=err: (428, {"error": e}))
        assert flow.poll_once("dev") == want
    assert not os.path.exists(gsettings.google_token_path)


def test_not_configured_503(client):
    r = client.post("/api/auth/device/start")
    assert r.status_code == 503 and r.json() == {"sentence": NOT_SET_UP}
    s = client.get("/api/auth/status").json()
    assert s["signed_in"] is False and s["error"] == NOT_SET_UP


def test_device_start_returns_code(gsettings, monkeypatch):
    posted = []

    def post(url, data):
        posted.append((url, data))
        return 200, {"device_code": "DEV-SECRET", "user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device",
                     "expires_in": 1800, "interval": 3600}

    monkeypatch.setattr(google_auth, "_post", post)
    with TestClient(create_app(gsettings), headers=AUTH) as c:
        r = c.post("/api/auth/device/start")
    assert r.status_code == 200
    assert r.json() == {"user_code": "ABCD-EFGH", "verification_url": "https://www.google.com/device", "expires_in": 1800}
    assert posted[0][0] == google_auth.DEVICE_URL and posted[0][1]["scope"].endswith("youtube.readonly")


def test_status_signed_in_and_import_summary(signed_in, conn):
    db.set_kv(conn, "google_account", "Alex")
    db.set_kv(conn, "import_summary", json.dumps({"added": 3, "removed": 2, "kept": 17}))
    with TestClient(create_app(signed_in), headers=AUTH) as c:
        s = c.get("/api/auth/status").json()
    assert s == {"signed_in": True, "signing_in": False, "account": "Alex", "error": None,
                 "sync_error": None, "import_summary": {"added": 3, "removed": 2, "kept": 17}}


def test_auth_routes_need_bearer(gsettings):
    with TestClient(create_app(gsettings)) as c:
        assert c.get("/api/auth/status").status_code == 401
        assert c.post("/api/auth/device/start").status_code == 401


def test_token_never_in_responses(signed_in, conn, make, monkeypatch):
    """Scan every GET route and the auth routes: neither the refresh token, an access token,
    the client secret, nor the device code may appear in any body."""
    ch = make.channel(cid=uc(1))
    vid = make.video(ch)
    db.set_kv(conn, "auth_error", "boom")
    from subfeed import ytdl
    monkeypatch.setattr(ytdl, "video_meta", lambda video_id, priority=None: {"description": "d", "chapters": []})
    monkeypatch.setattr(google_auth, "_post", lambda url, data: (200, {
        "device_code": "DEV-SECRET", "user_code": "ABCD", "verification_url": "https://www.google.com/device",
        "expires_in": 1800, "interval": 3600, "access_token": ACCESS}))
    secrets = [REFRESH, ACCESS, "csecret", "DEV-SECRET"]
    app = create_app(signed_in)

    def walk(routes):
        # FastAPI 0.16x wraps each include_router() in an _IncludedRouter whose real routes
        # sit under .original_router; walk into it so the scan sees every /api and /media route
        for r in routes:
            inner = getattr(r, "original_router", None)
            if inner is not None:
                yield from walk(inner.routes)
            else:
                yield r

    import collections
    fill = collections.defaultdict(lambda: "x", video_id=vid, channel_id=ch, tab_id="all")
    routes = [(r.path, r.methods) for r in walk(app.routes)
              if getattr(r, "methods", None) and r.path.startswith(("/api", "/media"))]
    get_paths = sorted({p for p, m in routes if "GET" in m})
    assert any(p.startswith("/media") for p in get_paths), get_paths
    assert "/api/auth/status" in get_paths and "/api/videos/{video_id}/detail" in get_paths
    with TestClient(app, headers=AUTH) as c:
        bodies = [c.get(p.format_map(fill)).text for p in get_paths]
        bodies.append(c.post("/api/auth/device/start").text)
    blob = "\n".join(bodies)
    for s in secrets:
        assert s not in blob
    assert len(get_paths) >= 7
