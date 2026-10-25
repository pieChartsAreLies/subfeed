"""yt-dlp access. Every call runs in the bounded process pool (never in the API process).

Public functions are small and mockable in tests:
  channel_uploads(channel_id, limit, meta=None) -> list[dict]   flat Videos tab, long-form only
  video_details(video_id) -> dict                               one video's full metadata
  resolve_handle(handle) -> {id, name, description}
"""
from __future__ import annotations

import urllib.request
from datetime import datetime, timezone

from . import pool

BOT_MARKERS = (
    "sign in to confirm", "not a bot", "confirm you're not", "confirm you’re not",
    "http error 429", "too many requests",
)


class YtdlError(Exception):
    """yt-dlp failed. Single-argument so it survives pickling across the process boundary."""


class BotCheckError(YtdlError):
    """YouTube is asking for sign-in / bot confirmation or rate limiting us: trips the breaker."""


class StorageError(YtdlError):
    """The destination disk failed during a download. Message is "full" or "nas"."""


_NAS_ERRNOS = {5, 13, 30, 116, 122}   # EIO, EACCES, EROFS, ESTALE, EDQUOT (28 = ENOSPC = full)


def _storage_error(exc: BaseException) -> StorageError | None:
    import urllib.error
    seen = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        if isinstance(e, OSError) and not isinstance(e, urllib.error.URLError):
            if e.errno == 28 or e.errno == 122:
                return StorageError("full")
            if e.errno in _NAS_ERRNOS:
                return StorageError("nas")
        info = getattr(e, "exc_info", None)
        stack += [e.__cause__, e.__context__, info[1] if info else None]
    return None


def classify(exc: BaseException) -> YtdlError:
    st = _storage_error(exc)
    if st:
        return st
    msg = str(exc)
    low = msg.lower()
    if any(m in low for m in BOT_MARKERS):
        return BotCheckError(msg[:300])
    return YtdlError(msg[:300])


_BASE_OPTS = {
    "quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True,
    "source_address": "0.0.0.0",  # force IPv4: stream URLs and the TV are on the IPv4 WAN address
    "socket_timeout": 20, "retries": 1, "extractor_retries": 1,
}
_FLAT_KEYS = ("id", "title", "duration", "live_status", "availability", "timestamp",
              "release_timestamp", "url")


# --- worker-side functions (run in pool processes; must be top-level and picklable) ---

def _w_channel(channel_id: str, limit: int) -> tuple[dict, list[dict]]:
    import yt_dlp
    opts = {**_BASE_OPTS, "extract_flat": "in_playlist", "playlistend": limit}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/channel/{channel_id}/videos", download=False)
    except Exception as exc:  # noqa: BLE001
        raise classify(exc) from None
    meta = {"name": info.get("channel") or info.get("uploader"), "description": info.get("description")}
    entries = [{k: e.get(k) for k in _FLAT_KEYS} for e in (info.get("entries") or []) if e.get("id")]
    return meta, entries


def _head_ok(url: str) -> bool:
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def pick_thumbnail(video_id: str, head=_head_ok) -> str:
    """maxres -> sd -> hq. maxres does not exist for every video, so each is checked;
    hqdefault exists for all of them and is the final answer without a check."""
    for name in ("maxresdefault", "sddefault"):
        url = f"https://i.ytimg.com/vi/{video_id}/{name}.jpg"
        if head(url):
            return url
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def _w_details(video_id: str) -> dict:
    import yt_dlp
    opts = {**_BASE_OPTS, "ignore_no_formats_error": True}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            # process=False: metadata only, skips format selection (about 3 s instead of 6+)
            d = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False, process=False)
    except Exception as exc:  # noqa: BLE001
        raise classify(exc) from None
    out = {k: d.get(k) for k in (
        "id", "title", "duration", "live_status", "availability", "age_limit",
        "timestamp", "release_timestamp", "upload_date", "channel_id",
    )}
    out["url"] = f"https://www.youtube.com/watch?v={video_id}"
    out["thumbnail_url"] = pick_thumbnail(video_id)
    return out


_FMT_KEYS = ("format_id", "ext", "vcodec", "acodec", "height", "width", "fps", "tbr", "abr", "protocol",
             "format_note", "language", "url", "http_headers", "filesize", "filesize_approx")


def _chapters(d: dict) -> list[dict]:
    return normalize_chapters(d.get("chapters"))


def normalize_chapters(chapters) -> list[dict]:
    """Contract shape {start, title}, from yt-dlp's {start_time, end_time, title} or an already-normalised list."""
    out = []
    for c in chapters or []:
        start = c.get("start", c.get("start_time"))
        if start is None:
            continue
        out.append({"start": float(start), "title": c.get("title") or ""})
    return out


