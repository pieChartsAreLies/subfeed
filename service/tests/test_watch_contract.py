import sqlite3
from datetime import timedelta

from subfeed import db, watch
from subfeed.db import utcnow


def iso(dt):
    return db.iso(dt)


def row(conn, vid):
    return conn.execute("SELECT * FROM watch WHERE video_id=?", (vid,)).fetchone()


def test_watched_at_95_not_949():
    # 3000 s video: 95% is 2850, and 2847 leaves 153 s, so the 75 s tail rule does not interfere
    assert not watch.is_watched(0.949 * 3000, 3000, ended=False)
    assert watch.is_watched(0.95 * 3000, 3000, ended=False)


def test_tail_rule_only_for_long_videos():
    assert watch.is_watched(1000 - 75, 1000, ended=False)        # 16 min video, 75 s left
    assert not watch.is_watched(31, 180, ended=False)             # 3 min video at 31 s (Review Focus 2)


def test_sponsor_skip_does_not_cross_threshold(conn, make):
    # 2000 s video, 60 s sponsor at 1800-1860; player jumps 1800 -> 1860 and posts raw 1860, content 1800.
    # Content duration 1940: 1800/1940 = 92.8%, 140 s left. Raw 1860/1940 would be 95.9%.
    v = make.video(make.channel(), duration=2000)
    assert watch.record_progress(conn, v, 1860, 2000, 60, content_position_s=1800) is False


def test_played_columns_set_only_past_30(conn, make):
    v = make.video(make.channel())
    watch.record_progress(conn, v, 12, 600, 0, content_position_s=12)
    assert row(conn, v)["played_at"] is None
    watch.record_progress(conn, v, 45, 600, 0, content_position_s=45)
    first = row(conn, v)
    assert first["played_at"] and first["last_played_at"]
    watch.record_progress(conn, v, 60, 600, 0, content_position_s=60)
    again = row(conn, v)
    assert again["played_at"] == first["played_at"]  # set once


def test_mark_actions_never_set_played(conn, make):
    v = make.video(make.channel())
    watch.set_watched(conn, v, True)
    watch.set_watched(conn, v, False)
    assert row(conn, v)["played_at"] is None and row(conn, v)["last_played_at"] is None


def test_classify_purges_after_14_days():
    now = utcnow()
    assert watch.classify(False, 300, 600, False, iso(now - timedelta(days=13)), now) == "in_progress"
    assert watch.classify(False, 300, 600, False, iso(now - timedelta(days=15)), now) == "stale"
    assert watch.classify(False, 300, None, False, iso(now), now) == "fresh"     # unknown duration
    assert watch.classify(False, 300, 600, True, iso(now), now) == "fresh"       # live
    assert watch.classify(True, 10, 600, False, None, now) == "watched"


def test_migration_backfills_old_rows_without_flooding(settings):
    old = iso(utcnow() - timedelta(days=30))
    c = sqlite3.connect(settings.db_path, isolation_level=None)
    c.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for version, sql in db._migrations():
        if version <= 3:
            for stmt in db._split(sql):
                c.execute(stmt)
            c.execute("INSERT INTO schema_version(version) VALUES (?)", (version,))
    c.execute("INSERT INTO channels(id, name) VALUES('UCold', 'Old')")
    c.execute("INSERT INTO videos(id, channel_id, title, published_at, duration_s, discovered_at) "
              "VALUES('vold', 'UCold', 't', ?, 600, ?)", (old, old))
    c.execute("INSERT INTO watch(video_id, position_s, max_position_s, duration_s, watched, updated_at) "
              "VALUES('vold', 200, 200, 600, 0, ?)", (old,))
    c.close()

    conn = db.connect(settings.db_path)
    r = row(conn, "vold")
    assert r["played_at"] == old and r["last_played_at"] == old
    assert watch.classify(bool(r["watched"]), r["position_s"], r["duration_s"], False,
                          r["last_played_at"], utcnow()) == "stale"
    conn.close()


def test_progress_route_accepts_content_position(client, make, conn):
    v = make.video(make.channel(), duration=2000)
    r = client.post(f"/api/videos/{v}/progress",
                    json={"position_s": 1860, "duration_s": 2000, "sponsor_s": 60, "content_position_s": 1800})
    assert r.json() == {"watched": False}
    assert row(conn, v)["max_position_s"] == 1800


def test_live_video_never_auto_watched_or_played(conn, make):
    ch = make.channel()
    v = make.video(ch, duration=3600)
    conn.execute("UPDATE videos SET live_status='is_live' WHERE id=?", (v,))
    assert watch.record_progress(conn, v, 3500, 3600, 0, ended=True, content_position_s=3500) is False
    r = row(conn, v)
    assert not r["watched"] and r["played_at"] is None and r["last_played_at"] is None
    watch.set_watched(conn, v, True)
    assert row(conn, v)["watched"] == 1


def test_exactly_30s_is_not_watched():
    assert not watch.is_watched(30, 31, ended=False)
