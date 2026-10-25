"""Google OAuth device flow ("TVs and Limited Input devices" client), scope youtube.readonly.

The refresh token lives in a CT-local file (mode 0600), never in SQLite, so the nightly NAS DB
backup cannot contain it, and no endpoint ever returns it. Only the non-secret account name
and error text go in kv.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

import httpx

from . import db

log = logging.getLogger("uvicorn.error")

DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
NOT_SET_UP = "Google sign-in isn't set up yet."
REVOKED = "Google sign-in expired. Sign in again from Settings."
MISCONFIGURED = "Google sign-in is set up wrong on the server."
DENIED = "Google sign-in was cancelled on the phone. Choose Sign in to try again."
CODE_EXPIRED = "The sign-in code expired. Choose Sign in to get a new one."
UNREACHABLE = "Couldn't reach Google to finish signing in. Try again in a minute."
NO_CODE = "Google didn't send a sign-in code. Try again."
MAX_POLL_EXCEPTIONS = 5   # consecutive network/parse failures before the attempt gives up


class AuthError(Exception):
    """Sign-in is missing, revoked or expired; the user must sign in again."""


class NotConfigured(AuthError):
    pass


def _post(url: str, data: dict) -> tuple[int, dict]:
    try:
        r = httpx.post(url, data=data, timeout=20)
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body if isinstance(body, dict) else {}
    except httpx.HTTPError as e:
        raise ConnectionError(type(e).__name__) from None


def configured(settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def _read(path: str) -> dict | None:
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("refresh_token") else None
    except (OSError, ValueError):
        return None


def signed_in(settings) -> bool:
    return _read(settings.google_token_path) is not None


def forget_token(settings) -> None:
    """The refresh token was revoked: delete it so signed_in() turns false and Settings offers
    a fresh sign-in instead of claiming one."""
    try:
        os.remove(settings.google_token_path)
    except FileNotFoundError:
        pass


def _set_error(db_path: str, sentence: str | None) -> None:
    conn = db.connect(db_path)
    try:
        db.set_kv(conn, "auth_error", sentence)
    finally:
        conn.close()


def save_refresh_token(settings, refresh_token: str) -> None:
    path = settings.google_token_path
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"refresh_token": refresh_token}, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def access_token(settings) -> str:
    """Exchange the stored refresh token for a short-lived access token."""
    if not configured(settings):
        raise NotConfigured(NOT_SET_UP)
    tok = _read(settings.google_token_path)
    if not tok:
        raise AuthError("Not signed in to Google.")
    status, body = _post(TOKEN_URL, {
        "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
        "refresh_token": tok["refresh_token"], "grant_type": "refresh_token"})
    if status == 200 and body.get("access_token"):
        return body["access_token"]
    err = body.get("error")
    if err == "invalid_grant":          # the only answer that means the grant is gone
        forget_token(settings)
        raise AuthError(REVOKED)
    if err in ("invalid_client", "unauthorized_client") or status == 401:
        raise AuthError(MISCONFIGURED)  # client id/secret wrong or deleted; the token may be fine
    raise ConnectionError(f"token refresh http {status}")


class DeviceFlow:
    """One sign-in attempt at a time. start() returns what the TV shows; a thread polls Google
    until the code is approved, denied or expired, then stores the refresh token."""

    def __init__(self, settings, db_path: str, on_signed_in=None):
        self.settings, self.db_path, self.on_signed_in = settings, db_path, on_signed_in
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def signing_in(self) -> bool:
        """True from start() until the current attempt has finished (including the first
        subscription sync after approval). The TV waits for this to go false before it reads
        signed_in and error, because an older token file keeps signed_in true meanwhile."""
        with self._lock:
            t, c = self._thread, self._cancel
        return bool(t and t.is_alive() and not c.is_set())

    def start(self) -> dict:
        if not configured(self.settings):
            raise NotConfigured(NOT_SET_UP)
        try:
            status, body = _post(DEVICE_URL, {"client_id": self.settings.google_client_id, "scope": SCOPE})
        except ConnectionError:
            raise ConnectionError("Couldn't reach Google. Try again in a minute.") from None
        if body.get("error") in ("invalid_client", "unauthorized_client") or status == 401:
            raise ConnectionError(MISCONFIGURED)
        if status != 200 or not body.get("device_code"):
            raise ConnectionError("Google refused the sign-in request.")
        if not body.get("user_code"):
            raise ConnectionError(NO_CODE)
        _set_error(self.db_path, None)     # a new attempt starts with a clean slate
        with self._lock:
            self._cancel.set()          # supersede any earlier attempt
            self._cancel = cancel = threading.Event()
            self._thread = threading.Thread(target=self._poll, name="subfeed-device-flow", daemon=True, args=(
                body["device_code"], int(body.get("interval", 5)), int(body.get("expires_in", 1800)), cancel))
            self._thread.start()
        return {"user_code": body["user_code"],
                "verification_url": body.get("verification_url") or body.get("verification_uri", "https://www.google.com/device"),
                "expires_in": int(body.get("expires_in", 1800))}

    def poll_once(self, device_code: str) -> str:
        """One token request. Returns 'done', 'pending', 'slow_down' or 'failed'. A failure also
        writes its sentence to kv.auth_error, which /api/auth/status returns to the TV."""
        status, body = _post(TOKEN_URL, {
            "client_id": self.settings.google_client_id, "client_secret": self.settings.google_client_secret,
            "device_code": device_code, "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        err = body.get("error")
        if status == 200 and body.get("refresh_token"):
            save_refresh_token(self.settings, body["refresh_token"])
            conn = db.connect(self.db_path)
            try:
                db.set_kv(conn, "auth_error", None)
            finally:
                conn.close()
            return "done"
        if err == "authorization_pending":
            return "pending"
        if err == "slow_down":
            return "slow_down"
        if status >= 500 and not err:
            return "pending"            # Google hiccup: keep polling
        if err == "access_denied":
            sentence = DENIED
        elif err in ("expired_token", "invalid_grant"):
            sentence = CODE_EXPIRED
        elif err in ("invalid_client", "unauthorized_client") or status == 401:
            sentence = MISCONFIGURED
        else:
            sentence = f"Google sign-in failed ({err or f'http {status}'}). Choose Sign in to try again."
        log.warning("google sign-in poll failed: %s (http %s)", err, status)
        _set_error(self.db_path, sentence)
        return "failed"

    def _poll(self, device_code: str, interval: int, expires_in: int, cancel: threading.Event) -> None:
        deadline = time.monotonic() + expires_in
        errors = 0
        while not cancel.wait(interval):
            if time.monotonic() >= deadline:
                _set_error(self.db_path, CODE_EXPIRED)
                return
            try:
                res = self.poll_once(device_code)
                errors = 0
            except Exception:  # noqa: BLE001
                errors += 1
                log.warning("google sign-in poll error %d/%d", errors, MAX_POLL_EXCEPTIONS, exc_info=True)
                if errors >= MAX_POLL_EXCEPTIONS:
                    try:
                        _set_error(self.db_path, UNREACHABLE)
                    except Exception:  # noqa: BLE001
                        log.exception("could not record the sign-in error")
                    return
                continue
            if res == "slow_down":
                interval += 5
            elif res == "failed":
                return
            elif res == "done":
                if self.on_signed_in:
                    try:
                        self.on_signed_in()
                    except Exception:  # noqa: BLE001
                        log.exception("first subscription sync failed")
                return
