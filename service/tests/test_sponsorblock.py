import hashlib

import httpx

from subfeed import sponsorblock

PAYLOAD = [
    {"videoID": "abcdefghijk", "segments": [
        {"segment": [10, 40], "category": "sponsor", "actionType": "skip"},
        {"segment": [50, 60], "category": "selfpromo", "actionType": "skip"},
        {"segment": [0, 5], "category": "sponsor", "actionType": "mute"},
        {"segment": [900, 950], "category": "sponsor", "actionType": "skip"},   # past the end
        {"segment": [70, 70], "category": "sponsor", "actionType": "skip"},     # empty
    ]},
    {"videoID": "someoneelse", "segments": [{"segment": [1, 2], "category": "sponsor"}]},
]


def test_sponsor_filters_and_failure_empty(monkeypatch, conn):
    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["url"], seen["params"] = url, params
        return httpx.Response(200, json=PAYLOAD, request=httpx.Request("GET", url))

    monkeypatch.setattr(sponsorblock.httpx, "get", fake_get)
    assert sponsorblock.segments(conn, "abcdefghijk", 690) == [{"start": 10.0, "end": 40.0}]
    assert seen["url"].endswith("/" + hashlib.sha256(b"abcdefghijk").hexdigest()[:4])   # hash prefix, not the id
    assert "sponsor" in seen["params"]["categories"]

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(sponsorblock.httpx, "get", boom)
    assert sponsorblock.segments(conn, "otherother1", 690) == []
    # a failure is only cached briefly: expire it, then the next call gets real data
    conn.execute("DELETE FROM kv WHERE key LIKE 'sb:%'")
    monkeypatch.setattr(sponsorblock.httpx, "get", fake_get)
    PAYLOAD2 = [{"videoID": "otherother1", "segments": [{"segment": [1, 9], "category": "sponsor"}]}]
    monkeypatch.setattr(sponsorblock.httpx, "get",
                        lambda url, params=None, timeout=None: httpx.Response(200, json=PAYLOAD2, request=httpx.Request("GET", url)))
    assert sponsorblock.segments(conn, "otherother1", 690) == [{"start": 1.0, "end": 9.0}]


def test_404_means_no_segments(monkeypatch, conn):
    monkeypatch.setattr(sponsorblock.httpx, "get",
                        lambda url, params=None, timeout=None: httpx.Response(404, request=httpx.Request("GET", url)))
    assert sponsorblock.segments(conn, "abcdefghijk", 690) == []


def test_failed_lookup_cached_five_minutes(monkeypatch, conn):
    calls = []

    def boom(url, params=None, timeout=None):
        calls.append(1)
        raise httpx.ConnectError("down")

    monkeypatch.setattr(sponsorblock.httpx, "get", boom)
    assert sponsorblock.segments(conn, "abcdefghijk", 690) == []
    assert sponsorblock.segments(conn, "abcdefghijk", 690) == [] and len(calls) == 1   # outage costs one wait
    real = sponsorblock.time.time
    monkeypatch.setattr(sponsorblock.time, "time", lambda: real() + 301)
    sponsorblock.segments(conn, "abcdefghijk", 690)
    assert len(calls) == 2
