"""Captions on /play and the chapter contract shape (TV fix round 1)."""
import os

from subfeed import ytdl

from .test_playback import INFO, play_env  # noqa: F401  (play_env is a fixture)


def test_captions_pick_original_and_english_prefer_manual_vtt():
    d = {
        "language": "de",
        "subtitles": {
            "en-US": [{"ext": "json3", "url": "u-json"}, {"ext": "vtt", "url": "u-en-manual"}],
        },
        "automatic_captions": {
            "de-orig": [{"ext": "srv3", "url": "x"}, {"ext": "vtt", "url": "u-de-auto"}],
            "fr": [{"ext": "vtt", "url": "u-fr"}],
            "en": [{"ext": "vtt", "url": "u-en-auto"}],
        },
    }
    assert ytdl.captions(d) == [
        {"lang": "de", "url": "u-de-auto", "mime": "text/vtt", "auto": True},
        {"lang": "en", "url": "u-en-manual", "mime": "text/vtt", "auto": False},
    ]
    # no vtt anywhere -> nothing offered; no language info -> English only
    assert ytdl.captions({"subtitles": {"en": [{"ext": "srv3", "url": "x"}]}}) == []
    assert [c["lang"] for c in ytdl.captions({"automatic_captions": {"en": [{"ext": "vtt", "url": "a"}]}})] == ["en"]


def test_chapters_normalised_to_contract_shape():
    raw = [{"start_time": 0, "end_time": 99, "title": "Intro"}, {"start_time": 99.5, "title": "Build"}, {"title": "no start"}]
    assert ytdl.normalize_chapters(raw) == [{"start": 0.0, "title": "Intro"}, {"start": 99.5, "title": "Build"}]
    assert ytdl.normalize_chapters([{"start": 3, "title": "x"}]) == [{"start": 3.0, "title": "x"}]
    assert ytdl.normalize_chapters(None) == []


def test_play_youtube_passes_captions_and_normalised_chapters(play_env):  # noqa: F811
    play_env.fake.result = {**INFO, "chapters": [{"start_time": 5, "title": "Raw"}],
                            "captions": [{"lang": "en", "url": "https://www.youtube.com/api/timedtext?x",
                                          "mime": "text/vtt", "auto": True}]}
    b = play_env.post(f"/api/videos/{play_env.vid}/play", json={"decoders": ["avc1"]}).json()
    assert b["chapters"] == [{"start": 5.0, "title": "Raw"}]
    assert b["captions"][0]["lang"] == "en" and b["captions"][0]["mime"] == "text/vtt"


def test_saved_play_offers_sidecar_captions_and_serves_them(play_env, make):  # noqa: F811
    vid = play_env.vid
    open(os.path.join(play_env.dir, f"{vid}.mp4"), "wb").write(b"x" * 100)
    open(os.path.join(play_env.dir, f"{vid}.en.vtt"), "w").write("WEBVTT\n\n00:00.000 --> 00:01.000\nhi\n")
    open(os.path.join(play_env.dir, f"{vid}.info.json"), "w").write(
        '{"duration": 690, "chapters": [{"start_time": 0, "title": "A"}]}')
    make.save(vid, "ready")
    b = play_env.post(f"/api/videos/{vid}/play", json={"decoders": ["avc1"]}).json()
    assert b["source"] == "saved"
    assert b["chapters"] == [{"start": 0.0, "title": "A"}]
    assert len(b["captions"]) == 1 and b["captions"][0]["url"].endswith(f"/media/saved/{vid}.en.vtt")
    r = play_env.get(f"/media/saved/{vid}.en.vtt")
    assert r.status_code == 200 and r.text.startswith("WEBVTT") and r.headers["content-type"].startswith("text/vtt")
    assert play_env.get(f"/media/saved/{vid}.en.vtt", headers={"Authorization": "Bearer wrong"}).status_code == 401
    os.remove(os.path.join(play_env.dir, f"{vid}.en.vtt"))
    assert play_env.post(f"/api/videos/{vid}/play", json={"decoders": ["avc1"]}).json()["captions"] == []
    assert play_env.get(f"/media/saved/{vid}.en.vtt").status_code == 404
