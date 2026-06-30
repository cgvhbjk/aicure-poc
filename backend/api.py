import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import re
import csv
import io
import base64
import hmac
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Query, HTTPException, Header, UploadFile, File, Form, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from typing import Optional, List
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

from db import get_connection, DB_PATH, request_connection_scope
from scoring import score_grant, score_trial

# Shared helpers/models/query-builders/jobs live in routes/_shared (a clean leaf
# both api.py and the route modules import one-way — no api<->routes cycle). The
# route modules import the specific names they use EXPLICITLY; api.py keeps the
# star-import deliberately — it both gives the middleware/lifespan the shared
# helpers AND RE-EXPORTS the full surface as `api.X`, which existing callers rely
# on (e.g. tests `from api import _trials_where, MergeConfirm`). Not the bare-name
# handler usage the route modules had, so it's a deliberate re-export, not a smell.
from routes._shared import *  # noqa: F401,F403,E402

@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(run_daily_news, "cron", hour=6, minute=0, id="daily_news")
    scheduler.add_job(run_daily_rescore, "cron", hour=7, minute=0, id="daily_rescore")
    scheduler.start()
    print("[scheduler] Daily news job at 06:00 UTC, fit rescore at 07:00 UTC")
    if _APP_PASSWORD:
        print("[auth] App-level login enabled (AICURE_APP_PASSWORD set).")
    elif os.environ.get("AICURE_ALLOW_OPEN") == "1":
        print("[auth] WARNING: AICURE_APP_PASSWORD unset and AICURE_ALLOW_OPEN=1 — "
              "the app is OPEN (no login on reads or mutations). Local dev only.")
    else:
        # Fail closed: the data carries real contact PII; refuse to serve it
        # world-open by accident (e.g. an ECS deploy that forgot the secret —
        # Render provisions it via render.yaml generateValue). Set
        # AICURE_APP_PASSWORD to gate the app, or AICURE_ALLOW_OPEN=1 to run open
        # on purpose (local dev). This runs in lifespan (app startup) only, so
        # `import api` in the test suite is unaffected.
        raise RuntimeError(
            "Refusing to start: AICURE_APP_PASSWORD unset. Set it to gate the app, "
            "or set AICURE_ALLOW_OPEN=1 to intentionally run with no login (local dev)."
        )
    if _cors_origins == ["*"]:
        print("[cors] WARNING: AICURE_CORS_ORIGINS unset — allowing all origins "
              "(*). Pin it to the real origin in any internet-facing deploy.")
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="AiCure POC API", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS. The SPA is served same-origin from this service (StaticFiles at "/"), so
# production needs no CORS at all; the wildcard default exists only for split-origin
# dev (e.g. the Vite dev server on another port). Credentials are OFF: auth here is
# a header key (X-Admin-Key), not a cookie, so nothing rides along automatically —
# and "*" + credentials would make Starlette reflect any Origin, trusting every site.
# Lock down in production by setting AICURE_CORS_ORIGINS=https://app.example.com[,...].
_cors_origins = [
    o.strip() for o in os.environ.get("AICURE_CORS_ORIGINS", "*").split(",") if o.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _app_auth(request, call_next):
    """Gate the whole app (UI + API) behind one shared credential when
    AICURE_APP_PASSWORD is set; a no-op when it's unset (local dev / tests).

    Why app-wide HTTP Basic rather than per-route key checks: the SPA is served
    same-origin, so once the browser answers the 401 Basic challenge it re-sends
    the cached Authorization header on every later XHR automatically — ZERO
    frontend changes, and no secret baked into the JS bundle. Reads are gated too
    (not just mutations): the data carries contact PII that shouldn't be
    world-readable.

    A valid X-Admin-Key is accepted as an alternative *service* credential so the
    GitHub-cron POST to /admin/* keeps working unchanged (those routes still also
    enforce the key via _require_admin). OPTIONS preflight and /healthz are exempt
    so CORS and the load-balancer probe aren't broken."""
    if (_APP_PASSWORD
            and request.method != "OPTIONS"
            and request.url.path not in _AUTH_EXEMPT_PATHS):
        admin = request.headers.get("X-Admin-Key", "")
        admin_ok = bool(_ADMIN_KEY) and hmac.compare_digest(admin, _ADMIN_KEY)
        if not (admin_ok or _valid_basic_auth(request.headers.get("Authorization", ""))):
            return Response(
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="AiCure", charset="UTF-8"'},
            )
    return await call_next(request)


@app.middleware("http")
async def _close_leaked_conns(request, call_next):
    """Force-close any DB connection opened while handling this request, even if
    the handler raised before its own conn.close() — a leaked sqlite fd
    contributes to spurious 'database is locked'. See db.request_connection_scope;
    the streaming CSV export opens its connection during the response body (after
    this scope has exited) and is closed by its own finally instead."""
    with request_connection_scope():
        return await call_next(request)


@app.get("/healthz")
def healthz():
    """Liveness + data check for the load balancer. Fails (503) if the DB can't
    be opened or comes up seeded-empty (e.g. an unresolved Git LFS pointer
    instead of the real file), so a broken task never enters the ALB rotation."""
    try:
        conn = get_connection()
        row = conn.execute("SELECT 1 FROM trials LIMIT 1").fetchone()
        conn.close()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"db unavailable: {e}")
    if row is None:
        raise HTTPException(status_code=503, detail="empty database")
    return {"status": "ok"}


