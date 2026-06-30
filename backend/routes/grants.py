"""Grants routes — split out of api.py.

Shared helpers/models/query-builders/jobs live in the dependency-free
routes/_shared module; this module imports them (`from routes._shared import *`)
so the moved handler bodies resolve those bare names. No api<->routes cycle.
"""
from fastapi import APIRouter
from routes._shared import *  # noqa: F401,F403 (shared helpers/models + framework re-exports)

router = APIRouter()


@router.get("/grants/stats")
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


@router.get("/grants/filter-options")
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


@router.get("/grants")
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


@router.get("/grants/export")
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


@router.get("/grants/{grant_id}/trials")
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


@router.get("/grants/{grant_id}")
def get_grant(grant_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM grants WHERE id = ?", (grant_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Grant not found")
    return row_to_dict(row)
