import os

import pytest
from fastapi.testclient import TestClient

from subfeed import db, playback, sponsorblock, ytdl
from subfeed.api import create_app

from .conftest import AUTH


def test_channels_enriched_and_sorted(client, make):
    b = make.channel(name="bravo")
    a = make.channel(name="Alpha")
    make.channel(name="gone", subscribed=0)
    v1 = make.video(a, age_min=10)
    v2 = make.video(a, age_min=0)
    make.video(a, age_min=5, eligible=0)
    make.progress(v1, 600, watched=1, played_at="2026-10-01T00:00:00Z", last_played_at="2026-10-02T00:00:00Z")
    rows = client.get("/api/channels").json()
    assert [r["name"] for r in rows] == ["Alpha", "bravo"]
    r = rows[0]
    assert (r["video_count"], r["unwatched_count"]) == (2, 1)
    assert r["last_watched_at"] == "2026-10-02T00:00:00Z"
    assert r["last_upload_at"] and r["category_source"] == "auto" and r["hidden"] is False
    assert rows[1]["video_count"] == 0 and rows[1]["unwatched_count"] == 0 and rows[1]["last_upload_at"] is None and rows[1]["last_watched_at"] is None


def test_channel_videos_hidden_channel_with_states(client, make):
    ch = make.channel(hidden=1)
    new = make.video(ch, age_min=0)
    prog = make.video(ch, age_min=1)
    done = make.video(ch, age_min=2)
    evicted = make.video(ch, age_min=3)
    make.progress(prog, 100, played_at="2026-10-01T00:00:00Z", last_played_at=db.iso(db.utcnow()))
    make.progress(done, 600, watched=1, played_at="2026-10-01T00:00:00Z", last_played_at="2026-10-01T00:00:00Z")
    make.conn.execute("UPDATE videos SET evicted=1 WHERE id=?", (evicted,))
    j = client.get(f"/api/channels/{ch}/videos").json()
    assert j["channel"]["id"] == ch and j["channel"]["hidden"] is True
    assert [v["id"] for v in j["videos"]] == [new, prog, done]
    assert [v["state"] for v in j["videos"]] == ["fresh", "in_progress", "watched"]
    assert j["total"] == 3


def test_channel_videos_paging_and_limit_cap(client, make):
    ch = make.channel()
    for i in range(150):
        make.video(ch, age_min=i)
    j = client.get(f"/api/channels/{ch}/videos?offset=100").json()
    assert j["total"] == 150 and len(j["videos"]) == 50
    assert len(client.get(f"/api/channels/{ch}/videos?limit=1000").json()["videos"]) == 150
    assert len(client.get(f"/api/channels/{ch}/videos?limit=120").json()["videos"]) == 120


def test_channel_videos_open_for_unsubscribed_channel(client, make):
    # History/Continue can hold videos of a channel the user later unsubscribed from
    ch = make.channel(name="gone", subscribed=0)
    v = make.video(ch, age_min=0, eligible=0)
    make.progress(v, 100, played_at="2026-10-01T00:00:00Z", last_played_at="2026-10-01T00:00:00Z")
    r = client.get(f"/api/channels/{ch}/videos")
    assert r.status_code == 200
    assert r.json()["channel"]["id"] == ch and [x["id"] for x in r.json()["videos"]] == [v]
    # the channels list itself stays subscribed-only
    assert ch not in [c["id"] for c in client.get("/api/channels").json()]


def test_channel_videos_unknown_404(client):
    r = client.get("/api/channels/nope/videos")
    assert r.status_code == 404 and r.json() == {"sentence": "That channel isn't in your subscriptions."}


@pytest.fixture
def play_client(settings, make, monkeypatch, tmp_path):
    settings.saved_dir = str(tmp_path / "saved")
    os.makedirs(settings.saved_dir)
    from .test_playback import INFO
    state = {"result": INFO}

    def fake(vid, priority=0):
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(ytdl, "play_info", fake)
    monkeypatch.setattr(sponsorblock, "fetch", lambda vid, dur, timeout=4.0: [])
    monkeypatch.setattr(playback, "client_host", lambda request: "10.0.0.30")
    ch = make.channel()
    vid = make.video(ch, vid="abcdefghijk", duration=690)
    make.progress(vid, 200, played_at="2026-10-01T00:00:00Z", last_played_at="2026-10-01T00:00:00Z")
    with TestClient(create_app(settings), headers=AUTH) as c:
        c.vid, c.state = vid, state
        yield c


def test_play_from_start_returns_zero_resume(play_client):
    body = {"decoders": ["avc1"]}
    assert play_client.post(f"/api/videos/{play_client.vid}/play", json=body).json()["resume_s"] == 200
    r = play_client.post(f"/api/videos/{play_client.vid}/play", json={**body, "from_start": True})
    assert r.json()["resume_s"] == 0


def test_failed_play_leaves_watch_unchanged(play_client, conn):
    play_client.state["result"] = RuntimeError("boom")
    q = "SELECT * FROM watch WHERE video_id=?"
    before = tuple(conn.execute(q, (play_client.vid,)).fetchone())
    r = play_client.post(f"/api/videos/{play_client.vid}/play", json={"from_start": True})
    assert r.status_code == 503
    assert tuple(conn.execute(q, (play_client.vid,)).fetchone()) == before