# Browser-cacheable GETs. ONLY aggregate/lookup endpoints whose data changes at
# ingest cadence — list endpoints (trials/news/orgs/grants/merges) must stay
# uncached because the UI refetches them right after mutations (merge confirm,
# org PATCH, upload) and a cached 200 would serve the pre-mutation state.
_CACHEABLE_PATHS = ("/stats", "/registries/stats", "/grants/stats", "/grants/filter-options")


@app.middleware("http")
async def _cache_headers(request, call_next):
    response = await call_next(request)
    if (request.method == "GET" and response.status_code == 200
            and request.url.path in _CACHEABLE_PATHS):
        response.headers.setdefault("Cache-Control", "private, max-age=300")
    return response


# ── Mount the resource routers (handlers split into routes/*.py) ──────────────
from routes import trials, news, orgs, grants, merges, misc, admin  # noqa: E402 (after app + helpers)
app.include_router(trials.router)
app.include_router(news.router)
app.include_router(orgs.router)
app.include_router(grants.router)
app.include_router(merges.router)
app.include_router(misc.router)
app.include_router(admin.router)

# Re-export the moved handlers so `from api import <handler>` keeps working (e.g.
# test_merge calls confirm_merge/undo_merge directly to exercise merge logic).
from routes.trials import (  # noqa: E402,F401
    get_trials, export_trials, get_trial, get_trial_registries, get_trial_news,
)
from routes.news import get_news, export_news  # noqa: E402,F401
from routes.orgs import (  # noqa: E402,F401
    get_orgs, get_org, get_org_trials, get_org_contacts, add_org_contact,
    enrich_org_contacts_route, patch_org, get_relationships,
)
from routes.grants import (  # noqa: E402,F401
    get_grants_stats, get_grants_filter_options, get_grants, export_grants,
    get_grant_trials, get_grant,
)
from routes.merges import (  # noqa: E402,F401
    get_merges, confirm_merge, undo_merge, reject_merge, snooze_merge, get_merge_stats,
)
from routes.misc import upload_file, get_stats, get_registries_stats  # noqa: E402,F401
from routes.admin import (  # noqa: E402,F401
    admin_refresh_news, admin_send_news_digest, admin_send_weekly_digest, admin_prune_old,
)


# Serve the built React SPA from /frontend/dist for single-service deploys
# (e.g. Render). Mounted last so API routes take precedence. The directory
# only exists after `npm run build`, so guard against missing dir in dev.
_FRONTEND_DIST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "dist"
)
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
