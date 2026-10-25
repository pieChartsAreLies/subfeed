from subfeed import db

LATEST = 4  # number of migrations in schema.sql


def test_connect_sets_wal_foreign_keys_and_version(tmp_path):
    c = db.connect(str(tmp_path / "x.db"))
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert c.execute("SELECT MAX(version) FROM schema_version").fetchone()[0] == LATEST
    names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"channels", "categories", "videos", "watch", "saves", "tab_seen", "kv"} <= names


def test_connect_twice_does_not_remigrate(tmp_path):
    p = str(tmp_path / "x.db")
    db.connect(p).close()
    c = db.connect(p)
    assert c.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == LATEST


def test_deleting_a_video_cascades_to_watch_and_saves(conn, make):
    ch = make.channel()
    v = make.video(ch)
    make.progress(v, 100)
    make.save(v)
    conn.execute("DELETE FROM videos WHERE id=?", (v,))
    assert conn.execute("SELECT COUNT(*) FROM watch").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM saves").fetchone()[0] == 0
