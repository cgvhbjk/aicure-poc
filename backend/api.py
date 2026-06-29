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

from fastapi import FastAPI, Query, HTTPException, Header, UploadFile, File, Form, BackgroundTasks, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import StreamingResponse
from typing import Optional, List
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler

from db import get_connection, DB_PATH, request_connection_scope
from scoring import score_grant, score_trial

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

# Grant columns the grid may sort on server-side. "aicure_fit" is handled
# separately (computed in Python, not a DB column); anything off this list
# falls back to the default amount ordering.
GRANT_SORTABLE_COLUMNS = {
    "amount_usd", "award_date", "start_date", "end_date", "fiscal_year",
    "title", "status", "source", "organization", "therapeutic_area",
    "sponsor_funder", "agency_division", "activity_code", "org_type",
    "country", "pi_name", "has_trial_link",
}
# Saved-upload originals live next to the DB, wherever that is: locally that's
# backend/data/uploads (unchanged); in prod AICURE_DB_PATH points at the EFS
# mount, so uploads persist there too instead of on the task's ephemeral disk
# (which would vanish on redeploy and leave uploads.file_path dangling).
_UPLOADS_DIR = os.path.join(os.path.dirname(DB_PATH), "uploads")
os.makedirs(_UPLOADS_DIR, exist_ok=True)

_ADMIN_KEY = os.environ.get("ADMIN_KEY", "")

# Single shared credential gating the WHOLE app (UI + API), enforced by the
# _app_auth middleware ONLY when AICURE_APP_PASSWORD is set — so local dev and
# the test suite (which leave it unset) behave exactly as before. It MUST be set
# in any internet-facing deploy (the ECS/ALB migration): without it every read
# and every mutation is open to anyone who finds the URL.
_APP_USER = os.environ.get("AICURE_APP_USER", "aicure")
_APP_PASSWORD = os.environ.get("AICURE_APP_PASSWORD", "")
# Never gate the load-balancer health probe: it sends no credentials, and a 401
# there would keep the task permanently out of the ALB rotation.
_AUTH_EXEMPT_PATHS = {"/healthz"}
_news_refresh_lock = threading.Lock()


def _require_admin(x_admin_key: str):
    """Fail-closed admin guard: refuses requests when ADMIN_KEY env var is unset.
    Constant-time compare so the key can't be recovered by timing the response."""
    if not _ADMIN_KEY or not hmac.compare_digest(x_admin_key, _ADMIN_KEY):
        raise HTTPException(status_code=403, detail="Forbidden")


def _valid_basic_auth(authorization: str) -> bool:
    """True iff `authorization` is 'Basic <base64(user:pass)>' matching the
    configured app credentials. Constant-time compares so the password can't be
    recovered by timing the response."""
    if not authorization.startswith("Basic "):
        return False
    try:
        user, _, pw = base64.b64decode(authorization[6:].strip()).decode("utf-8").partition(":")
    except Exception:
        return False
    # Non-short-circuit & so both comparisons always run (no early-exit timing leak).
    return hmac.compare_digest(user, _APP_USER) & hmac.compare_digest(pw, _APP_PASSWORD)


def _like_pattern(s: str) -> str:
    """Escape SQL LIKE wildcards in user input and wrap with %. Pair with ESCAPE '\\\\'."""
    escaped = s.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _iso_day(s: str, plus_days: int = 0):
    """Normalize a date query param to a bare YYYY-MM-DD string (optionally
    shifted), for direct string comparison against stored dates. Stored values
    are either YYYY-MM-DD or full ISO timestamps; both orders correctly against
    a bare-date string, which (unlike wrapping the column in DATE()) keeps the
    predicate indexable. 422 on garbage, where DATE() used to return NULL and
    silently match nothing."""
    try:
        d = datetime.strptime(s.strip()[:10], "%Y-%m-%d") + timedelta(days=plus_days)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid date: {s!r}")
    return d.strftime("%Y-%m-%d")


