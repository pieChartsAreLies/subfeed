import os

import pytest
from fastapi.testclient import TestClient

from subfeed import breaker, playback, sponsorblock, ytdl
from subfeed.api import create_app

from .conftest import AUTH


def fmt(fid, ext, vcodec="none", acodec="none", height=None, tbr=1000, **kw):
    return {"format_id": fid, "ext": ext, "vcodec": vcodec, "acodec": acodec, "height": height, "tbr": tbr,
            "abr": kw.pop("abr", None), "protocol": "https", "format_note": kw.pop("note", None),
            "language": kw.pop("language", None),
            "url": f"https://rr1.googlevideo.com/videoplayback?id={fid}&expire=1893456000",
            "http_headers": {"User-Agent": "UA"}, **kw}


FORMATS = [
    fmt("137", "mp4", "avc1.640028", height=1080, tbr=4000),
    fmt("248", "webm", "vp9", height=1080, tbr=2500),
    fmt("271", "webm", "vp9", height=1440, tbr=6000),
    fmt("313", "webm", "vp9", height=2160, tbr=12000),
    fmt("399", "mp4", "av01.0.08M.08", height=1080, tbr=1800),
    fmt("140-dub", "m4a", acodec="mp4a.40.2", abr=130, note="English (US) dubbed"),
    fmt("140", "m4a", acodec="mp4a.40.2", abr=129, note="medium, original (default)"),
    fmt("251", "webm", acodec="opus", abr=140),
    fmt("18", "mp4", "avc1.42001E", "mp4a.40.2", height=360),
]
INFO = {"duration": 690, "title": "T", "description": "d", "chapters": [{"start": 0.0, "title": "Intro"}],
        "formats": FORMATS}


@pytest.fixture
def play_env(settings, conn, make, monkeypatch, tmp_path):
    settings.saved_dir = str(tmp_path / "saved")
    os.makedirs(settings.saved_dir)
    calls = []

    def fake_play_info(vid, priority=0):
        calls.append(vid)
        r = fake_play_info.result
        if isinstance(r, Exception):
            raise r
        return r

    fake_play_info.result = INFO
    monkeypatch.setattr(ytdl, "play_info", fake_play_info)
    monkeypatch.setattr(sponsorblock, "fetch", lambda vid, dur, timeout=4.0: [{"start": 10.0, "end": 40.0}])
    monkeypatch.setattr(playback, "client_host", lambda request: "10.0.0.30")
    ch = make.channel()
    vid = make.video(ch, vid="abcdefghijk", duration=690)
    with TestClient(create_app(settings), headers=AUTH) as c:
        c.calls, c.fake, c.vid, c.dir = calls, fake_play_info, vid, settings.saved_dir
        yield c


def test_play_picks_decodable_format(play_env):
    r = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["vp9", "avc1"], "max_height": 2160})
    assert r.status_code == 200
    b = r.json()
    assert b["source"] == "youtube" and b["height"] == 2160 and "id=313" in b["video_url"]
    assert b["video_mime"] == "video/webm" and b["audio_mime"] == "audio/mp4"
    assert "id=140&" in b["audio_url"]                      # original-language AAC, not the dub, not opus
    assert b["headers"] == {"User-Agent": "UA"} and b["duration_s"] == 690
    assert b["sponsor"] == [{"start": 10.0, "end": 40.0}]
    assert b["chapters"] == [{"start": 0.0, "title": "Intro"}]
    assert b["session_id"] and b["expires_at"].startswith("2030-") and b["resume_s"] == 0


def test_play_prefers_av1_over_vp9_at_equal_height_and_respects_max_height(play_env):
    r = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["av01", "vp9", "avc1"], "max_height": 1080})
    b = r.json()
    assert b["height"] == 1080 and "id=399" in b["video_url"] and b["video_mime"] == "video/mp4"
    r = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"], "max_height": 2160})
    assert "id=137" in r.json()["video_url"]               # only H.264 decodable


