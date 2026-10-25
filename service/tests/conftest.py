import itertools
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from subfeed import db, pool
from subfeed.api import create_app
from subfeed.config import Settings

TOKEN = "test-token-not-real"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(autouse=True)
def _no_discovery_pacing(monkeypatch):
    """Production paces DISCOVERY jobs 4 s apart; tests that want it set it themselves."""
    monkeypatch.setattr(pool, "DISCOVERY_INTERVAL_S", 0.0)


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=str(tmp_path / "t.db"), token=TOKEN, scheduler=False,
                    save_worker=False, canary=False, saved_dir=str(tmp_path / "saved"))


@pytest.fixture
def conn(settings):
    c = db.connect(settings.db_path)
    yield c
    c.close()


@pytest.fixture
def client(settings, conn):
    with TestClient(create_app(settings), headers=AUTH) as c:
        yield c


_counter = itertools.count()


class Factory:
    """Inserts rows directly. Videos get descending published_at so ordering is predictable."""

    def __init__(self, conn):
        self.conn = conn
        self.base = db.utcnow() - timedelta(days=1)

    def category(self, name, position=0, approved=1):
        cur = self.conn.execute(
            "INSERT INTO categories(name, position, approved) VALUES(?,?,?)", (name, position, approved)
        )
        return cur.lastrowid

    def channel(self, cid=None, name=None, category_id=None, hidden=0, subscribed=1, source="auto"):
        n = next(_counter)
        cid = cid or f"UC{n:022d}"
        self.conn.execute(
            "INSERT INTO channels(id, name, category_id, category_source, hidden, subscribed) VALUES(?,?,?,?,?,?)",
            (cid, name or f"Channel {n}", category_id, source, hidden, subscribed),
        )
        return cid

    def video(self, channel_id, vid=None, age_min=0, eligible=1, duration=600, discovered_at=None, **kw):
        n = next(_counter)
        vid = vid or f"v{n:010d}"
        published = db.iso_s(self.base - timedelta(minutes=age_min))
        self.conn.execute(
            """INSERT INTO videos(id, channel_id, title, published_at, duration_s, thumbnail_url,
                                  eligible, discovered_at, evicted)
               VALUES(?,?,?,?,?,?,?,?,0)""",
            (vid, channel_id, kw.get("title", f"Title {vid}"), published, duration,
             kw.get("thumbnail_url", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"), eligible,
             discovered_at or db.iso(db.utcnow())),
        )
        return vid

    def progress(self, vid, position, watched=0, updated_at=None, played_at=None, last_played_at=None,
                 max_position=None):
        self.conn.execute(
            """INSERT INTO watch(video_id, position_s, max_position_s, watched, updated_at, played_at, last_played_at)
               VALUES(?,?,?,?,?,?,?)""",
            (vid, position, position if max_position is None else max_position, watched, updated_at or db.iso(db.utcnow()), played_at, last_played_at),
        )

    def save(self, vid, state="ready"):
        self.conn.execute(
            "INSERT INTO saves(video_id, quality, state, updated_at) VALUES(?,?,?,?)",
            (vid, "1080p", state, db.iso(db.utcnow())),
        )


@pytest.fixture
def make(conn):
    return Factory(conn)


def tab(feed, tab_id):
    return next(t for t in feed["tabs"] if t["id"] == tab_id)