def _grid_columns(table: str, exclude: set) -> str:
    """Column list for grid SELECTs: everything except the fat text fields the
    grids never render (eligibility criteria, endpoint/summary prose). Rows
    average ~8KB and the bulk of that is these fields, so trimming them cuts
    list-response size several-fold. Detail views fetch the full row by id.
    Resolved from PRAGMA table_info at import (after db._init_db has run its
    ALTERs) so new columns are picked up automatically."""
    conn = get_connection()
    try:
        cols = [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()
    return ", ".join(c for c in cols if c not in exclude)


# brief_summary is also read by the score_trial/score_grant fallback for rows
# the backfill hasn't reached; those rows re-fetch it by id (see get_trials).
_TRIAL_GRID_COLS = _grid_columns("trials", {
    "inclusion_criteria", "exclusion_criteria", "brief_summary",
    "primary_endpoints", "secondary_endpoints",
})
_GRANT_GRID_COLS = _grid_columns("grants", {"abstract"})


def _order_by_clause(sort, direction, sortable, default_col, tiebreak, prefix=""):
    """Shared ORDER BY builder for the paginated grids. The sort column is
    whitelisted against `sortable` (anything else falls back to `default_col`,
    so ?sort= can't inject SQL). `tiebreak` should make the ordering
    (near-)total so LIMIT/OFFSET pages don't shuffle rows between requests when
    the sort key has duplicates.

    NULLs are forced last in either direction. SQLite already sorts NULLs last
    under DESC, so a descending sort emits a bare `col DESC, tiebreak` that reads
    straight from the matching composite index (idx_*_<col>_*). Only ASC needs a
    leading `(col IS NULL)` term to move NULLs from first to last — and ASC is
    never a grid default, so the unindexed scan+sort it implies is the rare path.
    Emitting that prefix unconditionally (the previous behavior) defeated the
    index even on the hot DESC default — a full scan + temp B-tree, ~65x slower
    on trials."""
    col = sort if sort in sortable else default_col
    qcol = f"{prefix}{col}"
    if (direction or "desc").lower() == "asc":
        return f"ORDER BY ({qcol} IS NULL), {qcol} ASC, {tiebreak}"
    return f"ORDER BY {qcol} DESC, {tiebreak}"


# Columns the grids may sort on server-side (mirrored by SORTABLE_FIELDS in the
# corresponding frontend table components).
TRIAL_SORTABLE_COLUMNS = {
    "aicure_fit", "has_news", "therapeutic_area", "title_brief", "status",
    "phase", "sponsor", "sponsor_type", "lead_country", "enrollment",
    "start_date", "primary_completion", "study_completion", "first_posted",
    "last_updated", "id", "study_type", "num_arms", "num_sites", "pi_name",
    "is_pediatric", "epro_ecoa", "digital_biomarkers", "dct_elements",
    "ingested_at",
}
NEWS_SORTABLE_COLUMNS = {
    "published_at", "source", "title", "drug_mentioned", "phase_mentioned",
    "sponsor_mentioned", "is_trial_announcement", "is_trial_results", "trial_id",
}
ORG_SORTABLE_COLUMNS = {"canonical_name", "org_type", "trial_count"}


def cleanup_old_news():
    """Delete non-announcement news older than 7 days and repair has_news flags."""
    conn = get_connection()
    cutoff = (datetime.utcnow() - timedelta(days=7)).isoformat()
    try:
        # One transaction: dropping the links, dropping the news, and repairing
        # the denormalized has_news flag must all land or none — a partial run
        # leaves orphaned links / stale has_news. try/finally also guarantees the
        # connection is closed (a leaked fd contributes to "database is locked").
        conn.execute("BEGIN")
        conn.execute(
            "DELETE FROM trial_news_links WHERE news_id IN ("
            "  SELECT id FROM news_items WHERE is_trial_announcement = 0 AND published_at < ?"
            ")",
            (cutoff,),
        )
        deleted = conn.execute(
            "DELETE FROM news_items WHERE is_trial_announcement = 0 AND published_at < ?",
            (cutoff,),
        ).rowcount
        # Clear has_news on trials that no longer have any linked news
        conn.execute(
            "UPDATE trials SET has_news = 0 WHERE has_news = 1 AND id NOT IN ("
            "  SELECT DISTINCT trial_id FROM trial_news_links WHERE trial_id IS NOT NULL"
            ")"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(f"[cleanup] Removed {deleted} old non-announcement news items")
    return deleted


def run_daily_news():
    """Refresh RSS feeds, re-link to trials, then clean up stale items.

    Returns True on a completed refresh, False if another refresh already held
    the lock (skipped). Re-raises on actual failure so the in-app scheduler
    records a job error and the cron-driven send path (run_daily_news_and_send)
    aborts instead of emailing a stale/empty digest."""
    if not _news_refresh_lock.acquire(blocking=False):
        print("[daily-news] Already running, skipping")
        return False
    try:
        from rss_parser import parse_all_feeds
        from linker import run_linker
        print(f"[daily-news] Starting at {datetime.utcnow().isoformat()}")
        parse_all_feeds()
        run_linker()
        cleanup_old_news()
        print(f"[daily-news] Done at {datetime.utcnow().isoformat()}")
        return True
    except Exception:
        # Log the full traceback for the operator, then re-raise: a refresh that
        # failed must NOT silently fall through to sending a stale digest.
        print("[daily-news] ERROR during refresh:")
        import traceback
        traceback.print_exc()
        raise
    finally:
        _news_refresh_lock.release()


def run_daily_news_and_send(refresh: bool = True):
    """Full daily news pipeline: optionally refresh RSS + relink, then build and
    SEND the news digest. This is the piece the in-app scheduler was missing —
    run_daily_news() only refreshes the DB and never emailed anything. Intended
    to be driven by an external daily cron (see .github/workflows) so delivery
    doesn't depend on the in-app scheduler, which can't fire while a free-tier
    Render service is asleep. Returns the emailer status string.

    Raises if the refresh fails, so a broken ingest surfaces as an error (HTTP
    500 / non-200 to the cron) instead of silently sending a stale/empty digest."""
    if refresh:
        run_daily_news()  # raises on failure -> caller sees the error, no send
    import emailer
    return emailer.send_daily_news_digest()


def run_daily_rescore():
    """Re-run the AiCure fit backfill. The score is time-dependent (immediacy
    decays as start/award dates pass), so a snapshot taken at ingest drifts;
    re-scoring daily keeps the persisted aicure_fit (which the grids sort on)
    current between weekly ingests."""
    try:
        from score_backfill import backfill
        backfill()
    except Exception:
        # Print the full traceback (not just str(e)) and re-raise so APScheduler
        # records the job as failed rather than swallowing it.
        print("[scheduler] daily rescore ERROR:")
        import traceback
        traceback.print_exc()
        raise


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


def row_to_dict(row):
    return dict(row)


def _trials_where(q, status, phase, therapeutic_area, country, has_news,
                  has_euct_id, registry, sponsor, sponsor_not, min_enrollment,
                  max_enrollment, start_date_from, start_date_to,
                  completion_date_from, completion_date_to):
    """Build the shared WHERE clause for the trials list + export endpoints."""
    where_clauses = []
    params = []

    if q:
        where_clauses.append(
            "(LOWER(title_brief) LIKE ? OR LOWER(sponsor) LIKE ? OR LOWER(conditions) LIKE ? OR LOWER(interventions) LIKE ?)"
        )
        q_like = f"%{q.lower()}%"
        params.extend([q_like, q_like, q_like, q_like])

    if status:
        placeholders = ",".join("?" * len(status))
        where_clauses.append(f"status IN ({placeholders})")
        params.extend(status)

    if phase:
        placeholders = ",".join("?" * len(phase))
        where_clauses.append(f"phase IN ({placeholders})")
        params.extend(phase)

    if therapeutic_area:
        placeholders = ",".join("?" * len(therapeutic_area))
        where_clauses.append(f"therapeutic_area IN ({placeholders})")
        params.extend(therapeutic_area)

    if country:
        # Match against lead_country or any entry in the countries JSON array.
        clauses = []
        for c in country:
            clauses.append("(lead_country = ? OR countries LIKE ?)")
            params.append(c)
            params.append(f"%\"{c}\"%")
        where_clauses.append("(" + " OR ".join(clauses) + ")")

    if has_news is not None:
        op = "IN" if has_news else "NOT IN"
        where_clauses.append(
            f"id {op} (SELECT trial_id FROM trial_news_links WHERE trial_id IS NOT NULL)"
        )

    if has_euct_id is not None:
        where_clauses.append(
            "euct_id IS NOT NULL AND euct_id != ''" if has_euct_id
            else "(euct_id IS NULL OR euct_id = '')"
        )

    if registry:
        reg_clauses = " OR ".join(["registry_sources LIKE ?"] * len(registry))
        where_clauses.append(f"({reg_clauses})")
        params.extend([f"%{r}%" for r in registry])

    # Sponsor text match. Previously the FilterBar's sponsor condition was
    # applied client-side via an AG Grid filter model, which only worked while
    # the grid held every row; with server-side pagination it has to be a real
    # query param.
    if sponsor:
        where_clauses.append("LOWER(sponsor) LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(sponsor))

    if sponsor_not:
        where_clauses.append("(sponsor IS NULL OR LOWER(sponsor) NOT LIKE ? ESCAPE '\\')")
        params.append(_like_pattern(sponsor_not))

    if min_enrollment is not None:
        where_clauses.append("CAST(enrollment AS INTEGER) >= ?")
        params.append(min_enrollment)

    if max_enrollment is not None:
        where_clauses.append("CAST(enrollment AS INTEGER) <= ?")
        params.append(max_enrollment)

    if start_date_from:
        where_clauses.append("start_date >= ?")
        params.append(start_date_from)

    if start_date_to:
        where_clauses.append("start_date <= ?")
        params.append(start_date_to)

    if completion_date_from:
        where_clauses.append("primary_completion >= ?")
        params.append(completion_date_from)

    if completion_date_to:
        where_clauses.append("primary_completion <= ?")
        params.append(completion_date_to)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_sql, params


@app.get("/trials")
def get_trials(
    q: Optional[str] = None,
    status: Optional[List[str]] = Query(default=None),
    phase: Optional[List[str]] = Query(default=None),
    therapeutic_area: Optional[List[str]] = Query(default=None),
    country: Optional[List[str]] = Query(default=None),
    has_news: Optional[bool] = None,
    has_euct_id: Optional[bool] = None,
    registry: Optional[List[str]] = Query(default=None),
    sponsor: Optional[str] = None,
    sponsor_not: Optional[str] = None,
    min_enrollment: Optional[int] = None,
    max_enrollment: Optional[int] = None,
    start_date_from: Optional[str] = None,
    start_date_to: Optional[str] = None,
    completion_date_from: Optional[str] = None,
    completion_date_to: Optional[str] = None,
    sort: Optional[str] = "last_updated",
    sort_dir: str = Query("desc", alias="dir"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    conn = get_connection()

    where_sql, params = _trials_where(
        q, status, phase, therapeutic_area, country, has_news, has_euct_id,
        registry, sponsor, sponsor_not, min_enrollment, max_enrollment,
        start_date_from, start_date_to, completion_date_from, completion_date_to)

    total = conn.execute(f"SELECT COUNT(*) FROM trials {where_sql}", params).fetchone()[0]
    offset = (page - 1) * page_size
    order_by = _order_by_clause(sort, sort_dir, TRIAL_SORTABLE_COLUMNS,
                                "last_updated", "id")
    rows = conn.execute(
        f"SELECT {_TRIAL_GRID_COLS} FROM trials {where_sql} "
        f"{order_by} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    results = [row_to_dict(r) for r in rows]
    # Prefer the precomputed score; fall back for any un-backfilled row. The
    # scorer reads brief_summary, which the grid SELECT deliberately omits, so
    # re-fetch it for just the unscored rows (rare: backfill runs after every
    # ingest and daily at 07:00 UTC).
    unscored = [t["id"] for t in results if t.get("aicure_fit") is None]
    if unscored:
        placeholders = ",".join("?" * len(unscored))
        summaries = dict(conn.execute(
            f"SELECT id, brief_summary FROM trials WHERE id IN ({placeholders})",
            unscored,
        ).fetchall())
        for t in results:
            if t.get("aicure_fit") is None:
                t["aicure_fit"] = score_trial({**t, "brief_summary": summaries.get(t["id"])})
    conn.close()
    return {"total": total, "page": page, "results": results}


# (field, CSV header) — mirrors the Trials grid defaults plus contact fields.
_TRIAL_EXPORT_COLUMNS = [
    ("aicure_fit", "Fit"), ("therapeutic_area", "Area"),
    ("title_brief", "Trial Title"), ("status", "Status"), ("phase", "Phase"),
    ("sponsor", "Sponsor"), ("sponsor_type", "Sponsor Type"),
    ("lead_country", "Country"), ("enrollment", "Enrollment"),
    ("start_date", "Start"), ("primary_completion", "Primary Completion"),
    ("id", "NCT ID"), ("interventions", "Interventions"),
    ("conditions", "Conditions"), ("registry_sources", "Registries"),
    ("pi_name", "PI"), ("pi_email", "PI Email"), ("source_url", "Source URL"),
]


# NOTE: must be registered before GET /trials/{trial_id}, or "export" would be
# captured as a trial id.
@app.get("/trials/export")
def export_trials(
    q: Optional[str] = None,
    status: Optional[List[str]] = Query(default=None),
    phase: Optional[List[str]] = Query(default=None),
    therapeutic_area: Optional[List[str]] = Query(default=None),
    country: Optional[List[str]] = Query(default=None),
    has_news: Optional[bool] = None,
    has_euct_id: Optional[bool] = None,
    registry: Optional[List[str]] = Query(default=None),
    sponsor: Optional[str] = None,
    sponsor_not: Optional[str] = None,
    min_enrollment: Optional[int] = None,
    max_enrollment: Optional[int] = None,
    start_date_from: Optional[str] = None,
    start_date_to: Optional[str] = None,
    completion_date_from: Optional[str] = None,
    completion_date_to: Optional[str] = None,
    sort: Optional[str] = "last_updated",
    sort_dir: str = Query("desc", alias="dir"),
):
    """Stream the FULL filtered trial set as CSV (honors the grid's filters +
    sort). Replaces the client-side export, which only covered loaded rows
    once the grid moved to the infinite row model."""
    where_sql, params = _trials_where(
        q, status, phase, therapeutic_area, country, has_news, has_euct_id,
        registry, sponsor, sponsor_not, min_enrollment, max_enrollment,
        start_date_from, start_date_to, completion_date_from, completion_date_to)
    order_by = _order_by_clause(sort, sort_dir, TRIAL_SORTABLE_COLUMNS,
                                "last_updated", "id")

    def postprocess(t):
        if t.get("aicure_fit") is None:
            t["aicure_fit"] = score_trial(t)

    return _csv_stream(
        "trials", _TRIAL_EXPORT_COLUMNS,
        f"SELECT * FROM trials {where_sql} {order_by}", params, postprocess)


@app.get("/trials/{trial_id}")
def get_trial(trial_id: str):
    """Full trial record, including the fat text fields (summary, eligibility
    criteria, endpoints) the grid SELECT omits. The detail panel fetches this."""
    conn = get_connection()
    row = conn.execute("SELECT * FROM trials WHERE id = ?", (trial_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Trial not found")
    t = row_to_dict(row)
    if t.get("aicure_fit") is None:
        t["aicure_fit"] = score_trial(t)
    return t


def _news_where(q, source, linked_only, is_trial_announcement, is_trial_results,
                published_at_from, published_at_to, drug_mentioned,
                drug_mentioned_not, phase_mentioned, phase_mentioned_not,
                sponsor_mentioned, sponsor_mentioned_not):
    """Build the shared WHERE clause for the news list + export endpoints."""
    where_clauses = []
    params = []

    if q:
        where_clauses.append("(LOWER(ni.title) LIKE ? OR LOWER(ni.body_snippet) LIKE ?)")
        q_like = f"%{q.lower()}%"
        params.extend([q_like, q_like])

    if source:
        placeholders = ",".join("?" * len(source))
        where_clauses.append(f"ni.source IN ({placeholders})")
        params.extend(source)

    if linked_only is True:
        where_clauses.append("ni.trial_id IS NOT NULL")
    elif linked_only is False:
        where_clauses.append("ni.trial_id IS NULL")

    if is_trial_announcement is not None:
        where_clauses.append("ni.is_trial_announcement = ?")
        params.append(1 if is_trial_announcement else 0)

    if is_trial_results is not None:
        where_clauses.append("ni.is_trial_results = ?")
        params.append(1 if is_trial_results else 0)

    # Bare-date string bounds instead of DATE(col) — same rationale and
    # mixed-format safety as the grants award_date filter (see _iso_day).
    if published_at_from:
        where_clauses.append("ni.published_at >= ?")
        params.append(_iso_day(published_at_from))

    if published_at_to:
        where_clauses.append("ni.published_at > '' AND ni.published_at < ?")
        params.append(_iso_day(published_at_to, plus_days=1))

    if drug_mentioned:
        where_clauses.append("LOWER(ni.drug_mentioned) LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(drug_mentioned))

    if drug_mentioned_not:
        where_clauses.append("(ni.drug_mentioned IS NULL OR LOWER(ni.drug_mentioned) NOT LIKE ? ESCAPE '\\')")
        params.append(_like_pattern(drug_mentioned_not))

    if phase_mentioned:
        where_clauses.append("LOWER(ni.phase_mentioned) LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(phase_mentioned))

    if phase_mentioned_not:
        where_clauses.append("(ni.phase_mentioned IS NULL OR LOWER(ni.phase_mentioned) NOT LIKE ? ESCAPE '\\')")
        params.append(_like_pattern(phase_mentioned_not))

    if sponsor_mentioned:
        where_clauses.append("LOWER(ni.sponsor_mentioned) LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(sponsor_mentioned))

    if sponsor_mentioned_not:
        where_clauses.append("(ni.sponsor_mentioned IS NULL OR LOWER(ni.sponsor_mentioned) NOT LIKE ? ESCAPE '\\')")
        params.append(_like_pattern(sponsor_mentioned_not))

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_sql, params


# The list + export endpoints share this SELECT (joined trial context + best
# match method per item).
_NEWS_SELECT = """
    SELECT ni.*,
           (SELECT match_method FROM trial_news_links
            WHERE news_id = ni.id
            ORDER BY match_method DESC LIMIT 1) AS match_method,
           t.title_brief        AS trial_title,
           t.status             AS trial_status,
           t.phase              AS trial_phase,
           t.therapeutic_area   AS trial_therapeutic_area,
           t.sponsor            AS trial_sponsor
    FROM news_items ni
    LEFT JOIN trials t ON ni.trial_id = t.id
"""


@app.get("/news")
def get_news(
    q: Optional[str] = None,
    source: Optional[List[str]] = Query(default=None),
    linked_only: Optional[bool] = None,
    is_trial_announcement: Optional[bool] = None,
    is_trial_results: Optional[bool] = None,
    published_at_from: Optional[str] = None,
    published_at_to: Optional[str] = None,
    drug_mentioned: Optional[str] = None,
    drug_mentioned_not: Optional[str] = None,
    phase_mentioned: Optional[str] = None,
    phase_mentioned_not: Optional[str] = None,
    sponsor_mentioned: Optional[str] = None,
    sponsor_mentioned_not: Optional[str] = None,
    sort: Optional[str] = "published_at",
    sort_dir: str = Query("desc", alias="dir"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    # Build the WHERE first: _iso_day() can raise 422 on a bad date param, and
    # doing it before opening the connection avoids leaking one on that path.
    where_sql, params = _news_where(
        q, source, linked_only, is_trial_announcement, is_trial_results,
        published_at_from, published_at_to, drug_mentioned, drug_mentioned_not,
        phase_mentioned, phase_mentioned_not, sponsor_mentioned,
        sponsor_mentioned_not)
    conn = get_connection()

    total = conn.execute(
        f"SELECT COUNT(*) FROM news_items ni {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * page_size
    order_by = _order_by_clause(sort, sort_dir, NEWS_SORTABLE_COLUMNS,
                                "published_at", "ni.id DESC", prefix="ni.")
    rows = conn.execute(
        f"{_NEWS_SELECT} {where_sql} {order_by} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    conn.close()
    return {"total": total, "results": [row_to_dict(r) for r in rows]}


# (field, CSV header) — mirrors the News grid.
_NEWS_EXPORT_COLUMNS = [
    ("source", "Source"), ("title", "Title"), ("url", "URL"),
    ("published_at", "Published"), ("body_snippet", "Snippet"),
    ("drug_mentioned", "Drug"), ("phase_mentioned", "Phase"),
    ("sponsor_mentioned", "Sponsor"), ("nct_ids_found", "NCTs in Article"),
    ("trial_id", "Linked NCT"), ("trial_title", "Linked Trial Title"),
    ("trial_status", "Trial Status"), ("trial_therapeutic_area", "Trial Area"),
    ("match_method", "Match"),
]


@app.get("/news/export")
def export_news(
    q: Optional[str] = None,
    source: Optional[List[str]] = Query(default=None),
    linked_only: Optional[bool] = None,
    is_trial_announcement: Optional[bool] = None,
    is_trial_results: Optional[bool] = None,
    published_at_from: Optional[str] = None,
    published_at_to: Optional[str] = None,
    drug_mentioned: Optional[str] = None,
    drug_mentioned_not: Optional[str] = None,
    phase_mentioned: Optional[str] = None,
    phase_mentioned_not: Optional[str] = None,
    sponsor_mentioned: Optional[str] = None,
    sponsor_mentioned_not: Optional[str] = None,
    sort: Optional[str] = "published_at",
    sort_dir: str = Query("desc", alias="dir"),
):
    """Stream the FULL filtered news set as CSV (honors the grid's filters +
    sort); see export_trials for why this is server-side."""
    where_sql, params = _news_where(
        q, source, linked_only, is_trial_announcement, is_trial_results,
        published_at_from, published_at_to, drug_mentioned, drug_mentioned_not,
        phase_mentioned, phase_mentioned_not, sponsor_mentioned,
        sponsor_mentioned_not)
    order_by = _order_by_clause(sort, sort_dir, NEWS_SORTABLE_COLUMNS,
                                "published_at", "ni.id DESC", prefix="ni.")
    return _csv_stream(
        "news", _NEWS_EXPORT_COLUMNS,
        f"{_NEWS_SELECT} {where_sql} {order_by}", params)


@app.get("/trials/{trial_id}/registries")
def get_trial_registries(trial_id: str):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT registry, registry_trial_id, ingested_at
        FROM registry_source_records
        WHERE trial_id = ?
        ORDER BY registry
        """,
        (trial_id,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.get("/trials/{nct_id}/news")
def get_trial_news(nct_id: str):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT ni.*, tnl.match_method
        FROM news_items ni
        JOIN trial_news_links tnl ON ni.id = tnl.news_id
        WHERE tnl.trial_id = ?
        ORDER BY ni.published_at DESC
        """,
        (nct_id,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


class OrgUpdate(BaseModel):
    org_type: Optional[str] = None
    white_label_signal: Optional[str] = None
    funding_stage: Optional[str] = None
    offerings: Optional[str] = None
    notes: Optional[str] = None
    website: Optional[str] = None
    linkedin_url: Optional[str] = None


class ContactCreate(BaseModel):
    full_name: str
    title: Optional[str] = None
    department: Optional[str] = None
    email: Optional[str] = None
    linkedin_url: Optional[str] = None
    source_url: Optional[str] = None
    is_decision_maker: Optional[int] = 0
    notes: Optional[str] = None


_PATCHABLE_ORG_FIELDS = {
    "org_type", "white_label_signal", "funding_stage", "offerings",
    "notes", "website", "linkedin_url",
}


@app.get("/orgs")
def get_orgs(
    q: Optional[str] = None,
    org_type: Optional[List[str]] = Query(default=None),
    therapeutic_focus: Optional[List[str]] = Query(default=None),
    white_label: Optional[str] = None,
    has_trials: Optional[bool] = None,
    sort: Optional[str] = "trial_count",
    sort_dir: str = Query("desc", alias="dir"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    conn = get_connection()

    where_clauses = []
    params = []

    if q:
        where_clauses.append(
            "(LOWER(o.canonical_name) LIKE ? OR LOWER(o.aliases) LIKE ? OR LOWER(o.offerings) LIKE ?)"
        )
        q_like = f"%{q.lower()}%"
        params.extend([q_like, q_like, q_like])

    if org_type:
        placeholders = ",".join("?" * len(org_type))
        where_clauses.append(f"o.org_type IN ({placeholders})")
        params.extend(org_type)

    if therapeutic_focus:
        tf_clauses = " OR ".join(["o.therapeutic_focus LIKE ?"] * len(therapeutic_focus))
        where_clauses.append(f"({tf_clauses})")
        params.extend([f"%{tf}%" for tf in therapeutic_focus])

    if white_label:
        where_clauses.append("o.white_label_signal = ?")
        params.append(white_label)

    if has_trials:
        where_clauses.append("o.trial_count > 0")

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    total = conn.execute(f"SELECT COUNT(*) FROM organizations o {where_sql}", params).fetchone()[0]
    offset = (page - 1) * page_size
    order_by = _order_by_clause(sort, sort_dir, ORG_SORTABLE_COLUMNS,
                                "trial_count", "o.canonical_name", prefix="o.")
    rows = conn.execute(
        f"SELECT o.* FROM organizations o {where_sql} {order_by} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    conn.close()
    return {"total": total, "page": page, "results": [row_to_dict(r) for r in rows]}


@app.get("/orgs/{org_id}")
def get_org(org_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return row_to_dict(row)


@app.get("/orgs/{org_id}/trials")
def get_org_trials(org_id: str):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT t.*, tol.role
        FROM trials t
        JOIN trial_org_links tol ON t.id = tol.trial_id
        WHERE tol.org_id = ?
        ORDER BY t.last_updated DESC
        """,
        (org_id,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.get("/orgs/{org_id}/contacts")
def get_org_contacts(org_id: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM org_contacts WHERE org_id = ? ORDER BY is_decision_maker DESC, full_name",
        (org_id,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.post("/orgs/{org_id}/contacts")
def add_org_contact(org_id: str, body: ContactCreate):
    conn = get_connection()
    org = conn.execute("SELECT id FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not org:
        conn.close()
        raise HTTPException(status_code=404, detail="Organization not found")

    from datetime import datetime
    cur = conn.execute(
        """
        INSERT INTO org_contacts
            (org_id, full_name, title, department, email, linkedin_url, source_url, is_decision_maker, notes, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            org_id, body.full_name, body.title, body.department, body.email,
            body.linkedin_url, body.source_url, body.is_decision_maker or 0,
            body.notes, datetime.utcnow().isoformat(),
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM org_contacts WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    return row_to_dict(row)


@app.post("/orgs/{org_id}/enrich-contacts")
def enrich_org_contacts_route(org_id: str, force_refresh: bool = False,
                              x_admin_key: str = Header(default="")):
    """Enrich an org's contacts with CMO / clinical decision-makers via Seamless.AI
    (§7). Admin-guarded (it can spend Seamless credits). Served from the credit
    cache when possible; returns api_calls=0 when no credits were spent. No-ops
    cleanly when SEAMLESS_API_KEY is unset."""
    _require_admin(x_admin_key)
    from seamless import enrich_org_contacts
    result = enrich_org_contacts(org_id, force_refresh=force_refresh)
    if not result.get("ok") and result.get("error") == "organization not found":
        raise HTTPException(status_code=404, detail="Organization not found")
    # Return the refreshed contact list alongside the enrichment status.
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM org_contacts WHERE org_id = ? ORDER BY is_decision_maker DESC, full_name",
        (org_id,),
    ).fetchall()
    conn.close()
    return {"status": result, "contacts": [row_to_dict(r) for r in rows]}


@app.patch("/orgs/{org_id}")
def patch_org(org_id: str, body: OrgUpdate):
    conn = get_connection()
    org = conn.execute("SELECT id FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not org:
        conn.close()
        raise HTTPException(status_code=404, detail="Organization not found")

    updates = {k: v for k, v in body.model_dump().items() if v is not None and k in _PATCHABLE_ORG_FIELDS}
    if not updates:
        row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
        conn.close()
        return row_to_dict(row)

    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    conn.execute(
        f"UPDATE organizations SET {set_clauses} WHERE id = ?",
        list(updates.values()) + [org_id],
    )
    conn.commit()
    row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    conn.close()
    return row_to_dict(row)


@app.get("/relationships")
def get_relationships(
    org_id: Optional[str] = None,
    therapeutic_area: Optional[List[str]] = Query(default=None),
    status: Optional[List[str]] = Query(default=None),
    phase: Optional[List[str]] = Query(default=None),
):
    conn = get_connection()

    # Determine which orgs to show
    if org_id:
        orgs = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchall()
    else:
        orgs = conn.execute(
            "SELECT * FROM organizations ORDER BY trial_count DESC LIMIT 20"
        ).fetchall()

    org_ids = [o["id"] for o in orgs]
    if not org_ids:
        conn.close()
        return {"nodes": [], "edges": [], "total_nodes": 0}

    # Get trial links for these orgs
    placeholders = ",".join("?" * len(org_ids))
    links = conn.execute(
        f"SELECT trial_id, org_id, role FROM trial_org_links WHERE org_id IN ({placeholders})",
        org_ids,
    ).fetchall()

    trial_ids = list({lnk["trial_id"] for lnk in links})
    if not trial_ids:
        conn.close()
        org_nodes = [
            {"id": o["id"], "label": o["canonical_name"], "type": o["org_type"] or "OTHER", "trial_count": o["trial_count"] or 0}
            for o in orgs
        ]
        return {"nodes": org_nodes, "edges": [], "total_nodes": len(org_nodes)}

    # Apply trial filters — default to RECRUITING + NOT_YET_RECRUITING
    status_filter = status if status else ["RECRUITING", "NOT_YET_RECRUITING"]
    t_ph = ",".join("?" * len(trial_ids))
    s_ph = ",".join("?" * len(status_filter))
    trial_where = [f"id IN ({t_ph})", f"status IN ({s_ph})"]
    trial_params = trial_ids + status_filter

    if therapeutic_area:
        ta_ph = ",".join("?" * len(therapeutic_area))
        trial_where.append(f"therapeutic_area IN ({ta_ph})")
        trial_params.extend(therapeutic_area)

    if phase:
        ph_ph = ",".join("?" * len(phase))
        trial_where.append(f"phase IN ({ph_ph})")
        trial_params.extend(phase)

    trials = conn.execute(
        f"SELECT id, title_brief, status, phase, therapeutic_area FROM trials WHERE {' AND '.join(trial_where)}",
        trial_params,
    ).fetchall()
    conn.close()

    valid_trial_ids = {t["id"] for t in trials}

    org_nodes = [
        {"id": o["id"], "label": o["canonical_name"], "type": o["org_type"] or "OTHER", "trial_count": o["trial_count"] or 0}
        for o in orgs
    ]
    trial_nodes = [
        {"id": t["id"], "label": t["title_brief"] or t["id"], "type": "TRIAL", "status": t["status"], "phase": t["phase"]}
        for t in trials
    ]
    edges = [
        {"source": lnk["org_id"], "target": lnk["trial_id"], "role": lnk["role"]}
        for lnk in links
        if lnk["trial_id"] in valid_trial_ids
    ]

    all_nodes = org_nodes + trial_nodes
    return {
        "nodes": all_nodes,
        "edges": edges,
        "total_nodes": len(all_nodes),
    }


class MergeConfirm(BaseModel):
    reviewed_by: Optional[str] = ""
    surviving_id: Optional[str] = None


class MergeReview(BaseModel):
    reviewed_by: Optional[str] = ""


_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    entity_type: str = Form(...),
    analyst_name: str = Form(default=""),
    notes: str = Form(default=""),
):
    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 10 MB)")

    valid_types = ("trials", "organizations", "contacts")
    if entity_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"entity_type must be one of {list(valid_types)}")

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_name = re.sub(r"[^\w.\-]", "_", file.filename or "upload")
    save_path = os.path.join(_UPLOADS_DIR, f"{ts}_{safe_name}")
    with open(save_path, "wb") as fh:
        fh.write(content)

    from upload_processor import process_upload
    result = process_upload(content, file.filename or "", entity_type)

    conn = get_connection()
    cur = conn.execute(
        """INSERT INTO uploads
           (filename, entity_type, row_count, matched_count, new_count, skipped_count,
            uploaded_at, uploaded_by, notes, file_path)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (file.filename, entity_type,
         result["row_count"], result["matched"], result["inserted"], result["skipped"],
         datetime.utcnow().isoformat(), analyst_name, notes, save_path),
    )
    upload_id = cur.lastrowid
    conn.commit()
    conn.close()

    errors = result.get("errors", [])
    # Partial success is legitimate (200), but a file where EVERY row failed must
    # not look like success to a caller that only checks the status code — flag it
    # explicitly. errors is truncated to the first 50 (error_count has the total).
    all_failed = (
        result["row_count"] > 0 and result["inserted"] == 0 and result["matched"] == 0
    )
    return {
        "status": "all_failed" if all_failed else "ok",
        "upload_id": upload_id,
        "filename": file.filename,
        "row_count": result["row_count"],
        "matched": result["matched"],
        "inserted": result["inserted"],
        "skipped": result["skipped"],
        "errors": errors[:50],
        "errors_truncated": len(errors) > 50,
        "error_count": len(errors),
        "merge_candidates": result.get("merge_candidates", 0),
        "preview": result.get("preview", []),
    }


@app.get("/merges")
def get_merges(
    entity_type: Optional[str] = None,
    status: str = "PENDING",
    min_confidence: float = 0.0,
    max_confidence: float = 1.0,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    conn = get_connection()

    where = ["mc.confidence >= ?", "mc.confidence <= ?"]
    params: list = [min_confidence, max_confidence]

    if status == "PENDING":
        where.append("(mc.status = 'PENDING' OR (mc.status = 'SNOOZED' AND mc.snooze_until < ?))")
        params.append(datetime.utcnow().isoformat())
    else:
        where.append("mc.status = ?")
        params.append(status)

    if entity_type:
        where.append("mc.entity_type = ?")
        params.append(entity_type)

    where_sql = "WHERE " + " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) FROM merge_candidates mc {where_sql}", params).fetchone()[0]
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""SELECT mc.id, mc.entity_type, mc.record_a_id, mc.record_b_id, mc.confidence,
                   mc.match_fields, mc.match_scores, mc.status, mc.reviewed_by, mc.reviewed_at,
                   mc.merged_into, mc.snooze_until, mc.created_at,
                   (mc.loser_snapshot IS NOT NULL) AS loser_snapshot
            FROM merge_candidates mc {where_sql}
            ORDER BY mc.confidence DESC, mc.created_at DESC LIMIT ? OFFSET ?""",
        params + [page_size, offset],
    ).fetchall()

    candidates = [row_to_dict(r) for r in rows]
    for c in candidates:
        c["loser_snapshot"] = bool(c["loser_snapshot"])

    # Batch-fetch entity records
    trial_ids, org_ids = set(), set()
    for c in candidates:
        if c["entity_type"] == "trials":
            trial_ids.update([c["record_a_id"], c["record_b_id"]])
        else:
            org_ids.update([c["record_a_id"], c["record_b_id"]])

    trials_map, orgs_map = {}, {}
    if trial_ids:
        ph = ",".join("?" * len(trial_ids))
        for r in conn.execute(f"SELECT * FROM trials WHERE id IN ({ph})", list(trial_ids)).fetchall():
            trials_map[r["id"]] = row_to_dict(r)
    if org_ids:
        ph = ",".join("?" * len(org_ids))
        for r in conn.execute(f"SELECT * FROM organizations WHERE id IN ({ph})", list(org_ids)).fetchall():
            orgs_map[r["id"]] = row_to_dict(r)

    conn.close()

    for c in candidates:
        if c["entity_type"] == "trials":
            c["record_a"] = trials_map.get(c["record_a_id"])
            c["record_b"] = trials_map.get(c["record_b_id"])
        else:
            c["record_a"] = orgs_map.get(c["record_a_id"])
            c["record_b"] = orgs_map.get(c["record_b_id"])

    return {"total": total, "page": page, "results": candidates}


_TRIAL_FK_TABLES = [
    ("registry_source_records", "trial_id"),
    ("trial_org_links", "trial_id"),
    ("trial_news_links", "trial_id"),
    ("grant_trial_links", "trial_id"),
]
_ORG_FK_TABLES = [
    ("trial_org_links", "org_id"),
    ("organization_aliases", "org_id"),
    ("org_contacts", "org_id"),
]


def _snapshot_pre_merge(conn, entity_type, survivor_id, loser_id):
    """Capture the loser row, survivor row, and all FK rows touching either entity,
    so a later /undo can restore the pre-merge world byte-for-byte."""
    import json as _json
    entity_table = "trials" if entity_type == "trials" else "organizations"
    fk_tables = _TRIAL_FK_TABLES if entity_type == "trials" else _ORG_FK_TABLES

    loser = conn.execute(f"SELECT * FROM {entity_table} WHERE id = ?", (loser_id,)).fetchone()
    survivor = conn.execute(f"SELECT * FROM {entity_table} WHERE id = ?", (survivor_id,)).fetchone()

    fk_pre = {}
    for table, col in fk_tables:
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE {col} IN (?, ?)", (survivor_id, loser_id)
        ).fetchall()
        fk_pre[table] = [dict(r) for r in rows]

    return _json.dumps({
        "loser_row": dict(loser) if loser else None,
        "survivor_row": dict(survivor) if survivor else None,
        "fk_pre_state": fk_pre,
    })


@app.post("/merges/{merge_id}/confirm")
def confirm_merge(merge_id: int, body: MergeConfirm):
    conn = get_connection()
    try:
        mc = conn.execute("SELECT * FROM merge_candidates WHERE id = ?", (merge_id,)).fetchone()
        if not mc:
            raise HTTPException(status_code=404, detail="Merge candidate not found")

        survivor_id = body.surviving_id or mc["record_a_id"]
        loser_id = mc["record_b_id"] if survivor_id == mc["record_a_id"] else mc["record_a_id"]

        snapshot_json = _snapshot_pre_merge(conn, mc["entity_type"], survivor_id, loser_id)

        if mc["entity_type"] == "trials":
            survivor = conn.execute("SELECT * FROM trials WHERE id = ?", (survivor_id,)).fetchone()
            loser = conn.execute("SELECT * FROM trials WHERE id = ?", (loser_id,)).fetchone()
            if not survivor:
                raise HTTPException(status_code=400, detail=f"Survivor trial {survivor_id} not found")
            if loser:
                # Transfer registry info
                import json as _json
                s_sources = _json.loads(survivor["registry_sources"] or "[]")
                s_ids = _json.loads(survivor["all_registry_ids"] or "[]")
                b_sources = _json.loads(loser["registry_sources"] or "[]")
                b_ids = _json.loads(loser["all_registry_ids"] or "[]")
                for src in b_sources:
                    if src not in s_sources:
                        s_sources.append(src)
                for rid in b_ids + [loser_id]:
                    if rid not in s_ids:
                        s_ids.append(rid)

                from merge_detector import _id_col_for
                id_col, reg_val = _id_col_for(loser_id)
                extra_sql = f", {id_col} = ?" if id_col else ""
                extra_params = [reg_val] if id_col else []
                conn.execute(
                    f"UPDATE trials SET registry_sources = ?, all_registry_ids = ?{extra_sql} WHERE id = ?",
                    [_json.dumps(s_sources), _json.dumps(s_ids)] + extra_params + [survivor_id],
                )

                # Reassign FK references
                conn.execute("UPDATE registry_source_records SET trial_id = ? WHERE trial_id = ?", (survivor_id, loser_id))
                conn.execute("INSERT OR IGNORE INTO trial_org_links (trial_id, org_id, role) SELECT ?, org_id, role FROM trial_org_links WHERE trial_id = ?", (survivor_id, loser_id))
                conn.execute("DELETE FROM trial_org_links WHERE trial_id = ?", (loser_id,))
                conn.execute("INSERT OR IGNORE INTO trial_news_links (trial_id, news_id, match_method) SELECT ?, news_id, match_method FROM trial_news_links WHERE trial_id = ?", (survivor_id, loser_id))
                conn.execute("DELETE FROM trial_news_links WHERE trial_id = ?", (loser_id,))
                # Grant links reference trial_id too; reassign to the survivor (PK
                # (grant_id, trial_id) → OR IGNORE drops any that would collide)
                # before deleting the loser, so the link isn't orphaned/lost.
                conn.execute("INSERT OR IGNORE INTO grant_trial_links (grant_id, trial_id, match_method) SELECT grant_id, ?, match_method FROM grant_trial_links WHERE trial_id = ?", (survivor_id, loser_id))
                conn.execute("DELETE FROM grant_trial_links WHERE trial_id = ?", (loser_id,))
                conn.execute("DELETE FROM trials WHERE id = ?", (loser_id,))

        elif mc["entity_type"] == "organizations":
            import json as _json
            survivor = conn.execute("SELECT * FROM organizations WHERE id = ?", (survivor_id,)).fetchone()
            loser = conn.execute("SELECT * FROM organizations WHERE id = ?", (loser_id,)).fetchone()
            if not survivor:
                raise HTTPException(status_code=400, detail=f"Survivor org {survivor_id} not found")
            if loser:
                # Merge aliases + therapeutic_focus arrays, preferring survivor for scalars.
                def _merge_json_list(a, b):
                    la = _json.loads(a or "[]") if a else []
                    lb = _json.loads(b or "[]") if b else []
                    out = list(la)
                    for x in lb:
                        if x not in out:
                            out.append(x)
                    return _json.dumps(out)

                merged_aliases = _merge_json_list(survivor["aliases"], loser["aliases"])
                # Add the loser's canonical_name as an alias too.
                try:
                    al = _json.loads(merged_aliases)
                    if loser["canonical_name"] and loser["canonical_name"] not in al:
                        al.append(loser["canonical_name"])
                        merged_aliases = _json.dumps(al)
                except Exception:
                    pass
                merged_focus = _merge_json_list(survivor["therapeutic_focus"], loser["therapeutic_focus"])

                conn.execute(
                    "UPDATE organizations SET aliases = ?, therapeutic_focus = ? WHERE id = ?",
                    (merged_aliases, merged_focus, survivor_id),
                )

                # Reassign FK references: trial_org_links, organization_aliases, org_contacts.
                conn.execute(
                    "INSERT OR IGNORE INTO trial_org_links (trial_id, org_id, role) "
                    "SELECT trial_id, ?, role FROM trial_org_links WHERE org_id = ?",
                    (survivor_id, loser_id),
                )
                conn.execute("DELETE FROM trial_org_links WHERE org_id = ?", (loser_id,))
                conn.execute(
                    "UPDATE OR IGNORE organization_aliases SET org_id = ? WHERE org_id = ?",
                    (survivor_id, loser_id),
                )
                conn.execute("DELETE FROM organization_aliases WHERE org_id = ?", (loser_id,))
                conn.execute(
                    "UPDATE org_contacts SET org_id = ? WHERE org_id = ?",
                    (survivor_id, loser_id),
                )

                # Recompute trial_count on survivor and remove loser.
                new_count = conn.execute(
                    "SELECT COUNT(DISTINCT trial_id) FROM trial_org_links WHERE org_id = ?",
                    (survivor_id,),
                ).fetchone()[0]
                conn.execute(
                    "UPDATE organizations SET trial_count = ? WHERE id = ?",
                    (new_count, survivor_id),
                )
                conn.execute("DELETE FROM organizations WHERE id = ?", (loser_id,))

        now = datetime.utcnow().isoformat()
        conn.execute(
            """UPDATE merge_candidates SET status = 'CONFIRMED_MERGE', reviewed_by = ?,
               reviewed_at = ?, merged_into = ?, loser_snapshot = ? WHERE id = ?""",
            (body.reviewed_by, now, survivor_id, snapshot_json, merge_id),
        )
        conn.commit()
        return {"status": "ok", "merged_into": survivor_id}
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.post("/merges/{merge_id}/undo")
def undo_merge(merge_id: int):
    """Restore the loser entity and pre-merge FK state from the snapshot taken at confirm time."""
    import json as _json
    conn = get_connection()
    try:
        mc = conn.execute("SELECT * FROM merge_candidates WHERE id = ?", (merge_id,)).fetchone()
        if not mc:
            raise HTTPException(status_code=404, detail="Merge candidate not found")
        if mc["status"] != "CONFIRMED_MERGE":
            raise HTTPException(status_code=400, detail=f"Can only undo CONFIRMED_MERGE candidates (current: {mc['status']})")
        if not mc["loser_snapshot"]:
            raise HTTPException(status_code=400, detail="No snapshot available — this merge was confirmed before undo was supported")

        snapshot = _json.loads(mc["loser_snapshot"])
        entity_type = mc["entity_type"]
        survivor_id = mc["merged_into"] or mc["record_a_id"]
        loser_id = mc["record_b_id"] if survivor_id == mc["record_a_id"] else mc["record_a_id"]

        entity_table = "trials" if entity_type == "trials" else "organizations"
        fk_tables = _TRIAL_FK_TABLES if entity_type == "trials" else _ORG_FK_TABLES

        # Wipe current FK rows for both entities, then re-insert the pre-merge snapshot.
        for table, col in fk_tables:
            conn.execute(f"DELETE FROM {table} WHERE {col} IN (?, ?)", (survivor_id, loser_id))
        for table, _ in fk_tables:
            for row in snapshot["fk_pre_state"].get(table, []):
                cols = list(row.keys())
                placeholders = ",".join("?" * len(cols))
                col_list = ",".join(cols)
                conn.execute(
                    f"INSERT OR IGNORE INTO {table} ({col_list}) VALUES ({placeholders})",
                    [row[c] for c in cols],
                )

        # Restore loser + survivor rows to their pre-merge field values.
        for row_key in ("loser_row", "survivor_row"):
            row = snapshot.get(row_key)
            if not row:
                continue
            cols = list(row.keys())
            placeholders = ",".join("?" * len(cols))
            col_list = ",".join(cols)
            conn.execute(
                f"INSERT OR REPLACE INTO {entity_table} ({col_list}) VALUES ({placeholders})",
                [row[c] for c in cols],
            )

        # Reset the candidate to PENDING so it shows up again for review.
        conn.execute(
            """UPDATE merge_candidates
               SET status = 'PENDING', reviewed_by = NULL, reviewed_at = NULL,
                   merged_into = NULL, loser_snapshot = NULL WHERE id = ?""",
            (merge_id,),
        )
        conn.commit()
        return {"status": "ok", "restored_loser": loser_id}
    except HTTPException:
        conn.rollback()
        conn.close()
        raise
    except Exception as e:
        conn.rollback()
        conn.close()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        try:
            conn.close()
        except Exception:
            pass


@app.post("/merges/{merge_id}/reject")
def reject_merge(merge_id: int, body: Optional[MergeReview] = None):
    conn = get_connection()
    mc = conn.execute("SELECT id FROM merge_candidates WHERE id = ?", (merge_id,)).fetchone()
    if not mc:
        conn.close()
        raise HTTPException(status_code=404, detail="Not found")
    reviewed_by = body.reviewed_by if body else ""
    conn.execute(
        "UPDATE merge_candidates SET status = 'REJECTED', reviewed_by = ?, reviewed_at = ? WHERE id = ?",
        (reviewed_by, datetime.utcnow().isoformat(), merge_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok"}


@app.post("/merges/{merge_id}/snooze")
def snooze_merge(merge_id: int):
    conn = get_connection()
    mc = conn.execute("SELECT id FROM merge_candidates WHERE id = ?", (merge_id,)).fetchone()
    if not mc:
        conn.close()
        raise HTTPException(status_code=404, detail="Not found")
    snooze_until = (datetime.utcnow() + timedelta(days=30)).isoformat()
    conn.execute(
        "UPDATE merge_candidates SET status = 'SNOOZED', snooze_until = ? WHERE id = ?",
        (snooze_until, merge_id),
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "snooze_until": snooze_until}


@app.get("/merges/stats")
def get_merge_stats():
    conn = get_connection()
    now = datetime.utcnow().isoformat()
    week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()

    pending = conn.execute(
        "SELECT COUNT(*) FROM merge_candidates WHERE status = 'PENDING'"
    ).fetchone()[0]
    snoozed = conn.execute(
        "SELECT COUNT(*) FROM merge_candidates WHERE status = 'SNOOZED' AND snooze_until > ?",
        (now,)
    ).fetchone()[0]
    confirmed_week = conn.execute(
        "SELECT COUNT(*) FROM merge_candidates WHERE status = 'CONFIRMED_MERGE' AND reviewed_at >= ?",
        (week_ago,)
    ).fetchone()[0]
    rejected_week = conn.execute(
        "SELECT COUNT(*) FROM merge_candidates WHERE status = 'REJECTED' AND reviewed_at >= ?",
        (week_ago,)
    ).fetchone()[0]
    auto_merged = conn.execute(
        "SELECT COUNT(*) FROM merge_candidates WHERE status = 'CONFIRMED_MERGE' AND (reviewed_by IS NULL OR reviewed_by = '')"
    ).fetchone()[0]
    conn.close()

    return {
        "pending": pending,
        "snoozed": snoozed,
        "confirmed_this_week": confirmed_week,
        "rejected_this_week": rejected_week,
        "auto_merged": auto_merged,
    }


@app.get("/stats")
def get_stats():
    conn = get_connection()

    total_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    trials_with_news = conn.execute(
        "SELECT COUNT(DISTINCT trial_id) FROM trial_news_links"
    ).fetchone()[0]
    total_news = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
    unlinked_news = conn.execute("SELECT COUNT(*) FROM news_items WHERE trial_id IS NULL").fetchone()[0]
    total_orgs = conn.execute("SELECT COUNT(*) FROM organizations").fetchone()[0]

    by_status = {
        r["status"] or "Unknown": r["n"]
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM trials GROUP BY status").fetchall()
    }
    by_phase = {
        r["phase"] or "Unknown": r["n"]
        for r in conn.execute("SELECT phase, COUNT(*) AS n FROM trials GROUP BY phase").fetchall()
    }
    by_therapeutic_area = {
        r["therapeutic_area"] or "Unknown": r["n"]
        for r in conn.execute(
            "SELECT therapeutic_area, COUNT(*) AS n FROM trials GROUP BY therapeutic_area"
        ).fetchall()
    }

    eu_ctis_count = conn.execute(
        "SELECT COUNT(*) FROM trials WHERE euct_id IS NOT NULL"
    ).fetchone()[0]
    eu_ctr_count = conn.execute(
        "SELECT COUNT(*) FROM trials WHERE eudract_number IS NOT NULL"
    ).fetchone()[0]
    by_registry = {
        r["registry"]: r["n"]
        for r in conn.execute(
            "SELECT registry, COUNT(*) AS n FROM registry_source_records GROUP BY registry"
        ).fetchall()
    }
    by_country = {
        r["lead_country"]: r["n"]
        for r in conn.execute(
            "SELECT lead_country, COUNT(*) AS n FROM trials "
            "WHERE lead_country IS NOT NULL AND lead_country != '' "
            "GROUP BY lead_country ORDER BY n DESC LIMIT 40"
        ).fetchall()
    }

    last_ingested = conn.execute("SELECT MAX(ingested_at) FROM trials").fetchone()[0]
    conn.close()

    return {
        "total_trials": total_trials,
        "trials_with_news": trials_with_news,
        "total_news": total_news,
        "unlinked_news": unlinked_news,
        "total_orgs": total_orgs,
        "eu_ctis_count": eu_ctis_count,
        "eu_ctr_count": eu_ctr_count,
        "by_status": by_status,
        "by_phase": by_phase,
        "by_therapeutic_area": by_therapeutic_area,
        "by_registry": by_registry,
        "by_country": by_country,
        "last_ingested": last_ingested,
    }


@app.get("/registries/stats")
def get_registries_stats():
    """Per-registry counts plus cross-registration breakdown."""
    conn = get_connection()

    per_registry = {
        r["registry"]: r["n"]
        for r in conn.execute(
            "SELECT registry, COUNT(*) AS n FROM registry_source_records "
            "GROUP BY registry ORDER BY n DESC"
        ).fetchall()
    }

    # A trial is "cross-registered" if it has > 1 entry in registry_source_records.
    cross_registered = conn.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT trial_id FROM registry_source_records
            GROUP BY trial_id HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]

    # Trials with an NCT cross-reference recorded in an EU registry row.
    eu_with_nct = conn.execute(
        """
        SELECT COUNT(*) FROM trials
        WHERE (euct_id IS NOT NULL OR eudract_number IS NOT NULL)
          AND id LIKE 'NCT%'
        """
    ).fetchone()[0]

    conn.close()

    return {
        "per_registry": per_registry,
        "cross_registered_trials": cross_registered,
        "eu_trials_with_nct_xref": eu_with_nct,
    }


@app.get("/grants/stats")
def get_grants_stats():
    conn = get_connection()
    total_grants = conn.execute("SELECT COUNT(*) FROM grants").fetchone()[0]
    active_grants = conn.execute("SELECT COUNT(*) FROM grants WHERE status = 'ACTIVE'").fetchone()[0]
    grants_with_links = conn.execute("SELECT COUNT(*) FROM grants WHERE has_trial_link = 1").fetchone()[0]
    total_funding = conn.execute(
        "SELECT SUM(amount_usd) FROM grants WHERE amount_usd IS NOT NULL"
    ).fetchone()[0] or 0
    active_funding = conn.execute(
        "SELECT SUM(amount_usd) FROM grants WHERE status = 'ACTIVE' AND amount_usd IS NOT NULL"
    ).fetchone()[0] or 0
    by_source = {
        r["source"]: r["n"]
        for r in conn.execute("SELECT source, COUNT(*) AS n FROM grants GROUP BY source").fetchall()
    }
    by_area = {
        r["therapeutic_area"] or "Other": r["n"]
        for r in conn.execute(
            "SELECT therapeutic_area, COUNT(*) AS n FROM grants GROUP BY therapeutic_area"
        ).fetchall()
    }
    by_country = {
        r["country"]: r["n"]
        for r in conn.execute(
            "SELECT country, COUNT(*) AS n FROM grants "
            "WHERE country IS NOT NULL AND country != '' "
            "GROUP BY country ORDER BY n DESC LIMIT 30"
        ).fetchall()
    }
    conn.close()
    return {
        "total_grants": total_grants,
        "active_grants": active_grants,
        "grants_with_trial_links": grants_with_links,
        "total_funding_usd": total_funding,
        "active_funding_usd": active_funding,
        "by_source": by_source,
        "by_therapeutic_area": by_area,
        "by_country": by_country,
    }


@app.get("/grants/filter-options")
def get_grants_filter_options():
    conn = get_connection()
    activity_codes = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT activity_code FROM grants "
            "WHERE activity_code IS NOT NULL ORDER BY activity_code"
        ).fetchall()
    ]
    org_types = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT org_type FROM grants "
            "WHERE org_type IS NOT NULL ORDER BY org_type"
        ).fetchall()
    ]
    research_types = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT research_type FROM grants "
            "WHERE research_type IS NOT NULL ORDER BY research_type"
        ).fetchall()
    ]
    agency_divisions = [
        r[0] for r in conn.execute(
            "SELECT agency_division, COUNT(*) AS n FROM grants "
            "WHERE agency_division IS NOT NULL "
            "GROUP BY agency_division ORDER BY n DESC LIMIT 20"
        ).fetchall()
    ]
    conn.close()
    return {
        "activity_codes": activity_codes,
        "org_types": org_types,
        "research_types": research_types,
        "agency_divisions": agency_divisions,
    }


def _grants_where(q, source, therapeutic_area, status, country, country_q,
                  country_q_not, has_trial_link, min_amount, max_amount,
                  activity_code, org_type, research_type, agency_division,
                  fiscal_year_min, fiscal_year_max, award_date_from, award_date_to):
    """Build the shared WHERE clause for the grants list + export endpoints."""
    where_clauses, params = [], []

    if q:
        where_clauses.append(
            "(LOWER(title) LIKE ? OR LOWER(abstract) LIKE ? "
            "OR LOWER(organization) LIKE ? OR LOWER(pi_name) LIKE ?)"
        )
        q_like = f"%{q.lower()}%"
        params.extend([q_like, q_like, q_like, q_like])

    for col, vals in (
        ("source", source), ("therapeutic_area", therapeutic_area),
        ("status", status), ("country", country), ("activity_code", activity_code),
        ("org_type", org_type), ("research_type", research_type),
        ("agency_division", agency_division),
    ):
        if vals:
            placeholders = ",".join("?" * len(vals))
            where_clauses.append(f"{col} IN ({placeholders})")
            params.extend(vals)

    if has_trial_link is not None:
        where_clauses.append("has_trial_link = ?")
        params.append(1 if has_trial_link else 0)
    if min_amount is not None:
        where_clauses.append("amount_usd >= ?")
        params.append(min_amount)
    if max_amount is not None:
        where_clauses.append("amount_usd <= ?")
        params.append(max_amount)
    if fiscal_year_min is not None:
        where_clauses.append("fiscal_year >= ?")
        params.append(fiscal_year_min)
    if fiscal_year_max is not None:
        where_clauses.append("fiscal_year <= ?")
        params.append(fiscal_year_max)
    if country_q:
        where_clauses.append("LOWER(country) LIKE ? ESCAPE '\\'")
        params.append(_like_pattern(country_q))
    if country_q_not:
        where_clauses.append("(country IS NULL OR LOWER(country) NOT LIKE ? ESCAPE '\\')")
        params.append(_like_pattern(country_q_not))
    # award_date holds a mix of bare YYYY-MM-DD and full ISO timestamps; bare-
    # date string bounds order correctly against both (see _iso_day), unlike
    # the old DATE(award_date) wrapper which forced a per-row function call and
    # made the predicate unindexable.
    if award_date_from:
        where_clauses.append("award_date >= ?")
        params.append(_iso_day(award_date_from))
    if award_date_to:
        # Inclusive "to" day = exclusive next-day bound, so same-day timestamps
        # ("...T23:59:59") still match. > '' keeps NULL/empty excluded, which
        # the NULL-propagating DATE() comparison used to do implicitly.
        where_clauses.append("award_date > '' AND award_date < ?")
        params.append(_iso_day(award_date_to, plus_days=1))

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
    return where_sql, params


def _grants_order_by(sort, sort_dir):
    """ORDER BY clause for grants. aicure_fit is a real (precomputed) column, so
    the default fit ranking sorts in SQL like any other; unknown columns fall
    back to it."""
    return _order_by_clause(sort, sort_dir,
                            GRANT_SORTABLE_COLUMNS | {"aicure_fit"},
                            "aicure_fit", "ingested_at DESC")


@app.get("/grants")
def get_grants(
    q: Optional[str] = None,
    source: Optional[List[str]] = Query(default=None),
    therapeutic_area: Optional[List[str]] = Query(default=None),
    status: Optional[List[str]] = Query(default=None),
    country: Optional[List[str]] = Query(default=None),
    country_q: Optional[str] = None,
    country_q_not: Optional[str] = None,
    has_trial_link: Optional[bool] = None,
    min_amount: Optional[int] = None,
    max_amount: Optional[int] = None,
    activity_code: Optional[List[str]] = Query(default=None),
    org_type: Optional[List[str]] = Query(default=None),
    research_type: Optional[List[str]] = Query(default=None),
    agency_division: Optional[List[str]] = Query(default=None),
    fiscal_year_min: Optional[int] = None,
    fiscal_year_max: Optional[int] = None,
    award_date_from: Optional[str] = None,
    award_date_to: Optional[str] = None,
    sort: Optional[str] = "aicure_fit",
    sort_dir: str = Query("desc", alias="dir"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
):
    # Build the WHERE first: _iso_day() can raise 422 on a bad date param, and
    # doing it before opening the connection avoids leaking one on that path.
    where_sql, params = _grants_where(
        q, source, therapeutic_area, status, country, country_q, country_q_not,
        has_trial_link, min_amount, max_amount, activity_code, org_type,
        research_type, agency_division, fiscal_year_min, fiscal_year_max,
        award_date_from, award_date_to)
    conn = get_connection()

    # One pass for both header aggregates instead of two scans of the same
    # filtered set.
    total, total_funding = conn.execute(
        f"SELECT COUNT(*), COALESCE(SUM(amount_usd), 0) FROM grants {where_sql}",
        params,
    ).fetchone()
    offset = (page - 1) * page_size

    # aicure_fit is precomputed (score_backfill.py) into a real column, so the
    # default fit ranking paginates server-side like any other sort.
    rows = conn.execute(
        f"SELECT {_GRANT_GRID_COLS} FROM grants {where_sql} "
        f"{_grants_order_by(sort, sort_dir)} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    results = [row_to_dict(r) for r in rows]
    # Fallback for any row not yet backfilled (e.g. just uploaded). The scorer
    # reads abstract, which the grid SELECT omits — re-fetch for those rows.
    unscored = [g["id"] for g in results if g.get("aicure_fit") is None]
    if unscored:
        placeholders = ",".join("?" * len(unscored))
        abstracts = dict(conn.execute(
            f"SELECT id, abstract FROM grants WHERE id IN ({placeholders})",
            unscored,
        ).fetchall())
        for g in results:
            if g.get("aicure_fit") is None:
                g["aicure_fit"] = score_grant({**g, "abstract": abstracts.get(g["id"])})
    conn.close()
    return {"total": total, "total_funding": total_funding, "page": page, "results": results}


# (field, CSV header) — mirrors the Funding grid, score first.
_GRANT_EXPORT_COLUMNS = [
    ("aicure_fit", "Fit"), ("source", "Source"), ("therapeutic_area", "Area"),
    ("title", "Grant Title"), ("status", "Status"), ("sponsor_funder", "Funder"),
    ("agency_division", "Division / Programme"), ("activity_code", "Award Type"),
    ("organization", "Recipient"), ("org_type", "Org Type"), ("pi_name", "PI"),
    ("pi_email", "PI Email"), ("amount_usd", "Amount (USD)"), ("currency", "Currency"),
    ("amount_original", "Original Amount"), ("country", "Country"),
    ("award_date", "Awarded"), ("start_date", "Start"), ("end_date", "End"),
    ("fiscal_year", "Fiscal Year"), ("linked_trial_id", "Linked Trial"),
    ("award_id", "Award ID"), ("source_url", "Source URL"),
]


def _csv_safe(value):
    """Neutralize spreadsheet formula injection. Grant fields come from external
    feeds, so a cell beginning with = + - @ (or a leading tab/CR) could execute
    as a formula in Excel/Sheets — prefix those with a single quote."""
    if isinstance(value, str) and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def _csv_stream(name, columns, row_query, params, postprocess=None):
    """Stream `row_query` results as a CSV download. `columns` is a list of
    (row field, CSV header) pairs; `postprocess` may mutate each row dict
    before it is written (e.g. on-the-fly scoring)."""
    def rows_iter():
        # check_same_thread=False: Starlette iterates this generator across
        # anyio worker threads, so the connection may be created on one thread
        # and used on another. Only one thread touches it at a time here.
        conn = get_connection(check_same_thread=False)
        try:
            buf = io.StringIO()
            writer = csv.writer(buf)

            def flush():
                data = buf.getvalue()
                buf.seek(0); buf.truncate(0)
                return data

            writer.writerow([h for _, h in columns])
            yield flush()
            for r in conn.execute(row_query, params):
                d = row_to_dict(r)
                if postprocess:
                    postprocess(d)
                writer.writerow([_csv_safe(d.get(field)) for field, _ in columns])
                yield flush()
        finally:
            conn.close()

    filename = f"{name}_export_{datetime.utcnow():%Y%m%d}.csv"
    return StreamingResponse(
        rows_iter(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Declared before /grants/{grant_id} so "export" isn't captured as a grant id.
@app.get("/grants/export")
def export_grants(
    q: Optional[str] = None,
    source: Optional[List[str]] = Query(default=None),
    therapeutic_area: Optional[List[str]] = Query(default=None),
    status: Optional[List[str]] = Query(default=None),
    country: Optional[List[str]] = Query(default=None),
    country_q: Optional[str] = None,
    country_q_not: Optional[str] = None,
    has_trial_link: Optional[bool] = None,
    min_amount: Optional[int] = None,
    max_amount: Optional[int] = None,
    activity_code: Optional[List[str]] = Query(default=None),
    org_type: Optional[List[str]] = Query(default=None),
    research_type: Optional[List[str]] = Query(default=None),
    agency_division: Optional[List[str]] = Query(default=None),
    fiscal_year_min: Optional[int] = None,
    fiscal_year_max: Optional[int] = None,
    award_date_from: Optional[str] = None,
    award_date_to: Optional[str] = None,
    sort: Optional[str] = "aicure_fit",
    sort_dir: str = Query("desc", alias="dir"),
):
    """Stream the FULL filtered grant set as CSV (honors the grid's filters +
    sort). Unlike the client-side export, this covers every matching row, not
    just the pages currently loaded into the infinite-scroll grid."""
    where_sql, params = _grants_where(
        q, source, therapeutic_area, status, country, country_q, country_q_not,
        has_trial_link, min_amount, max_amount, activity_code, org_type,
        research_type, agency_division, fiscal_year_min, fiscal_year_max,
        award_date_from, award_date_to)

    def postprocess(g):
        if g.get("aicure_fit") is None:
            g["aicure_fit"] = score_grant(g)

    return _csv_stream(
        "grants", _GRANT_EXPORT_COLUMNS,
        f"SELECT * FROM grants {where_sql} {_grants_order_by(sort, sort_dir)}",
        params, postprocess)


@app.get("/grants/{grant_id}/trials")
def get_grant_trials(grant_id: str):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT t.*, gtl.match_method
        FROM trials t
        JOIN grant_trial_links gtl ON t.id = gtl.trial_id
        WHERE gtl.grant_id = ?
        ORDER BY gtl.match_method
        """,
        (grant_id,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@app.get("/grants/{grant_id}")
def get_grant(grant_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM grants WHERE id = ?", (grant_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Grant not found")
    return row_to_dict(row)


@app.post("/admin/refresh-news")
def admin_refresh_news(x_admin_key: str = Header(default="")):
    """Manually trigger a news refresh + cleanup. Protected by X-Admin-Key header."""
    _require_admin(x_admin_key)
    # Hold the lock for the whole job, not just this check: releasing it here made
    # the 409 "already in progress" guard decorative (a second call would acquire
    # the just-freed lock and start a concurrent refresh). The worker releases it
    # in finally.
    if not _news_refresh_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="Refresh already in progress")

    def _refresh_job():
        # run_daily_news re-raises on refresh failure ON PURPOSE; routing it
        # through a daemon thread would otherwise re-hide that. The endpoint
        # already returned {"started"}, so log the traceback or the operator who
        # triggered the manual refresh never learns it failed.
        try:
            run_daily_news()
        except Exception:
            print("[admin] background news refresh FAILED:")
            traceback.print_exc()
        finally:
            _news_refresh_lock.release()

    try:
        thread = threading.Thread(target=_refresh_job, daemon=True)
        thread.start()
    except Exception:
        _news_refresh_lock.release()  # never leak the lock if the thread won't start
        raise
    return {"status": "started", "message": "News refresh running in background"}


@app.post("/admin/send-news-digest")
def admin_send_news_digest(refresh: bool = True, x_admin_key: str = Header(default="")):
    """Refresh news (unless refresh=false) then build + SEND the daily news
    digest. Protected by X-Admin-Key. Meant to be hit by an external daily cron
    so delivery works even while the free-tier Render service is asleep (the
    request wakes it). Runs synchronously and returns the emailer result so the
    caller gets a real status (sent / skipped-empty / error)."""
    _require_admin(x_admin_key)
    try:
        detail = run_daily_news_and_send(refresh=refresh)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"digest send failed: {e}")
    return {"status": "ok", "detail": detail}


@app.post("/admin/send-weekly-digest")
def admin_send_weekly_digest(x_admin_key: str = Header(default="")):
    """Build + SEND the weekly trials and grants digests (two emails) from the
    CURRENT DB. Read-only keyword scoring, no ingest — so it's light enough to
    run on free-tier Render. Driven by the weekly GitHub Actions cron. Note:
    content freshness is bounded by how recently the DB was ingested/deployed;
    this endpoint does not scrape (see the ingest discussion in the PR)."""
    _require_admin(x_admin_key)
    import emailer
    try:
        trials = emailer.send_weekly_trials_digest()
        grants = emailer.send_weekly_grants_digest()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"weekly digest send failed: {e}")
    return {"status": "ok", "trials": trials, "grants": grants}


@app.post("/admin/prune-old")
def admin_prune_old(
    background_tasks: BackgroundTasks,
    dry_run: bool = True,
    cutoff_days: int = 365,
    x_admin_key: str = Header(default=""),
):
    """Remove trials/grants with primary_completion/end_date older than cutoff_days.

    Defaults to dry_run=True; pass dry_run=false to actually delete.
    """
    _require_admin(x_admin_key)
    from prune_old import prune_old
    if dry_run:
        trial_count, grant_count = prune_old(dry_run=True, cutoff_days=cutoff_days)
        return {"trials_pruned": trial_count, "grants_pruned": grant_count, "dry_run": True}
    def _prune_job():
        # Destructive delete in the background: the caller already got
        # {"started"}, so log the outcome (and any failure's traceback) — there's
        # otherwise no durable record that the prune ran, partially ran, or died.
        try:
            t, g = prune_old(dry_run=False, cutoff_days=cutoff_days)
            print(f"[admin] background prune done: {t} trials, {g} grants removed (cutoff_days={cutoff_days})")
        except Exception:
            print("[admin] background prune FAILED:")
            traceback.print_exc()

    background_tasks.add_task(_prune_job)
    return {"status": "started", "message": "Prune running in background", "dry_run": False}


# Serve the built React SPA from /frontend/dist for single-service deploys
# (e.g. Render). Mounted last so API routes take precedence. The directory
# only exists after `npm run build`, so guard against missing dir in dev.
_FRONTEND_DIST = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "dist"
)
if os.path.isdir(_FRONTEND_DIST):
    app.mount("/", StaticFiles(directory=_FRONTEND_DIST, html=True), name="frontend")
