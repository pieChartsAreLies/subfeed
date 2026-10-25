"""POST /api/videos/{id}/play, GET /media/saved/{id}.mp4, GET /api/videos/{id}/detail.

/play returns either the saved MP4 (when a ready save exists on disk) or direct googlevideo
URLs picked from a yt-dlp extraction. Whether the client is at home is decided from its source
IP, never from a client flag. Remote clients need the proxy (Tier D); until it exists they get
a plain sentence. No transcoding anywhere."""
from __future__ import annotations

import ipaddress
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

from fastapi import Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import breaker, db, sponsorblock, storage, ytdl

PROXY_ENABLED = False
FAIL_SENTENCE = "YouTube isn't letting us play videos right now. Your saved videos still work."
AWAY_SENTENCE = "Watching away from home isn't set up yet."
DETAIL_TTL_S = 24 * 3600

# preference when the client can decode several: AV1 > VP9 > H.264 (after height)
CODEC_RANK = {"av01": 3, "vp9": 2, "avc1": 1}
_ALIASES = {"av1": "av01", "av01": "av01", "vp9": "vp9", "vp09": "vp9", "h264": "avc1", "avc": "avc1", "avc1": "avc1"}


class PlayIn(BaseModel):
    decoders: list[str] = Field(default_factory=lambda: ["avc1"])
    max_height: int = Field(default=2160, ge=144, le=4320)
    from_start: bool = False


def client_host(request: Request) -> str:
    return request.client.host if request.client else ""


