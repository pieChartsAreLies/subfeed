"""Saving videos to the NAS: POST/DELETE /api/videos/{id}/save and the one-at-a-time worker.

Layout in /srv/saved (all named by video ID): {id}.mp4 (faststart, H.264 + AAC preferred,
remux only), {id}.info.json, {id}.jpg, {id}.en.vtt (when it exists). Downloads write
{id}.partial.* and the final MP4 is renamed into place last, after the sidecars, so a file
called {id}.mp4 is always complete. Everything fails closed when the NAS is not mounted.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import sqlite3
import subprocess
import threading
import urllib.request

from fastapi import Depends, HTTPException, Response
from pydantic import BaseModel

from . import breaker, db, pool, storage, ytdl

log = logging.getLogger("uvicorn.error")

NAS_SENTENCE = "Can't reach the NAS, so saving is paused."
FULL_SENTENCE = "Your saved-videos space is full."
BLOCKED_SENTENCE = "YouTube blocked this download. Try again later."
FAILED_SENTENCE = "Couldn't save this video."
POLL_S = 2.0
MAX_BOT_ATTEMPTS = 3

FORMATS = {
    # H.264 first; the fallbacks exclude AV1 (the A80K has no AV1 decoder)
    "1080p": "bv*[vcodec^=avc1][height<=1080]+ba[ext=m4a]"
             "/bv*[height<=1080][ext=mp4][vcodec!^=av01]+ba[ext=m4a]"
             "/b[ext=mp4][height<=1080][vcodec!^=av01]",
    "best": "bv*+ba[ext=m4a]/b[ext=mp4]/b",
}


class SaveIn(BaseModel):
    quality: str = "1080p"


# --- pure helpers -------------------------------------------------------------------------

def is_faststart(path: str) -> bool:
    """True if the MP4's moov atom comes before mdat (playable while still downloading/seeking)."""
    with open(path, "rb") as f:
        pos = 0
        size_total = os.fstat(f.fileno()).st_size
        while pos + 8 <= size_total:
            f.seek(pos)
            hdr = f.read(16)
            size, kind = int.from_bytes(hdr[:4], "big"), hdr[4:8]
            if size == 1:
                size = int.from_bytes(hdr[8:16], "big")
            if kind == b"moov":
                return True
            if kind == b"mdat":
                return False
            if size < 8:
                return False
            pos += size
    return False


def reap_partials(saved_dir: str) -> int:
    """Delete leftover {id}.partial* files (a crash or restart mid-download). Only called when
    nothing is downloading. Returns how many were removed."""
    n = 0
    for p in glob.glob(os.path.join(saved_dir, "*.partial*")):
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


def delete_files(saved_dir: str, video_id: str) -> None:
    if not storage.VIDEO_ID_RE.match(video_id):
        return
    for p in glob.glob(os.path.join(glob.escape(saved_dir), f"{glob.escape(video_id)}.*")):
        try:
            os.remove(p)
        except OSError:
            pass


