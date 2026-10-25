-- migration 1
CREATE TABLE categories (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  position INTEGER NOT NULL DEFAULT 0,
  approved INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE channels (
  id TEXT PRIMARY KEY,                -- canonical UC... id
  name TEXT NOT NULL,
  handle TEXT,
  description TEXT,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  category_source TEXT NOT NULL DEFAULT 'auto' CHECK (category_source IN ('auto','manual')),
  hidden INTEGER NOT NULL DEFAULT 0,
  subscribed INTEGER NOT NULL DEFAULT 1,
  last_polled_at TEXT,
  last_upload_at TEXT,
  next_poll_at TEXT,                  -- persisted schedule; NULL = due now
  error_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE videos (
  id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  published_at TEXT NOT NULL,         -- ISO-8601 UTC
  duration_s INTEGER,
  thumbnail_url TEXT,
  eligible INTEGER NOT NULL DEFAULT 1,
  unavailable_reason TEXT,
  discovered_at TEXT NOT NULL,
  live_status TEXT,
  availability TEXT,
  ineligible_reason TEXT,
  recheck_at TEXT,                    -- upcoming/premiere: look again after this time
  evicted INTEGER NOT NULL DEFAULT 0  -- tombstone so a polled-again video is not re-added
);
CREATE INDEX videos_channel ON videos(channel_id, published_at);
CREATE INDEX videos_recheck ON videos(recheck_at) WHERE recheck_at IS NOT NULL;

CREATE TABLE watch (
  video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
  position_s REAL NOT NULL DEFAULT 0,
  max_position_s REAL NOT NULL DEFAULT 0,
  duration_s REAL,
  sponsor_s REAL,
  watched INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE saves (
  video_id TEXT PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
  quality TEXT,
  state TEXT NOT NULL CHECK (state IN ('queued','downloading','ready','failed')),
  bytes INTEGER,
  error TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE tab_seen (tab TEXT PRIMARY KEY, seen_at TEXT NOT NULL);
CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT);

-- migration 2
ALTER TABLE videos ADD COLUMN evicted_at TEXT;
UPDATE videos SET evicted_at = discovered_at WHERE evicted = 1;

-- migration 3
ALTER TABLE channels ADD COLUMN poll_limit INTEGER;

-- migration 4
ALTER TABLE watch ADD COLUMN played_at TEXT;
ALTER TABLE watch ADD COLUMN last_played_at TEXT;
UPDATE watch SET played_at = updated_at, last_played_at = updated_at WHERE max_position_s > 30;
