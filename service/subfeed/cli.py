"""subfeed command line: seed, poll, resolve, serve."""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import categorize, db, discovery, ytdl
from .config import Settings

_SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
# seed/channels.txt is your own list (git-ignored); seed/channels.example.txt ships with the repo.
DEFAULT_SEED = _SEED_DIR / "channels.txt"
if not DEFAULT_SEED.exists():
    DEFAULT_SEED = _SEED_DIR / "channels.example.txt"


def parse_seed(text: str) -> list[dict]:
    """Lines: `<UC id> <@handle or -> <name...>  [# comment]`. Blank lines and #-lines skipped."""
    out = []
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        parts = line.split(None, 2)
        if len(parts) < 3 or not parts[0].startswith("UC"):
            raise ValueError(f"bad seed line: {raw!r}")
        handle = None if parts[1] == "-" else parts[1]
        out.append({"id": parts[0], "handle": handle, "name": parts[2].strip()})
    return out


def seed_channels(conn, channels: list[dict]) -> int:
    """Insert channels as subscribed. Existing rows keep their category, hidden and schedule."""
    added = 0
    with db.tx(conn):
        for ch in channels:
            cur = conn.execute(
                "INSERT OR IGNORE INTO channels(id, name, handle, subscribed) VALUES(?,?,?,1)",
                (ch["id"], ch["name"], ch["handle"]),
            )
            added += cur.rowcount
            if not cur.rowcount and ch["handle"]:
                conn.execute("UPDATE channels SET handle=? WHERE id=?", (ch["handle"], ch["id"]))
    return added


def cmd_seed(args) -> int:
    conn = db.connect(Settings.from_env().db_path)
    channels = parse_seed(Path(args.file).read_text())
    added = seed_channels(conn, channels)
    print(f"seed: {len(channels)} channels in file, {added} added")
    return 0


def cmd_poll(args) -> int:
    settings = Settings.from_env()
    conn0 = db.connect(settings.db_path)
    if args.channel:
        ids = [args.channel]
    else:
        ids = [r["id"] for r in conn0.execute("SELECT id FROM channels WHERE subscribed=1 ORDER BY name COLLATE NOCASE")]

    def one(cid: str) -> dict:
        conn = db.connect(settings.db_path)
        new = discovery.poll_channel(conn, cid, args.limit)
        row = conn.execute("SELECT name, error_count, last_error FROM channels WHERE id=?", (cid,)).fetchone()
        total = conn.execute(
            "SELECT COUNT(*) FROM videos WHERE channel_id=? AND eligible=1 AND evicted=0", (cid,)
        ).fetchone()[0]
        conn.close()
        return {"id": cid, "name": row["name"], "new": new, "eligible_total": total,
                "error": row["last_error"]}

    with ThreadPoolExecutor(max_workers=2) as ex:
        results = list(ex.map(one, ids))
    for r in results:
        print(json.dumps(r))
    print(f"poll: {sum(r['new'] for r in results)} new eligible videos across {len(results)} channels")
    return 0


def cmd_resolve(args) -> int:
    print(json.dumps(ytdl.resolve_handle(args.handle)))
    return 0


def cmd_categorize(args) -> int:
    conn = db.connect(Settings.from_env().db_path)
    if args.first_run:
        counts = categorize.first_run(conn)
        print(json.dumps(counts))
    else:
        print(f"categorized {categorize.categorize_pending(conn)} channels")
    return 0


def cmd_serve(args) -> int:
    import uvicorn
    from .api import create_app
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="subfeed")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="import the seed channels as subscribed")
    s.add_argument("--file", default=str(DEFAULT_SEED))
    s.set_defaults(fn=cmd_seed)
    s = sub.add_parser("poll", help="poll channels now, ignoring the schedule")
    s.add_argument("--channel")
    s.add_argument("--limit", type=int, default=discovery.BACKFILL_LIMIT)
    s.set_defaults(fn=cmd_poll)
    s = sub.add_parser("resolve", help="resolve an @handle to a channel id")
    s.add_argument("handle")
    s.set_defaults(fn=cmd_resolve)
    s = sub.add_parser("categorize", help="auto-categorize channels (OPENROUTER_API_KEY)")
    s.add_argument("--first-run", action="store_true", help="propose and approve the tab set, assign all")
    s.set_defaults(fn=cmd_categorize)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
