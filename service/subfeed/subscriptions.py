"""Daily YouTube subscription sync (Data API v3, subscriptions.list mine=true).

Safety: a sync that errors partway or returns zero subscriptions changes nothing. Channels
that disappear get subscribed=0 (hidden from the feed, saved items kept), never deleted.
Channels are deduped by UC id, so seed channels that are also real subscriptions keep their
category, manual choices and watch state. New channels have next_poll_at NULL, so the scheduler
backfills them (poll_limit 5, SYNC_BACKFILL) and categorize_pending then picks them up.
"""
from __future__ import annotations

import json
import logging
import threading

import httpx

from . import db, google_auth

log = logging.getLogger("uvicorn.error")

API = "https://www.googleapis.com/youtube/v3"
MAX_PAGES = 100  # 5000 subscriptions; a runaway pager must not loop forever
SYNC_BACKFILL = 5  # channels added by a sync list only their newest 5 uploads (not 15): a first
                   # sync can add hundreds of channels, and a 15-deep backfill of each is the kind of
                   # burst that gets the home IP bot-checked
_sync_lock = threading.Lock()   # the scheduler and the sign-in callback must not sync at once

QUOTA = "YouTube's daily limit for Subfeed is used up. Subscriptions will sync again tomorrow."
API_OFF = "The YouTube Data API isn't turned on in the Google Cloud project (see docs/google-setup.md)."
NO_SCOPE = "Google didn't give Subfeed permission to read your subscriptions. Sign in again and allow it."
UNREACHABLE = "Couldn't reach YouTube to sync your subscriptions. Subfeed will try again in an hour."
EMPTY = "YouTube returned no subscriptions, so Subfeed kept your current channel list."


class SyncError(ConnectionError):
    """A non-auth sync failure; str() is the plain sentence stored in kv.sync_error."""


def _forbidden(body: dict) -> SyncError:
    err = body.get("error") if isinstance(body.get("error"), dict) else {}
    reasons = {e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)}
    reasons |= {d.get("reason") for d in err.get("details") or [] if isinstance(d, dict)}
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "RATE_LIMIT_EXCEEDED"}:
        return SyncError(QUOTA)
    if reasons & {"accessNotConfigured", "SERVICE_DISABLED"}:
        return SyncError(API_OFF)
    if reasons & {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}:
        return SyncError(NO_SCOPE)
    return SyncError("YouTube refused the subscription list (http 403). Subfeed will try again in an hour.")


def _get(path: str, params: dict, token: str) -> tuple[int, dict]:
    try:
        r = httpx.get(f"{API}/{path}", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=30)
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body if isinstance(body, dict) else {}
    except httpx.HTTPError as e:
        raise ConnectionError(type(e).__name__) from None


def fetch_subscriptions(token: str) -> list[dict]:
    """All subscriptions as [{id, name, description}]. Raises on any failed page."""
    out: dict[str, dict] = {}
    page = None
    for _ in range(MAX_PAGES):
        params = {"mine": "true", "part": "snippet", "maxResults": 50}
        if page:
            params["pageToken"] = page
        status, body = _get("subscriptions", params, token)
        if status == 401:
            raise google_auth.AuthError(google_auth.REVOKED)
        if status == 403:
            raise _forbidden(body)
        if status != 200:
            raise SyncError(f"YouTube's subscription list failed (http {status}). Subfeed will try again in an hour.")
        for it in body.get("items", []):
            sn = it.get("snippet", {})
            cid = (sn.get("resourceId") or {}).get("channelId")
            if cid and cid.startswith("UC"):
                out[cid] = {"id": cid, "name": sn.get("title") or cid, "description": sn.get("description") or ""}
        page = body.get("nextPageToken")
        if not page:
            return list(out.values())
    raise SyncError("YouTube's subscription list never finished. Subfeed will try again in an hour.")


def account_name(token: str) -> str | None:
    try:
        status, body = _get("channels", {"mine": "true", "part": "snippet"}, token)
    except ConnectionError:
        return None
    items = body.get("items") or []
    return items[0].get("snippet", {}).get("title") if status == 200 and items else None


def apply(conn, subs: list[dict]) -> dict:
    """Reconcile the channels table with the fetched list in one short transaction."""
    ids = {s["id"] for s in subs}
    with db.tx(conn):
        first = db.get_kv(conn, "last_sub_sync") is None   # read under the write lock
        existing = {r["id"]: r["subscribed"] for r in conn.execute("SELECT id, subscribed FROM channels")}
        added = kept = 0
        for s in subs:
            if s["id"] not in existing:
                conn.execute("INSERT INTO channels(id, name, description, subscribed, poll_limit) VALUES(?,?,?,1,?)",
                             (s["id"], s["name"], s["description"], SYNC_BACKFILL))
                added += 1
            elif not existing[s["id"]]:   # re-subscribed: show again and re-poll
                conn.execute("UPDATE channels SET subscribed=1, next_poll_at=NULL WHERE id=?", (s["id"],))
                added += 1
            else:
                kept += 1
        gone = [c for c, sub in existing.items() if sub and c not in ids]
        for c in gone:
            conn.execute("UPDATE channels SET subscribed=0 WHERE id=?", (c,))
        result = {"added": added, "removed": len(gone), "kept": kept}
        db.set_kv(conn, "last_sub_sync", db.iso(db.utcnow()))
        db.set_kv(conn, "auth_error", None)
        db.set_kv(conn, "sync_error", None)
        if first:
            db.set_kv(conn, "import_summary", json.dumps(result))
    return result


def sync(conn, settings) -> dict:
    """One sync, never two at once. Any failure (auth, network, quota, empty list) leaves the
    channel list untouched and records one plain sentence: sign-in problems in kv.auth_error,
    everything else in kv.sync_error. Both are returned by /api/auth/status."""
    with _sync_lock:
        return _sync(conn, settings)


def _sync(conn, settings) -> dict:
    try:
        token = google_auth.access_token(settings)
        subs = fetch_subscriptions(token)
    except google_auth.NotConfigured:
        raise
    except google_auth.AuthError as e:
        db.set_kv(conn, "auth_error", str(e))
        raise
    except SyncError as e:
        db.set_kv(conn, "sync_error", str(e))
        raise
    except ConnectionError:
        db.set_kv(conn, "sync_error", UNREACHABLE)
        raise
    except Exception:
        db.set_kv(conn, "sync_error", "Subscription sync failed on the server. Subfeed will try again in an hour.")
        raise
    if not subs:
        log.warning("subscription sync returned zero subscriptions; changing nothing")
        db.set_kv(conn, "sync_error", EMPTY)
        raise SyncError(EMPTY)
    result = apply(conn, subs)
    name = account_name(token)
    if name:
        db.set_kv(conn, "google_account", name)
    return result
