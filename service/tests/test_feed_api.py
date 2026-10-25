import time
from datetime import timedelta

from fastapi.testclient import TestClient

from subfeed import db
from subfeed.api import create_app

from .conftest import tab

VIDEO_KEYS = {
    "id", "channel_id", "channel_title", "category_id", "title", "published_at", "duration_s",
    "thumbnail_url", "position_s", "max_position_s", "watched", "save_state", "unavailable_reason",
}


def feed(client):
    r = client.get("/api/feed")
    assert r.status_code == 200
    return r.json()


def test_feed_excludes_hidden_and_ineligible(client, make):
    ok = make.channel()
    hidden = make.channel(hidden=1)
    v_ok = make.video(ok)
    v_inel = make.video(ok, eligible=0)
    v_hid = make.video(hidden)
    f = feed(client)
    assert tab(f, "all")["video_ids"] == [v_ok]
    assert v_inel not in f["videos"] and v_hid not in f["videos"]


def test_video_object_has_exactly_the_contract_fields(client, make):
    v = make.video(make.channel(name="Fireship"))
    obj = feed(client)["videos"][v]
    assert set(obj) == VIDEO_KEYS
    assert obj["channel_title"] == "Fireship"
    assert obj["published_at"].endswith("Z") and "T" in obj["published_at"]


def test_feed_tab_order(client, make):
    c2 = make.category("Gaming", position=2)
    c1 = make.category("AI", position=1)
    make.category("Proposed", position=3, approved=0)
    f = feed(client)
    assert [t["id"] for t in f["tabs"]] == ["all", "continue", f"cat-{c1}", f"cat-{c2}", "other", "saved", "history"]
    assert [t["kind"] for t in f["tabs"]] == ["all", "continue", "category", "category", "other", "saved", "history"]


def test_categories_other_and_membership(client, make):
    ai = make.category("AI", 1)
    c_ai = make.channel(category_id=ai)
    c_none = make.channel()
    va, vn = make.video(c_ai), make.video(c_none)
    f = feed(client)
    assert tab(f, f"cat-{ai}")["video_ids"] == [va]
    assert tab(f, "other")["video_ids"] == [vn]
    assert set(tab(f, "all")["video_ids"]) == {va, vn}
    assert f["videos"][va]["category_id"] == ai


def test_saved_visible_when_channel_hidden(client, make):
    hidden = make.channel(hidden=1)
    v = make.video(hidden)
    make.save(v, "ready")
    f = feed(client)
    assert tab(f, "saved")["video_ids"] == [v]
    assert v not in tab(f, "all")["video_ids"]
    assert f["videos"][v]["save_state"] == "ready"


def test_failed_save_is_not_in_saved_tab(client, make):
    v = make.video(make.channel())
    make.save(v, "failed")
    assert tab(feed(client), "saved")["video_ids"] == []


def test_row_order_unwatched_then_watched(client, make):
    ch = make.channel()
    newest_unwatched = make.video(ch, age_min=1)
    older_unwatched = make.video(ch, age_min=50)
    watched_new = make.video(ch, age_min=0)
    make.progress(watched_new, 500, watched=1)
    t = tab(feed(client), "all")
    assert t["video_ids"] == [newest_unwatched, older_unwatched, watched_new]


def test_new_count_since_seen(client, make):
    ch = make.channel()
    old = db.iso(db.utcnow() - timedelta(hours=2))
    make.video(ch, discovered_at=old)
    make.video(ch, discovered_at=old)
    client.post("/api/tabs/all/seen")
    make.video(ch)  # discovered after the tab was marked seen
    f = feed(client)
    assert tab(f, "all")["new_count"] == 1
    assert tab(f, "other")["new_count"] == 3  # other was never marked seen
    assert client.post("/api/tabs/all/seen").status_code == 204
    assert tab(feed(client), "all")["new_count"] == 0


def test_manual_category_sticks(client, make):
    ai = make.category("AI", 1)
    ch = make.channel()
    r = client.put(f"/api/channels/{ch}", json={"category_id": ai})
    assert r.status_code == 200
    row = next(c for c in client.get("/api/channels").json() if c["id"] == ch)
    assert row == {"id": ch, "name": row["name"], "category_id": ai, "category_source": "manual", "hidden": False,
                   "video_count": 0, "unwatched_count": 0, "last_upload_at": None, "last_watched_at": None}
    # hide-only does not change category_source
    other = make.channel()
    client.put(f"/api/channels/{other}", json={"hidden": True})
    row = next(c for c in client.get("/api/channels").json() if c["id"] == other)
    assert row["category_source"] == "auto" and row["hidden"] is True
    # clearing the category is also a manual choice
    client.put(f"/api/channels/{ch}", json={"category_id": None})
    row = next(c for c in client.get("/api/channels").json() if c["id"] == ch)
    assert row["category_id"] is None and row["category_source"] == "manual"


def test_channel_errors_use_sentence_body(client):
    r = client.put("/api/channels/UCnope", json={"hidden": True})
    assert r.status_code == 404 and set(r.json()) == {"sentence"}


def test_create_category(client):
    a = client.post("/api/categories", json={"name": "  Woodworking "}).json()
    b = client.post("/api/categories", json={"name": "Music"}).json()
    again = client.post("/api/categories", json={"name": "woodworking"}).json()
    assert a["name"] == "Woodworking" and b["id"] != a["id"] and again["id"] == a["id"]
    assert client.post("/api/categories", json={"name": "  "}).status_code == 422


