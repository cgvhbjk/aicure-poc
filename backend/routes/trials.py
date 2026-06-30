"""Trials routes — split out of api.py.

Shared helpers/models/query-builders/jobs live in the dependency-free
routes/_shared module; this module imports them (`from routes._shared import *`)
so the moved handler bodies resolve those bare names. No api<->routes cycle.
"""
from fastapi import APIRouter
from routes._shared import *  # noqa: F401,F403 (shared helpers/models + framework re-exports)

router = APIRouter()


@router.get("/trials")
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


@router.get("/trials/export")
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


@router.get("/trials/{trial_id}")
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


@router.get("/trials/{trial_id}/registries")
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


@router.get("/trials/{nct_id}/news")
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