def test_play_falls_back_to_progressive(play_env):
    play_env.fake.result = {**INFO, "formats": [f for f in FORMATS if f["format_id"] == "18"]}
    b = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"], "max_height": 2160}).json()
    assert "id=18" in b["video_url"] and b["audio_url"] is None and b["audio_mime"] is None


def test_play_prefers_saved(play_env, make):
    open(os.path.join(play_env.dir, f"{play_env.vid}.mp4"), "wb").write(b"x" * 100)
    make.save(play_env.vid, "ready")
    r = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"], "max_height": 2160})
    b = r.json()
    assert b["source"] == "saved" and b["video_url"].endswith(f"/media/saved/{play_env.vid}.mp4")
    assert b["audio_url"] is None and b["headers"]["Authorization"].startswith("Bearer ")
    assert play_env.calls == []                             # no extraction at all


def test_saved_row_without_file_falls_through_to_youtube(play_env, make):
    make.save(play_env.vid, "ready")                        # DB says ready, file is gone
    b = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"]}).json()
    assert b["source"] == "youtube"


def test_play_extraction_failure_message(play_env):
    play_env.fake.result = ytdl.YtdlError("boom")
    r = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"]})
    assert r.status_code == 503
    assert r.json() == {"sentence": "YouTube isn't letting us play videos right now. Your saved videos still work."}


def test_bot_check_trips_breaker_and_says_so(play_env, conn):
    play_env.fake.result = ytdl.BotCheckError("Sign in to confirm you're not a bot")
    r = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"]})
    assert r.status_code == 503 and r.json()["sentence"] == breaker.LIMITED_SENTENCE
    assert breaker.is_open(conn)


def test_open_breaker_still_tries_once_for_user_play(play_env, conn):
    breaker.trip(conn, "test")
    b = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"]})
    assert b.status_code == 200 and play_env.calls == [play_env.vid]


def test_unknown_video_404(play_env):
    r = play_env.post("/api/videos/nope1234567/play", json={"decoders": ["avc1"]})
    assert r.status_code == 404 and "sentence" in r.json()


def test_remote_without_proxy_gets_away_from_home_message(play_env, monkeypatch):
    monkeypatch.setattr(playback, "client_host", lambda request: "93.184.216.34")
    monkeypatch.setattr(playback, "PROXY_ENABLED", False)
    r = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"]})
    assert r.status_code == 503 and r.json() == {"sentence": "Watching away from home isn't set up yet."}
    assert play_env.calls == []


def test_is_local():
    assert playback.is_local("10.0.0.30") and playback.is_local("127.0.0.1") and not playback.is_local("testclient") and not playback.is_local("")
    assert not playback.is_local("93.184.216.34") and not playback.is_local("8.8.8.8")


def test_media_range_206_and_auth(play_env, make):
    make.save(play_env.vid, "ready")
    data = bytes(range(256)) * 40
    open(os.path.join(play_env.dir, f"{play_env.vid}.mp4"), "wb").write(data)
    url = f"/media/saved/{play_env.vid}.mp4"
    r = play_env.get(url, headers={"Range": "bytes=100-299"})
    assert r.status_code == 206 and r.content == data[100:300]
    assert r.headers["content-range"] == f"bytes 100-299/{len(data)}" and r.headers["accept-ranges"] == "bytes"
    assert play_env.get(url, headers={"Range": f"bytes={len(data) - 10}-"}).content == data[-10:]
    assert play_env.get(url, headers={"Range": "bytes=-5"}).content == data[-5:]
    assert play_env.get(url, headers={"Range": f"bytes={len(data) + 5}-"}).status_code == 416
    assert play_env.get(url).status_code == 200 and len(play_env.get(url).content) == len(data)
    assert play_env.get("/media/saved/zzzzzzzzzzz.mp4").status_code == 404
    assert play_env.get("/media/saved/..%2Fetc.mp4").status_code in (404, 422)
    assert play_env.get(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
