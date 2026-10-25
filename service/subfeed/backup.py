"""Nightly SQLite backup: `.backup` of the live DB to <saved>/_db/subfeed-YYYYMMDD.db, keep 14.

Run as `python -m subfeed.backup` (systemd timer). Refuses (exit 1) if the NAS is not mounted,
so nothing is ever written to the CT's own disk. The OAuth token is not in this database."""
from __future__ import annotations

import glob
import os
import sqlite3
import sys
from datetime import datetime

from . import storage
from .config import Settings

KEEP = 14


def backup(db_path: str, saved_dir: str, now: datetime | None = None, keep: int = KEEP) -> str:
    if not storage.check_mount(saved_dir):
        raise RuntimeError("the NAS is not mounted")
    dest_dir = os.path.join(saved_dir, "_db")
    os.makedirs(dest_dir, exist_ok=True)
    name = f"subfeed-{(now or datetime.now()).strftime('%Y%m%d')}.db"
    final = os.path.join(dest_dir, name)
    tmp = final + ".partial"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    os.replace(tmp, final)
    for old in sorted(glob.glob(os.path.join(dest_dir, "subfeed-????????.db")))[:-keep]:
        os.remove(old)
    return final


def main() -> int:
    s = Settings.from_env()
    try:
        print(backup(s.db_path, s.saved_dir))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"backup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