def is_local(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False     # fail closed: if we cannot read the client address, treat it as away from home
    return ip.is_private or ip.is_loopback or ip.is_link_local


def _family(vcodec: str | None) -> str | None:
    v = (vcodec or "").lower()
    for prefix, fam in (("av01", "av01"), ("vp09", "vp9"), ("vp9", "vp9"), ("avc1", "avc1")):
        if v.startswith(prefix):
            return fam
    return None


def _has(codec: str | None) -> bool:
    return codec not in (None, "none", "")


def _http(f: dict) -> bool:
    return (f.get("protocol") or "") in ("https", "http") and bool(f.get("url"))


def _mime(f: dict, audio: bool) -> str:
    sub = {"m4a": "mp4", "mp4": "mp4", "webm": "webm"}.get(f.get("ext"), f.get("ext") or "mp4")
    return f"{'audio' if audio else 'video'}/{sub}"


def _audio_rank(f: dict) -> tuple:
    note = (f.get("format_note") or "").lower()
    return (1 if "original" in note else 0, 1 if "default" in note else 0, f.get("abr") or 0)


def select(formats: list[dict], decoders: list[str], max_height: int) -> tuple[dict, dict | None] | None:
    """(video_format, audio_format|None). Best height <= max_height, then AV1 > VP9 > H.264
    among codecs the client lists, then bitrate. Audio is original-language AAC (m4a), never a
    dub. Falls back to one progressive file. None when nothing is playable."""
    want = {_ALIASES[d.lower()] for d in decoders if d.lower() in _ALIASES}
    vids = [f for f in formats if _has(f.get("vcodec")) and not _has(f.get("acodec")) and _http(f)
            and (f.get("height") or 0) <= max_height and _family(f["vcodec"]) in want]
    auds = [f for f in formats if _has(f.get("acodec")) and not _has(f.get("vcodec")) and _http(f)
            and f.get("ext") == "m4a" and (f.get("acodec") or "").startswith("mp4a")]
    auds = [f for f in auds if "dubbed" not in (f.get("format_note") or "").lower()] or auds
    if vids and auds:
        vids.sort(key=lambda f: (f.get("height") or 0, CODEC_RANK[_family(f["vcodec"])], f.get("tbr") or 0),
                  reverse=True)
        auds.sort(key=_audio_rank, reverse=True)
        return vids[0], auds[0]
    prog = [f for f in formats if _has(f.get("vcodec")) and _has(f.get("acodec")) and _http(f)
            and (f.get("height") or 0) <= max_height and _family(f["vcodec"]) in want]
    if prog:
        prog.sort(key=lambda f: (f.get("height") or 0, f.get("tbr") or 0), reverse=True)
        return prog[0], None
    return None


def expires_at(url: str, now: datetime | None = None) -> str:
    """googlevideo URLs carry `expire=<epoch>`; if it is missing assume a conservative 4 h."""
    try:
        exp = int(parse_qs(urlparse(url).query)["expire"][0])
        return datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (KeyError, ValueError, IndexError):
        return ((now or db.utcnow()) + timedelta(hours=4)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resume(conn: sqlite3.Connection, video_id: str) -> float:
    w = conn.execute("SELECT position_s, watched FROM watch WHERE video_id=?", (video_id,)).fetchone()
    if not w or w["watched"] or (w["position_s"] or 0) <= 30:
        return 0.0
    return float(w["position_s"])


def saved_ready_path(conn: sqlite3.Connection, saved_dir: str, video_id: str) -> str | None:
    row = conn.execute("SELECT state FROM saves WHERE video_id=?", (video_id,)).fetchone()
    if not row or row["state"] != "ready":
        return None
    path = storage.video_path(saved_dir, video_id)
    try:
        return path if os.path.getsize(path) > 0 else None
    except OSError:
        return None


def _saved_sidecar(saved_dir: str, video_id: str) -> dict:
    try:
        with open(os.path.join(saved_dir, f"{video_id}.info.json")) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def saved_captions(saved_dir: str, video_id: str, base: str) -> list[dict]:
    """The English sidecar written by the save worker, served under /media/saved with the bearer."""
    if os.path.isfile(os.path.join(saved_dir, f"{video_id}.en.vtt")):
        return [{"lang": "en", "url": f"{base}/media/saved/{video_id}.en.vtt", "mime": "text/vtt", "auto": False}]
    return []


def add_routes(private, media, get_conn, settings):
    def video_row(conn, video_id):
        if not storage.VIDEO_ID_RE.match(video_id):
            raise HTTPException(404, "That video isn't in your feed any more.")
        row = conn.execute("SELECT id, duration_s FROM videos WHERE id=?", (video_id,)).fetchone()
        if not row:
            raise HTTPException(404, "That video isn't in your feed any more.")
        return row

    @private.post("/videos/{video_id}/play")
    def play(video_id: str, body: PlayIn, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        row = video_row(conn, video_id)
        db_duration = float(row["duration_s"]) if row["duration_s"] else None
        resume = 0.0 if body.from_start else _resume(conn, video_id)

        path = saved_ready_path(conn, settings.saved_dir, video_id)
        if path:
            side = _saved_sidecar(settings.saved_dir, video_id)
            duration = side.get("duration") or db_duration
            base = str(request.base_url).rstrip("/")
            return {
                "session_id": uuid.uuid4().hex, "source": "saved",
                "video_url": f"{base}/media/saved/{video_id}.mp4", "audio_url": None,
                "headers": {"Authorization": f"Bearer {settings.token}"},
                "expires_at": (db.utcnow() + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "video_mime": "video/mp4", "audio_mime": None, "height": side.get("height"),
                "duration_s": duration, "resume_s": resume,
                "sponsor": sponsorblock.segments(conn, video_id, duration),
                "chapters": ytdl.normalize_chapters(side.get("chapters")),
                "captions": saved_captions(settings.saved_dir, video_id, base),
            }

        if not is_local(client_host(request)) and not PROXY_ENABLED:
            raise HTTPException(503, AWAY_SENTENCE)
        try:
            info = ytdl.play_info(video_id)
        except ytdl.BotCheckError as exc:
            breaker.trip(conn, str(exc))
            raise HTTPException(503, breaker.LIMITED_SENTENCE) from None
        except Exception:  # noqa: BLE001 - extraction errors, timeouts, dead workers
            raise HTTPException(
                503, breaker.LIMITED_SENTENCE if breaker.is_open(conn) else FAIL_SENTENCE) from None
        picked = select(info.get("formats") or [], body.decoders, body.max_height)
        if not picked:
            raise HTTPException(503, FAIL_SENTENCE)
        v, a = picked
        duration = info.get("duration") or db_duration
        exp = [expires_at(v["url"])] + ([expires_at(a["url"])] if a else [])
        return {
            "session_id": uuid.uuid4().hex, "source": "youtube",
            "video_url": v["url"], "audio_url": a["url"] if a else None,
            "headers": v.get("http_headers") or {}, "expires_at": min(exp),
            "video_mime": _mime(v, False), "audio_mime": _mime(a, True) if a else None,
            "height": v.get("height"), "duration_s": duration, "resume_s": resume,
            "sponsor": sponsorblock.segments(conn, video_id, duration),
            "chapters": ytdl.normalize_chapters(info.get("chapters")),
            "captions": info.get("captions") or [],
        }

    @private.get("/videos/{video_id}/detail")
    def detail(video_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        video_row(conn, video_id)
        key = f"detail:{video_id}"
        cached = db.get_kv(conn, key)
        if cached:
            try:
                c = json.loads(cached)
                if time.time() - c["at"] < DETAIL_TTL_S:
                    return {"description": c["description"], "chapters": ytdl.normalize_chapters(c["chapters"])}
            except Exception:  # noqa: BLE001
                pass
        try:
            meta = ytdl.video_meta(video_id)
        except Exception:  # noqa: BLE001
            raise HTTPException(503, FAIL_SENTENCE) from None
        out = {"description": meta.get("description") or "", "chapters": ytdl.normalize_chapters(meta.get("chapters"))}
        with db.tx(conn):
            db.set_kv(conn, key, json.dumps({"at": time.time(), **out}))
        return out

    @media.get("/saved/{video_id}.en.vtt")
    def saved_captions_file(video_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        if not storage.VIDEO_ID_RE.match(video_id) or not saved_ready_path(conn, settings.saved_dir, video_id):
            raise HTTPException(404, "Those captions aren't there.")
        path = os.path.join(settings.saved_dir, f"{video_id}.en.vtt")
        if not os.path.isfile(path):
            raise HTTPException(404, "Those captions aren't there.")
        return FileResponse(path, media_type="text/vtt")

    @media.get("/saved/{video_id}.mp4")
    def saved_file(video_id: str, request: Request, conn: sqlite3.Connection = Depends(get_conn)):
        if not storage.VIDEO_ID_RE.match(video_id):
            raise HTTPException(404, "That saved video isn't there.")
        path = saved_ready_path(conn, settings.saved_dir, video_id)
        if not path:
            raise HTTPException(404, "That saved video isn't there.")
        return storage.ranged_file_response(path, request.headers.get("range"), "video/mp4")