def test_progress_watched_threshold(client, make):
    ch = make.channel()
    v = make.video(ch, duration=1000)
    # content duration 900: 95% = 855, but the 75 s tail rule (900 >= 300) trips first at 825
    assert client.post(f"/api/videos/{v}/progress", json={"position_s": 800, "duration_s": 1000, "sponsor_s": 100}).json() == {"watched": False}
    assert client.post(f"/api/videos/{v}/progress", json={"position_s": 826, "duration_s": 1000, "sponsor_s": 100}).json() == {"watched": True}
    short = make.video(ch, duration=40)
    assert client.post(f"/api/videos/{short}/progress", json={"position_s": 25, "duration_s": 40, "sponsor_s": 0}).json() == {"watched": False}
    ended = make.video(ch, duration=300)
    assert client.post(f"/api/videos/{ended}/progress", json={"position_s": 300, "duration_s": 300, "sponsor_s": 0, "ended": True}).json() == {"watched": True}


def test_first_thirty_seconds_never_count(client, make):
    v = make.video(make.channel(), duration=61)
    r = client.post(f"/api/videos/{v}/progress", json={"position_s": 29, "duration_s": 61, "sponsor_s": 0})
    assert r.json() == {"watched": False}


def test_progress_is_monotonic_and_watched_sticks(client, make, conn):
    v = make.video(make.channel(), duration=1000)
    client.post(f"/api/videos/{v}/progress", json={"position_s": 960, "duration_s": 1000, "sponsor_s": 0})
    r = client.post(f"/api/videos/{v}/progress", json={"position_s": 100, "duration_s": 1000, "sponsor_s": 0})
    assert r.json() == {"watched": True}  # a late retry of an older post does not undo it
    row = conn.execute("SELECT max_position_s FROM watch WHERE video_id=?", (v,)).fetchone()
    assert row[0] == 960


def test_sponsor_s_freezes_at_first_value(client, make, conn):
    v = make.video(make.channel(), duration=1000)
    client.post(f"/api/videos/{v}/progress", json={"position_s": 100, "duration_s": 1000, "sponsor_s": 200})
    client.post(f"/api/videos/{v}/progress", json={"position_s": 110, "duration_s": 1000, "sponsor_s": 0})
    assert conn.execute("SELECT sponsor_s FROM watch WHERE video_id=?", (v,)).fetchone()[0] == 200


def test_mark_watched_and_unwatched_override(client, make):
    v = make.video(make.channel(), duration=1000)
    assert client.post(f"/api/videos/{v}/watched", json={"watched": True}).status_code == 204
    assert feed(client)["videos"][v]["watched"] is True
    assert client.post(f"/api/videos/{v}/watched", json={"watched": False}).status_code == 204
    obj = feed(client)["videos"][v]
    assert obj["watched"] is False and obj["position_s"] == 0 and obj["max_position_s"] == 0


def test_unknown_video_progress_404(client):
    r = client.post("/api/videos/nope/progress", json={"position_s": 1, "duration_s": 2, "sponsor_s": 0})
    assert r.status_code == 404 and set(r.json()) == {"sentence"}


def test_auth_required_except_health(settings, conn):
    with TestClient(create_app(settings)) as bare:
        for method, path in [("get", "/api/feed"), ("get", "/api/channels"),
                             ("post", "/api/tabs/all/seen"), ("post", "/api/categories")]:
            r = getattr(bare, method)(path)
            assert r.status_code == 401 and set(r.json()) == {"sentence"}, path
        assert bare.get("/api/feed", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert bare.get("/api/feed", headers={"Authorization": "Basic " + "test-token-not-real"}).status_code == 401
        assert bare.get("/api/health").status_code == 200
        assert bare.get("/api/feed", headers={"Authorization": "Bearer test-token-not-real"}).status_code == 200


def test_no_token_configured_denies_everything(settings):
    settings.token = ""
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/feed", headers={"Authorization": "Bearer "}).status_code == 401
        assert c.get("/api/feed").status_code == 401


def test_health_shape_and_nothing_sensitive(client):
    h = client.get("/api/health", headers={}).json()
    assert {"ok", "last_sub_sync", "last_poll", "last_canary", "canary_ok", "ytdlp_version",
            "saved_bytes", "saved_quota_bytes", "nas_mounted"} <= set(h)
    assert h["ok"] is True and h["canary_ok"] is None and h["nas_mounted"] is False
    assert "test-token-not-real" not in str(h)


def test_not_found_route_uses_sentence_body(client):
    r = client.get("/api/nothing-here")
    assert r.status_code in (401, 404)
    assert set(r.json()) == {"sentence"}


def test_feed_perf_300_channels(client, make, conn):
    ai = make.category("AI", 1)
    conn.execute("BEGIN")
    for i in range(300):
        ch = make.channel(category_id=ai if i % 2 else None)
        for j in range(25):
            make.video(ch, age_min=j)
    conn.execute("COMMIT")
    client.get("/api/feed")  # warm
    t0 = time.perf_counter()
    r = client.get("/api/feed")
    elapsed = time.perf_counter() - t0
    assert r.status_code == 200 and len(r.json()["videos"]) == 7500
    assert elapsed < 0.3, f"feed took {elapsed*1000:.0f} ms"
