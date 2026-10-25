from subfeed import categorize, discovery, subscriptions
from subfeed.discovery import RETAIN_PER_CHANNEL

from .test_subscriptions import fake_google, gsettings, item, signed_in, uc  # noqa: F401


def live(conn, ch):
    return conn.execute(
        "SELECT COUNT(*) FROM videos WHERE channel_id=? AND evicted=0", (ch,)
    ).fetchone()[0]


def test_played_video_never_evicted(conn, make):
    ch = make.channel()
    oldest = make.video(ch, age_min=10000)
    make.progress(oldest, 0, watched=1, played_at="2026-10-01T00:00:00Z", last_played_at="2026-10-01T00:00:00Z")
    for i in range(29):
        make.video(ch, age_min=i)
    discovery.enforce_retention(conn, ch, keep=RETAIN_PER_CHANNEL)
    assert conn.execute("SELECT evicted FROM videos WHERE id=?", (oldest,)).fetchone()[0] == 0


def test_hidden_channel_keeps_100(conn, make):
    assert discovery.RETAIN_HIDDEN == 100
    ch = make.channel(hidden=1)
    for i in range(120):
        make.video(ch, age_min=i)
    discovery.enforce_retention(conn, ch, keep=discovery.retention_for(conn, ch))
    assert live(conn, ch) == 100


def test_sync_keeps_hidden_and_manual_category(conn, make, signed_in, monkeypatch):  # noqa: F811
    cat = make.category("Sports")
    ch = make.channel(cid=uc(1), category_id=cat, hidden=1, source="manual")
    fake_google(monkeypatch, [[item(1)]])
    subscriptions.sync(conn, signed_in)
    row = conn.execute("SELECT hidden, category_id, category_source FROM channels WHERE id=?", (ch,)).fetchone()
    assert (row[0], row[1], row[2]) == (1, cat, "manual")


def test_categorizer_skips_manual(conn, make, monkeypatch):
    cat = make.category("Sports")
    make.category("Gaming")
    ch = make.channel(category_id=cat, source="manual")
    monkeypatch.setattr(categorize, "_complete", lambda *a: {"label": "Gaming"})
    assert categorize.assign(conn, ch) == cat