def used_bytes(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(SUM(bytes),0) FROM saves WHERE state='ready'").fetchone()[0]


def _set_state(conn, video_id, state, error=None, nbytes=None):
    with db.tx(conn):
        conn.execute(
            "UPDATE saves SET state=?, error=?, bytes=COALESCE(?, bytes), updated_at=? WHERE video_id=?",
            (state, error, nbytes, db.iso(db.utcnow()), video_id),
        )


# --- download (runs in a pool process) ----------------------------------------------------

def _w_save(video_id: str, quality: str, saved_dir: str) -> dict:
    """Download + merge into {id}.partial.mp4 (faststart). Returns metadata for the sidecars."""
    import yt_dlp
    out = os.path.join(saved_dir, f"{video_id}.partial.%(ext)s")
    opts = {
        **ytdl._BASE_OPTS, "skip_download": False, "noplaylist": True,
        "format": FORMATS.get(quality, FORMATS["1080p"]), "merge_output_format": "mp4",
        "outtmpl": out, "postprocessor_args": {"merger": ["-movflags", "+faststart"]},
        "concurrent_fragment_downloads": 2, "overwrites": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    except Exception as exc:  # noqa: BLE001
        raise ytdl.classify(exc) from None
    mp4 = os.path.join(saved_dir, f"{video_id}.partial.mp4")
    if not os.path.exists(mp4):
        raise ytdl.YtdlError("download produced no MP4")
    if not is_faststart(mp4):
        fixed = os.path.join(saved_dir, f"{video_id}.partial.fast.mp4")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", mp4, "-c", "copy", "-movflags", "+faststart", fixed],
                       check=True, timeout=1800)
        os.replace(fixed, mp4)
    try:   # best effort: English subtitles, uploaded beats auto-generated (yt-dlp's own precedence)
        sopts = {**ytdl._BASE_OPTS, "writesubtitles": True, "writeautomaticsub": True,
                 "subtitleslangs": ["en", "en-orig"],
                 "subtitlesformat": "vtt", "outtmpl": os.path.join(saved_dir, f"{video_id}.partial.%(ext)s")}
        with yt_dlp.YoutubeDL(sopts) as y:
            y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
    except Exception:  # noqa: BLE001
        pass
    return {
        "title": info.get("title"), "channel": info.get("channel"), "duration": info.get("duration"),
        "height": info.get("height"), "vcodec": info.get("vcodec"), "acodec": info.get("acodec"),
        "chapters": [{"start": c.get("start_time"), "title": c.get("title")} for c in (info.get("chapters") or [])],
    }


def _fetch_thumb(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.read()
    except Exception:  # noqa: BLE001
        return None


def finalize(saved_dir: str, video_id: str, meta: dict, thumb_url: str | None, fetch_thumb=_fetch_thumb) -> int:
    """Write sidecars, then rename the MP4 into place last. Returns total bytes of the save."""
    part = os.path.join(saved_dir, f"{video_id}.partial.mp4")
    with open(os.path.join(saved_dir, f"{video_id}.info.json.partial"), "w") as fh:
        json.dump(meta, fh)
    os.replace(os.path.join(saved_dir, f"{video_id}.info.json.partial"), os.path.join(saved_dir, f"{video_id}.info.json"))
    jpg = fetch_thumb(thumb_url) if thumb_url else None
    if jpg:
        with open(os.path.join(saved_dir, f"{video_id}.jpg.partial"), "wb") as fh:
            fh.write(jpg)
        os.replace(os.path.join(saved_dir, f"{video_id}.jpg.partial"), os.path.join(saved_dir, f"{video_id}.jpg"))
    vtt = os.path.join(saved_dir, f"{video_id}.partial.en.vtt")
    orig = os.path.join(saved_dir, f"{video_id}.partial.en-orig.vtt")
    if not os.path.exists(vtt) and os.path.exists(orig):
        vtt = orig      # English auto-captions sometimes only come as the untranslated 'en-orig'
    if os.path.exists(vtt):
        os.replace(vtt, os.path.join(saved_dir, f"{video_id}.en.vtt"))
    if os.path.exists(orig):
        os.remove(orig)
    final = os.path.join(saved_dir, f"{video_id}.mp4")
    os.replace(part, final)
    return sum(os.path.getsize(p) for p in glob.glob(os.path.join(glob.escape(saved_dir), f"{glob.escape(video_id)}.*")))


def default_runner(video_id: str, quality: str, saved_dir: str) -> dict:
    return pool.get_pool().submit(pool.SAVE, _w_save, video_id, quality, saved_dir).result()


# --- worker -------------------------------------------------------------------------------

class SaveWorker:
    def __init__(self, settings, runner=None, fetch_thumb=None):
        self.settings = settings
        self.runner = runner or default_runner
        self.fetch_thumb = fetch_thumb or _fetch_thumb
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="subfeed-saver", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def startup(self, conn):
        """Anything left 'downloading' was killed mid-way: requeue it and delete its partials."""
        with db.tx(conn):
            conn.execute("UPDATE saves SET state='queued' WHERE state='downloading'")
        if storage.check_mount(self.settings.saved_dir):
            reap_partials(self.settings.saved_dir)

    def run_once(self, conn) -> bool:
        """Process at most one queued save. Returns True if one was handled."""
        if breaker.is_open(conn):
            return False
        row = conn.execute(
            "SELECT s.video_id, s.quality, v.duration_s, v.thumbnail_url FROM saves s JOIN videos v ON v.id=s.video_id "
            "WHERE s.state='queued' ORDER BY s.updated_at LIMIT 1").fetchone()
        if not row:
            return False
        vid, quality = row["video_id"], row["quality"] or "1080p"
        mount = storage.check_mount(self.settings.saved_dir)
        if not mount:
            _set_state(conn, vid, "failed", NAS_SENTENCE)
            return True
        if storage.quota_problem(used_bytes(conn), storage.estimate_bytes(row["duration_s"], quality),
                                 self.settings.saved_quota_bytes, mount["free"]):
            _set_state(conn, vid, "failed", FULL_SENTENCE)
            return True
        _set_state(conn, vid, "downloading")
        meta = None
        try:
            meta = self.runner(vid, quality, self.settings.saved_dir)
        except ytdl.BotCheckError as exc:
            # YouTube is limiting us: open the breaker and put the save back in the queue. The
            # is_open guard above holds it until the pause ends; no extra traffic meanwhile.
            breaker.trip(conn, str(exc))
            reap_one(self.settings.saved_dir, vid)
            tries = int(db.get_kv(conn, f"save_attempts:{vid}", "0") or 0) + 1
            with db.tx(conn):
                db.set_kv(conn, f"save_attempts:{vid}", str(tries) if tries < MAX_BOT_ATTEMPTS else None)
            if conn.execute("SELECT 1 FROM saves WHERE video_id=?", (vid,)).fetchone():
                if tries >= MAX_BOT_ATTEMPTS:
                    _set_state(conn, vid, "failed", BLOCKED_SENTENCE)
                else:
                    _set_state(conn, vid, "queued")
            return True
        except ytdl.StorageError as exc:
            err = FULL_SENTENCE if str(exc) == "full" else NAS_SENTENCE
        except OSError as exc:
            err = FULL_SENTENCE if getattr(exc, "errno", None) in (28, 122) else NAS_SENTENCE
        except Exception:  # noqa: BLE001
            err = FAILED_SENTENCE
        still_wanted = conn.execute("SELECT 1 FROM saves WHERE video_id=?", (vid,)).fetchone()
        if meta is None:
            reap_one(self.settings.saved_dir, vid)
            if still_wanted:
                _set_state(conn, vid, "failed", err)
            return True
        if not still_wanted:      # unsaved while it was downloading
            delete_files(self.settings.saved_dir, vid)
            return True
        if not storage.check_mount(self.settings.saved_dir):     # NAS vanished mid-download
            delete_files(self.settings.saved_dir, vid)
            _set_state(conn, vid, "failed", NAS_SENTENCE)
            return True
        try:
            nbytes = finalize(self.settings.saved_dir, vid, meta, row["thumbnail_url"], self.fetch_thumb)
        except OSError as exc:
            delete_files(self.settings.saved_dir, vid)          # no orphaned sidecars
            _set_state(conn, vid, "failed", FULL_SENTENCE if getattr(exc, "errno", None) in (28, 122) else NAS_SENTENCE)
            return True
        _set_state(conn, vid, "ready", None, nbytes)
        return True

    def _loop(self):
        while not self._stop.is_set():
            conn = None
            try:
                conn = db.connect(self.settings.db_path)
                if not self.run_once(conn):
                    self._stop.wait(POLL_S)
            except Exception:  # noqa: BLE001
                log.exception("saver: loop error")
                self._stop.wait(POLL_S * 5)
            finally:
                if conn:
                    conn.close()


def reap_one(saved_dir: str, video_id: str) -> None:
    for p in glob.glob(os.path.join(glob.escape(saved_dir), f"{glob.escape(video_id)}.*partial*")):
        try:
            os.remove(p)
        except OSError:
            pass


# --- routes -------------------------------------------------------------------------------

def add_routes(private, get_conn, settings):
    @private.post("/videos/{video_id}/save", status_code=202)
    def save(video_id: str, body: SaveIn, conn: sqlite3.Connection = Depends(get_conn)):
        if body.quality not in FORMATS:
            raise HTTPException(422, "Pick 1080p or best.")
        row = conn.execute("SELECT id, duration_s FROM videos WHERE id=?", (video_id,)).fetchone() \
            if storage.VIDEO_ID_RE.match(video_id) else None
        if not row:
            raise HTTPException(404, "That video isn't in your feed any more.")
        existing = conn.execute("SELECT state FROM saves WHERE video_id=?", (video_id,)).fetchone()
        if existing and existing["state"] in ("queued", "downloading", "ready"):
            return {"state": existing["state"]}
        mount = storage.check_mount(settings.saved_dir)
        if not mount:
            raise HTTPException(503, NAS_SENTENCE)
        if storage.quota_problem(used_bytes(conn), storage.estimate_bytes(row["duration_s"], body.quality),
                                 settings.saved_quota_bytes, mount["free"]):
            raise HTTPException(507, FULL_SENTENCE)
        with db.tx(conn):
            conn.execute(
                "INSERT INTO saves(video_id, quality, state, error, bytes, updated_at) VALUES(?,?,'queued',NULL,NULL,?) "
                "ON CONFLICT(video_id) DO UPDATE SET quality=excluded.quality, state='queued', error=NULL, "
                "bytes=NULL, updated_at=excluded.updated_at",
                (video_id, body.quality, db.iso(db.utcnow())))
        return {"state": "queued"}

    @private.delete("/videos/{video_id}/save", status_code=204)
    def unsave(video_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        if not storage.VIDEO_ID_RE.match(video_id):
            raise HTTPException(404, "That video isn't in your feed any more.")
        row = conn.execute("SELECT state FROM saves WHERE video_id=?", (video_id,)).fetchone()
        if not row:
            return Response(status_code=204)
        if not storage.check_mount(settings.saved_dir):
            raise HTTPException(503, NAS_SENTENCE)    # deleting now would orphan the files
        with db.tx(conn):
            conn.execute("DELETE FROM saves WHERE video_id=?", (video_id,))
        if row["state"] != "downloading":
            delete_files(settings.saved_dir, video_id)  # a running download cleans up after itself
        return Response(status_code=204)
