"""The saved-videos directory: fail-closed mount check, quota arithmetic, ranged file serving."""
from __future__ import annotations

import os
import re
import threading
from typing import Iterator

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,20}$")
GB = 1024**3
RESERVE_BYTES = 2 * GB
CHUNK = 1024 * 1024


def check_mount(path: str, timeout: float = 2.0) -> dict | None:
    """Is `path` a mount point we can stat? Runs in a thread so a hung NFS server costs at
    most `timeout` seconds. Returns {"free", "total"} bytes, or None (fail closed) when it is
    not a mount, the stat fails, or the stat does not finish in time. The empty mountpoint
    directory is a plain directory on the CT's own disk, so ismount is the real test."""
    box: dict = {}

    def probe():
        try:
            if os.path.ismount(path):
                st = os.statvfs(path)
                box["r"] = {"free": st.f_bavail * st.f_frsize, "total": st.f_blocks * st.f_frsize}
        except OSError:
            pass

    t = threading.Thread(target=probe, daemon=True, name="subfeed-mountcheck")
    t.start()
    t.join(timeout)
    return None if t.is_alive() else box.get("r")


def video_path(saved_dir: str, video_id: str) -> str:
    if not VIDEO_ID_RE.match(video_id):
        raise ValueError("bad video id")
    return os.path.join(saved_dir, f"{video_id}.mp4")


def estimate_bytes(duration_s: float | None, quality: str) -> int:
    """Rough size before downloading: ~1 MB/s of video at 1080p, ~2.5 MB/s for best."""
    rate = 2_500_000 if quality == "best" else 1_000_000
    return int((duration_s or 3600) * rate)


def quota_problem(used: int, estimate: int, quota: int, free: int) -> bool:
    """True if the save would not fit: our own total plus the estimate plus 2 GB headroom
    exceeds the quota, or the NAS itself has less free than the estimate plus 2 GB."""
    need = estimate + RESERVE_BYTES
    return used + need > quota or free < need


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """(start, end inclusive) for a single `bytes=` range, None when no header. Raises 416."""
    if not header:
        return None
    m = re.match(r"^bytes=(\d*)-(\d*)$", header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        raise HTTPException(416, "That part of the video isn't available.")
    if m.group(1) == "":
        n = int(m.group(2))
        start, end = max(0, size - n), size - 1
    else:
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
    if start >= size or start > end:
        raise HTTPException(416, "That part of the video isn't available.")
    return start, end


def _iter_file(path: str, start: int, end: int) -> Iterator[bytes]:
    with open(path, "rb") as f:
        f.seek(start)
        left = end - start + 1
        while left > 0:
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                break
            left -= len(chunk)
            yield chunk


def ranged_file_response(path: str, range_header: str | None, media_type: str) -> StreamingResponse:
    try:
        size = os.path.getsize(path)
    except OSError:
        raise HTTPException(404, "That saved video isn't there.") from None
    rng = parse_range(range_header, size)
    base = {"Accept-Ranges": "bytes"}
    if rng is None:
        return StreamingResponse(_iter_file(path, 0, size - 1), 200, {**base, "Content-Length": str(size)},
                                 media_type)
    start, end = rng
    return StreamingResponse(
        _iter_file(path, start, end), 206,
        {**base, "Content-Length": str(end - start + 1), "Content-Range": f"bytes {start}-{end}/{size}"},
        media_type,
    )
