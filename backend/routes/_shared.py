"""Shared helpers, models, query-builders, and scheduler jobs for the HTTP layer.

Extracted from api.py so the route modules (routes/*.py) AND api.py import these
ONE-WAY — dissolving the prior api<->routes import cycle and the
globals().update(vars(api)) namespace snapshot. This module imports only db,
scoring, and framework/stdlib (never api or routes.*), so it is a clean leaf.
`from routes._shared import *` re-exports the whole surface (see __all__ below).
"""
import os
import re
import csv
import io
import json
import base64
import hmac
import threading
from datetime import datetime, timedelta, timezone

from fastapi import (Query, HTTPException, Header, UploadFile, File, Form,  # noqa: F401
                     BackgroundTasks, Request, Response)
from fastapi.responses import StreamingResponse  # noqa: F401
from typing import Optional, List  # noqa: F401
from pydantic import BaseModel

from db import get_connection, DB_PATH, request_connection_scope  # noqa: F401
from scoring import score_grant, score_trial  # noqa: F401


def _naive_utcnow() -> datetime:
    """Naive UTC now. _naive_utcnow() is deprecated/removal-tracked; this is
    the drop-in naive-UTC equivalent used across the HTTP layer."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _require_enrich_auth(request: Request, x_admin_key: str):
    """Authorize the (credit-spending) enrichment route.

    Accepts either the service admin key (the cron/automation credential) OR a
    valid app-password session — the same HTTP Basic auth the _app_auth
    middleware already enforces app-wide. This is why the SPA does NOT embed an
    admin key in its public bundle (VITE_* vars ship to every browser): the
    browser's cached Basic-auth header authorizes the call automatically,
    same-origin. When neither gate is configured (local dev / tests) the app is
    already open, so allow."""
    if _ADMIN_KEY and hmac.compare_digest(x_admin_key, _ADMIN_KEY):
        return
    if _APP_PASSWORD and _valid_basic_auth(request.headers.get("Authorization", "")):
        return
    if not _APP_PASSWORD and not _ADMIN_KEY:
        return  # nothing configured to gate against (matches the app-open posture)
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
    cutoff = (_naive_utcnow() - timedelta(days=7)).isoformat()
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
        print(f"[daily-news] Starting at {_naive_utcnow().isoformat()}")
        parse_all_feeds()
        run_linker()
        cleanup_old_news()
        print(f"[daily-news] Done at {_naive_utcnow().isoformat()}")
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


















class MergeConfirm(BaseModel):
    reviewed_by: Optional[str] = ""
    surviving_id: Optional[str] = None


class MergeReview(BaseModel):
    reviewed_by: Optional[str] = ""


_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB






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

    filename = f"{name}_export_{_naive_utcnow():%Y%m%d}.csv"
    return StreamingResponse(
        rows_iter(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# Declared before /grants/{grant_id} so "export" isn't captured as a grant id.
















# Re-export the ENTIRE shared surface (incl. the framework/stdlib names above)
# so `from routes._shared import *` gives route modules + api.py everything their
# handlers reference — no per-module import list, no namespace snapshot, no cycle.
__all__ = [_name for _name in list(globals()) if not _name.startswith('__')]
