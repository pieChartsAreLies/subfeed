"""FastAPI app: feed, channels, categories, progress, health. Everything under /api except
/api/health needs the bearer token. Every error body is {"sentence": "..."}."""
from __future__ import annotations

import hmac
import json
import sqlite3
from contextlib import asynccontextmanager
from importlib import metadata

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import breaker, db, feed, google_auth, health as health_mod, playback, saving, subscriptions, watch
from .config import Settings
from .models import CategoryIn, ChannelPatch, ProgressIn, WatchedIn


def _err(status: int, sentence: str) -> HTTPException:
    return HTTPException(status_code=status, detail=sentence)


def _sync_now(settings) -> None:
    conn = db.connect(settings.db_path)
    try:
        subscriptions.sync(conn, settings)
    finally:
        conn.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # touch the DB so migrations run at startup, not on the first request
        db.connect(settings.db_path).close()
        sched, saver, canary = None, None, None
        if settings.scheduler:
            from . import scheduler
            sched = scheduler.start(app)
        if settings.canary and settings.scheduler:
            canary = health_mod.CanaryThread(settings.db_path).start()
        if settings.save_worker:
            saver = saving.SaveWorker(settings)
            c = db.connect(settings.db_path)
            try:
                saver.startup(c)
            finally:
                c.close()
            saver.start()
        yield
        for svc in (sched, saver, canary):
            if svc:
                svc.stop()

    app = FastAPI(title="Subfeed", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    def get_conn():
        conn = db.connect(settings.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def require_token(request: Request):
        header = request.headers.get("authorization", "")
        scheme, _, supplied = header.partition(" ")
        expected = settings.token
        if not expected or scheme.lower() != "bearer" or not hmac.compare_digest(
            supplied.strip().encode(), expected.encode()
        ):
            raise HTTPException(status_code=401, detail="This device isn't signed in to Subfeed.")

    @app.exception_handler(StarletteHTTPException)
    async def _http_err(_: Request, exc: StarletteHTTPException):
        generic = {404: "That doesn't exist.", 405: "That isn't something this server does."}
        detail = generic.get(exc.status_code) if exc.detail in ("Not Found", "Method Not Allowed") else exc.detail
        detail = detail if isinstance(detail, str) else "That request wasn't valid."
        return JSONResponse({"sentence": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_err(_: Request, exc: RequestValidationError):
        return JSONResponse({"sentence": "That request wasn't valid."}, status_code=422)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        return JSONResponse({"sentence": "Something went wrong on the server."}, status_code=500)

    public = APIRouter(prefix="/api")
    private = APIRouter(prefix="/api", dependencies=[Depends(require_token)])

    @public.get("/health")
    def health(conn: sqlite3.Connection = Depends(get_conn)):
        lim = breaker.status(conn)
        try:
            ytdlp_version = metadata.version("yt-dlp")
        except metadata.PackageNotFoundError:
            ytdlp_version = None
        return {
            "ok": True,
            "last_sub_sync": db.get_kv(conn, "last_sub_sync"),
            "auth_error": db.get_kv(conn, "auth_error"),
            "sync_error": db.get_kv(conn, "sync_error"),
            "last_poll": db.get_kv(conn, "last_poll"),
            "ytdlp_version": ytdlp_version,
            **health_mod.health_fields(conn, settings),
            "youtube_limited": lim["limited"],
            "message": lim["message"],
        }

    @private.get("/feed")
    def get_feed(conn: sqlite3.Connection = Depends(get_conn)):
        return JSONResponse(feed.build_feed(conn))  # bypass jsonable_encoder: ~3 MB at full size

    @private.post("/tabs/{tab_id}/seen", status_code=204)
    def tab_seen(tab_id: str, conn: sqlite3.Connection = Depends(get_conn)):
        if not tab_id or len(tab_id) > 64:
            raise _err(422, "That request wasn't valid.")
        with db.tx(conn):
            conn.execute(
                "INSERT INTO tab_seen(tab, seen_at) VALUES(?,?) "
                "ON CONFLICT(tab) DO UPDATE SET seen_at=excluded.seen_at",
                (tab_id, db.iso(db.utcnow())),
            )
        return Response(status_code=204)

    def _channel_row(r) -> dict:
        return {
            "id": r["id"], "name": r["name"], "category_id": r["category_id"],
            "category_source": r["category_source"], "hidden": bool(r["hidden"]),
            "video_count": r["video_count"], "unwatched_count": r["unwatched_count"],
            "last_upload_at": r["last_upload_at"], "last_watched_at": r["last_watched_at"],
        }

    # one aggregate over the same admission as the feed (feed.ADMIT); unwatched = admitted and not watched
    _CHANNEL_SQL = (
        "SELECT c.id, c.name, c.category_id, c.category_source, c.hidden, "
        "COUNT(v.id) AS video_count, "
        "COALESCE(SUM(CASE WHEN v.id IS NOT NULL AND COALESCE(w.watched, 0) = 0 THEN 1 ELSE 0 END), 0) AS unwatched_count, "
        "MAX(v.published_at) AS last_upload_at, MAX(w.last_played_at) AS last_watched_at "
        "FROM channels c "
        "LEFT JOIN (SELECT v.* FROM videos v LEFT JOIN watch w ON w.video_id = v.id "
        "           LEFT JOIN saves s ON s.video_id = v.id WHERE " + feed.ADMIT + ") v ON v.channel_id = c.id "
        "LEFT JOIN watch w ON w.video_id = v.id "
    )

    @private.get("/channels")
    def list_channels(conn: sqlite3.Connection = Depends(get_conn)):
        rows = conn.execute(
            _CHANNEL_SQL + "WHERE c.subscribed=1 GROUP BY c.id ORDER BY c.name COLLATE NOCASE"
        ).fetchall()
        return [_channel_row(r) for r in rows]

    @private.get("/channels/{channel_id}/videos")
    def channel_videos(channel_id: str, offset: int = 0, limit: int = 100,
                       conn: sqlite3.Connection = Depends(get_conn)):
        row = conn.execute(_CHANNEL_SQL + "WHERE c.id=? GROUP BY c.id", (channel_id,)).fetchone()
        if not row:
            # unsubscribed channels still open: History/Continue can hold their videos
            raise _err(404, "That channel isn't in your subscriptions.")
        total, videos = feed.channel_videos(conn, channel_id, max(offset, 0), min(max(limit, 1), 200))
        return {"channel": _channel_row(row), "total": total, "videos": videos}

    @private.put("/channels/{channel_id}")
    def put_channel(channel_id: str, body: ChannelPatch, conn: sqlite3.Connection = Depends(get_conn)):
        with db.tx(conn):
            ch = conn.execute("SELECT * FROM channels WHERE id=?", (channel_id,)).fetchone()
            if not ch:
                raise _err(404, "That channel isn't in your subscriptions.")
            sent = body.model_fields_set
            if "category_id" in sent:
                if body.category_id is not None and not conn.execute(
                    "SELECT 1 FROM categories WHERE id=?", (body.category_id,)
                ).fetchone():
                    raise _err(404, "That category doesn't exist.")
                # a manual choice (including "no category") is never overwritten by auto-categorizing
                conn.execute(
                    "UPDATE channels SET category_id=?, category_source='manual' WHERE id=?",
                    (body.category_id, channel_id),
                )
            if "hidden" in sent and body.hidden is not None:
                conn.execute("UPDATE channels SET hidden=? WHERE id=?", (int(body.hidden), channel_id))
        row = conn.execute(_CHANNEL_SQL + "WHERE c.id=? GROUP BY c.id", (channel_id,)).fetchone()
        return _channel_row(row)

    @private.post("/categories")
    def create_category(body: CategoryIn, conn: sqlite3.Connection = Depends(get_conn)):
        name = body.name.strip()
        if not name or len(name) > 40:
            raise _err(422, "Category names need 1 to 40 characters.")
        with db.tx(conn):
            row = conn.execute("SELECT id, name FROM categories WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if row:  # existing name (e.g. a proposed one): approve it instead of failing
                conn.execute("UPDATE categories SET approved=1 WHERE id=?", (row["id"],))
            else:
                pos = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM categories").fetchone()[0]
                cur = conn.execute(
                    "INSERT INTO categories(name, position, approved) VALUES(?,?,1)", (name, pos)
                )
                row = {"id": cur.lastrowid, "name": name}
        return {"id": row["id"], "name": row["name"]}

    @private.post("/videos/{video_id}/progress")
    def progress(video_id: str, body: ProgressIn, conn: sqlite3.Connection = Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM videos WHERE id=?", (video_id,)).fetchone():
            raise _err(404, "That video isn't in your feed any more.")
        watched = watch.record_progress(
            conn, video_id, body.position_s, body.duration_s, body.sponsor_s, body.ended,
            content_position_s=body.content_position_s,
        )
        return {"watched": watched}

    @private.post("/videos/{video_id}/watched", status_code=204)
    def set_watched(video_id: str, body: WatchedIn, conn: sqlite3.Connection = Depends(get_conn)):
        if not conn.execute("SELECT 1 FROM videos WHERE id=?", (video_id,)).fetchone():
            raise _err(404, "That video isn't in your feed any more.")
        watch.set_watched(conn, video_id, body.watched)
        return Response(status_code=204)

    flow = google_auth.DeviceFlow(settings, settings.db_path, on_signed_in=lambda: _sync_now(settings))
    app.state.device_flow = flow

    @private.post("/auth/device/start")
    def auth_device_start():
        try:
            return flow.start()
        except google_auth.NotConfigured:
            raise _err(503, google_auth.NOT_SET_UP)
        except ConnectionError as e:
            raise _err(502, str(e))

    @private.get("/auth/status")
    def auth_status(conn: sqlite3.Connection = Depends(get_conn)):
        """Never includes any token. error is the setup sentence, the last sign-in failure, or the
        last subscription-sync failure, in that order. While signing_in is true an older token
        file may still make signed_in true, so the TV waits for signing_in to go false."""
        if not google_auth.configured(settings):
            return {"signed_in": False, "signing_in": False, "account": None, "error": google_auth.NOT_SET_UP,
                    "sync_error": None, "import_summary": None}
        summary = db.get_kv(conn, "import_summary")
        auth_error, sync_error = db.get_kv(conn, "auth_error"), db.get_kv(conn, "sync_error")
        return {
            "signed_in": google_auth.signed_in(settings),
            "signing_in": flow.signing_in,
            "account": db.get_kv(conn, "google_account"),
            "error": auth_error or sync_error,
            "sync_error": sync_error,
            "import_summary": json.loads(summary) if summary else None,
        }

    media = APIRouter(prefix="/media", dependencies=[Depends(require_token)])
    playback.add_routes(private, media, get_conn, settings)
    saving.add_routes(private, get_conn, settings)

    app.include_router(public)
    app.include_router(private)
    app.include_router(media)
    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """`uvicorn subfeed.api:app` builds the app from the environment on first access, so
    importing this module (tests, CLI) never reads settings or touches the database."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
