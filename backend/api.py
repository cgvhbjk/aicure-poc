import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional, List

from db import get_connection

app = FastAPI(title="AiCure POC API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def row_to_dict(row):
    return dict(row)


@app.get("/trials")
def get_trials(
    q: Optional[str] = None,
    status: Optional[List[str]] = Query(default=None),
    phase: Optional[List[str]] = Query(default=None),
    therapeutic_area: Optional[List[str]] = Query(default=None),
    country: Optional[str] = None,
    has_news: Optional[bool] = None,
    page: int = 1,
    page_size: int = 100,
):
    page_size = min(page_size, 500)
    conn = get_connection()

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
        where_clauses.append("lead_country = ?")
        params.append(country)

    if has_news is not None:
        where_clauses.append("has_news = ?")
        params.append(1 if has_news else 0)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    total = conn.execute(f"SELECT COUNT(*) FROM trials {where_sql}", params).fetchone()[0]
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"SELECT * FROM trials {where_sql} ORDER BY last_updated DESC LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    conn.close()
    return {"total": total, "page": page, "results": [row_to_dict(r) for r in rows]}


@app.get("/news")
def get_news(
    q: Optional[str] = None,
    source: Optional[List[str]] = Query(default=None),
    linked_only: Optional[bool] = None,
    is_trial_announcement: Optional[bool] = None,
    page: int = 1,
    page_size: int = 100,
):
    page_size = min(page_size, 500)
    conn = get_connection()

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

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) FROM news_items ni {where_sql}", params
    ).fetchone()[0]

    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT ni.*,
               (SELECT match_method FROM trial_news_links
                WHERE news_id = ni.id
                ORDER BY match_method DESC LIMIT 1) AS match_method
        FROM news_items ni
        {where_sql}
        ORDER BY ni.published_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()

    conn.close()
    return {"total": total, "results": [row_to_dict(r) for r in rows]}


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


@app.get("/stats")
def get_stats():
    conn = get_connection()

    total_trials = conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0]
    trials_with_news = conn.execute("SELECT COUNT(*) FROM trials WHERE has_news = 1").fetchone()[0]
    total_news = conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]
    unlinked_news = conn.execute("SELECT COUNT(*) FROM news_items WHERE trial_id IS NULL").fetchone()[0]

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

    last_ingested = conn.execute("SELECT MAX(ingested_at) FROM trials").fetchone()[0]
    conn.close()

    return {
        "total_trials": total_trials,
        "trials_with_news": trials_with_news,
        "total_news": total_news,
        "unlinked_news": unlinked_news,
        "by_status": by_status,
        "by_phase": by_phase,
        "by_therapeutic_area": by_therapeutic_area,
        "last_ingested": last_ingested,
    }
