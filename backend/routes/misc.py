"""Misc routes — split out of api.py.

Shared helpers/models/query-builders/jobs live in the dependency-free
routes/_shared module; this module imports them (`from routes._shared import *`)
so the moved handler bodies resolve those bare names. No api<->routes cycle.
"""
from fastapi import APIRouter
from routes._shared import *  # noqa: F401,F403 (shared helpers/models + framework re-exports)

router = APIRouter()


@router.post("/upload")
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

    ts = _naive_utcnow().strftime("%Y%m%dT%H%M%S")
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
         _naive_utcnow().isoformat(), analyst_name, notes, save_path),
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


@router.get("/stats")
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


@router.get("/registries/stats")
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