def _pick_track(tracks: list[dict]) -> dict | None:
    """Prefer WebVTT; skip anything without a URL."""
    usable = [t for t in tracks or [] if t.get("url")]
    for t in usable:
        if t.get("ext") == "vtt":
            return t
    return None


def captions(d: dict) -> list[dict]:
    """Caption tracks for the original language and English: [{lang, url, mime, auto}].
    Uploaded subtitles beat auto-generated ones; only WebVTT is offered (the player reads it natively).
    Auto-captions list ~100 machine translations; only the original-language track ('<lang>-orig' or
    '<lang>') and English are taken."""
    orig = (d.get("language") or "").split("-")[0].lower() or None
    wanted = [l for l in (orig, "en") if l]
    wanted = list(dict.fromkeys(wanted))
    manual = d.get("subtitles") or {}
    auto = d.get("automatic_captions") or {}
    out = []
    for lang in wanted:
        track, is_auto = None, False
        for key in (lang, f"{lang}-orig"):
            track = track or _pick_track(manual.get(key))
        if track is None:
            # any uploaded regional variant (en-US, en-GB) still beats a machine track
            for key, tracks in manual.items():
                if key.lower().startswith(lang + "-"):
                    track = _pick_track(tracks)
                    if track:
                        break
        if track is None:
            # for auto captions the untranslated track is '<lang>-orig' when it exists
            for key in (f"{lang}-orig", lang):
                track = track or _pick_track(auto.get(key))
            is_auto = track is not None
        if track is not None:
            out.append({"lang": lang, "url": track["url"], "mime": "text/vtt", "auto": is_auto})
    return out


def _w_play(video_id: str) -> dict:
    """Full extraction (format selection happens in the API process, on this trimmed list)."""
    import yt_dlp
    opts = {**_BASE_OPTS, "noplaylist": True}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            d = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
    except Exception as exc:  # noqa: BLE001
        raise classify(exc) from None
    return {
        "duration": d.get("duration"), "title": d.get("title"), "description": d.get("description"),
        "chapters": _chapters(d),
        "captions": captions(d),
        "formats": [{k: f.get(k) for k in _FMT_KEYS} for f in (d.get("formats") or [])],
    }


def _w_meta(video_id: str) -> dict:
    import yt_dlp
    opts = {**_BASE_OPTS, "ignore_no_formats_error": True}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            d = y.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False, process=False)
    except Exception as exc:  # noqa: BLE001
        raise classify(exc) from None
    return {"description": d.get("description"), "chapters": _chapters(d)}


def _w_resolve(handle: str) -> dict:
    import yt_dlp
    h = handle if handle.startswith("@") else f"@{handle}"
    opts = {**_BASE_OPTS, "extract_flat": "in_playlist", "playlistend": 1}
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(f"https://www.youtube.com/{h}/videos", download=False)
    except Exception as exc:  # noqa: BLE001
        raise classify(exc) from None
    return {"id": info.get("channel_id") or info.get("id"),
            "name": info.get("channel") or info.get("uploader"),
            "description": info.get("description")}


# --- public API ---

def channel_uploads(channel_id: str, limit: int, meta: dict | None = None) -> list[dict]:
    """Flat listing of the channel's Videos tab (Shorts and Streams live on other tabs, so
    this is long-form uploads). Optional `meta` dict is filled with name and description."""
    m, entries = pool.get_pool().submit(pool.DISCOVERY, _w_channel, channel_id, limit).result()
    if meta is not None:
        meta.update(m)
    return entries


def video_details(video_id: str, priority: int = pool.DISCOVERY) -> dict:
    return pool.get_pool().submit(priority, _w_details, video_id).result()


def play_info(video_id: str, priority: int = pool.PLAY) -> dict:
    """Formats (with stream URLs and headers), duration and chapters for one video."""
    return pool.get_pool().submit(priority, _w_play, video_id).result()


def video_meta(video_id: str, priority: int = pool.DETAIL) -> dict:
    """Description and chapters only (no format selection, about 3 s)."""
    return pool.get_pool().submit(priority, _w_meta, video_id).result()


def resolve_handle(handle: str) -> dict:
    return pool.get_pool().submit(pool.DISCOVERY, _w_resolve, handle).result()


def published_at(entry: dict) -> str:
    """ISO-8601 UTC from the best timestamp yt-dlp gave us (publish time, else premiere time,
    else upload date); empty string if none."""
    ts = entry.get("timestamp") or entry.get("release_timestamp")
    if ts:
        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ud = entry.get("upload_date")
    if ud and len(ud) == 8:
        return f"{ud[0:4]}-{ud[4:6]}-{ud[6:8]}T00:00:00Z"
    return ""
