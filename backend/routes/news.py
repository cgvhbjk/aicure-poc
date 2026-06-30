"""News routes — split out of api.py.

Shared helpers/models/constants stay in api.py; we copy its module globals so
the moved handler bodies resolve bare names (row_to_dict, get_connection,
_trials_where, OrgUpdate, …) exactly as before — no fragile per-name import list.
"""
from fastapi import APIRouter
from routes._shared import *  # noqa: F401,F403 (shared helpers/models + framework re-exports)

router = APIRouter()


@router.get("/news")
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


@router.get("/news/export")
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
