from datetime import timedelta

from subfeed import db

from .conftest import tab


def feed(client):
    r = client.get("/api/feed")
    assert r.status_code == 200
    return r.json()


def ago(**kw):
    return db.iso(db.utcnow() - timedelta(**kw))


def test_in_progress_leaves_rows_and_appears_in_continue(client, make):
    cat = make.category("Tech")
    ch = make.channel(category_id=cat)
    fresh = make.video(ch, age_min=1)
    prog = make.video(ch, age_min=5)
    make.progress(prog, 200, last_played_at=ago(minutes=1), played_at=ago(minutes=1))
    f = feed(client)
    assert tab(f, "all")["video_ids"] == [fresh]
    assert tab(f, f"cat-{cat}")["video_ids"] == [fresh]
    assert tab(f, "continue")["video_ids"] == [prog]
    assert prog in f["videos"]


def test_in_progress_uses_max_position_not_raw(client, make):
    ch = make.channel()
    v = make.video(ch)
    # resume point raw 20, furthest content position 200
    make.progress(v, 20, max_position=200, last_played_at=ago(minutes=1), played_at=ago(minutes=1))
    assert tab(feed(client), "continue")["video_ids"] == [v]
    w = make.video(ch, age_min=3)
    make.progress(w, 200, max_position=20, last_played_at=ago(minutes=1))
    assert w not in tab(feed(client), "continue")["video_ids"]


def test_continue_ordered_by_last_played(client, make):
    ch = make.channel()
    a, b = make.video(ch, age_min=1), make.video(ch, age_min=2)
    make.progress(a, 100, last_played_at=ago(hours=5), played_at=ago(hours=5))
    make.progress(b, 100, last_played_at=ago(hours=1), played_at=ago(hours=1))
    assert tab(feed(client), "continue")["video_ids"] == [b, a]


def test_continue_includes_hidden_and_unsubscribed_channels(client, make, conn):
    hid = make.channel(hidden=1)
    uns = make.channel(subscribed=0)
    a, b = make.video(hid, age_min=1), make.video(uns, age_min=2)
    make.progress(a, 100, last_played_at=ago(hours=1), played_at=ago(hours=1))
    make.progress(b, 100, last_played_at=ago(hours=2), played_at=ago(hours=2))
    f = feed(client)
    assert tab(f, "continue")["video_ids"] == [a, b]
    assert tab(f, "all")["video_ids"] == []


def test_evicted_played_in_continue_and_history_not_rows(client, make, conn):
    ch = make.channel()
    v = make.video(ch)
    make.progress(v, 100, last_played_at=ago(hours=1), played_at=ago(hours=1))
    conn.execute("UPDATE videos SET evicted=1 WHERE id=?", (v,))
    f = feed(client)
    assert tab(f, "continue")["video_ids"] == [v]
    assert tab(f, "history")["video_ids"] == [v]
    w = make.video(ch, age_min=3)
    make.progress(w, 500, watched=1, last_played_at=ago(hours=1), played_at=ago(hours=1))
    conn.execute("UPDATE videos SET evicted=1 WHERE id=?", (w,))
    f = feed(client)
    assert w not in tab(f, "all")["video_ids"] and w in tab(f, "history")["video_ids"]


def test_stale_returns_to_row_unwatched(client, make):
    ch = make.channel()
    v = make.video(ch)
    make.progress(v, 200, last_played_at=ago(days=15), played_at=ago(days=15))
    f = feed(client)
    assert tab(f, "all")["video_ids"] == [v]
    assert tab(f, "continue")["video_ids"] == []
    assert tab(f, "history")["video_ids"] == [v]


def test_live_never_in_continue(client, make, conn):
    ch = make.channel()
    v = make.video(ch)
    conn.execute("UPDATE videos SET live_status='is_live' WHERE id=?", (v,))
    make.progress(v, 200, last_played_at=ago(minutes=1))
    f = feed(client)
    assert tab(f, "continue")["video_ids"] == []
    assert tab(f, "all")["video_ids"] == [v]


def test_rows_keep_watched_dimmed_at_end(client, make):
    ch = make.channel()
    new = make.video(ch, age_min=5)
    done = make.video(ch, age_min=0)
    make.progress(done, 580, watched=1, last_played_at=ago(hours=1), played_at=ago(hours=1))
    assert tab(feed(client), "all")["video_ids"] == [new, done]


def test_history_lists_played_newest_first_limit_200(client, make, conn):
    ch = make.channel()
    ids = []
    for i in range(205):
        v = make.video(ch, age_min=i)
        ids.append(v)
        make.progress(v, 100, last_played_at=ago(minutes=i), played_at=ago(minutes=i))
    h = tab(feed(client), "history")["video_ids"]
    assert len(h) == 200 and h == ids[:200]


def test_history_excludes_under_30s_and_mark_watched_only(client, make):
    ch = make.channel()
    short = make.video(ch, age_min=1)
    marked = make.video(ch, age_min=2)
    make.progress(short, 20)               # never past 30 s: no played_at
    make.progress(marked, 0, watched=1)    # mark-watched only: no played_at
    assert tab(feed(client), "history")["video_ids"] == []


def test_saved_started_video_in_saved_and_continue(client, make):
    ch = make.channel()
    v = make.video(ch)
    make.save(v)
    make.progress(v, 200, last_played_at=ago(minutes=1), played_at=ago(minutes=1))
    f = feed(client)
    assert tab(f, "saved")["video_ids"] == [v]
    assert tab(f, "continue")["video_ids"] == [v]


def test_tab_order(client, make):
    make.category("Tech")
    ids = [t["id"] for t in feed(client)["tabs"]]
    assert ids[:2] == ["all", "continue"] and ids[-2:] == ["saved", "history"]
    t = tab(feed(client), "continue")
    assert t["kind"] == "continue" and t["name"] == "Continue Watching" and t["new_count"] == 0
