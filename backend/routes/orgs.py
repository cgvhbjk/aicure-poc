"""Orgs routes — split out of api.py.

Shared helpers/models/query-builders/jobs live in the dependency-free
routes/_shared module; this module imports them (explicitly)
so the moved handler bodies resolve those bare names. No api<->routes cycle.
"""
from fastapi import APIRouter
from routes._shared import ( ContactCreate, HTTPException, Header, List,
    ORG_SORTABLE_COLUMNS, Optional, OrgUpdate, Query, Request, _PATCHABLE_ORG_FIELDS,
    _naive_utcnow, _order_by_clause, _require_enrich_auth, get_connection, row_to_dict)

router = APIRouter()


@router.get("/orgs")
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


@router.get("/orgs/{org_id}")
def get_org(org_id: str):
    conn = get_connection()
    row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Organization not found")
    return row_to_dict(row)


@router.get("/orgs/{org_id}/trials")
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


@router.get("/orgs/{org_id}/contacts")
def get_org_contacts(org_id: str):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM org_contacts WHERE org_id = ? ORDER BY is_decision_maker DESC, full_name",
        (org_id,),
    ).fetchall()
    conn.close()
    return [row_to_dict(r) for r in rows]


@router.post("/orgs/{org_id}/contacts")
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
            body.notes, _naive_utcnow().isoformat(),
        ),
    )
    new_id = cur.lastrowid
    conn.commit()
    row = conn.execute("SELECT * FROM org_contacts WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    return row_to_dict(row)


@router.post("/orgs/{org_id}/enrich-contacts")
def enrich_org_contacts_route(org_id: str, request: Request,
                              force_refresh: bool = False,
                              x_admin_key: str = Header(default="")):
    """Enrich an org's contacts with CMO / clinical decision-makers via Seamless.AI
    (§7). Authorized by the app-password session or the service admin key (it can
    spend Seamless credits). Served from the credit cache when possible; returns
    api_calls=0 when no credits were spent. No-ops cleanly when SEAMLESS_API_KEY
    is unset."""
    _require_enrich_auth(request, x_admin_key)
    from seamless import enrich_org_contacts
    result = enrich_org_contacts(org_id, force_refresh=force_refresh)
    if not result.get("ok") and result.get("error") == "organization not found":
        raise HTTPException(status_code=404, detail="Organization not found")
    # Return the refreshed contact list alongside the enrichment status.
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM org_contacts WHERE org_id = ? ORDER BY is_decision_maker DESC, full_name",
            (org_id,),
        ).fetchall()
    finally:
        conn.close()
    return {"status": result, "contacts": [row_to_dict(r) for r in rows]}


@router.patch("/orgs/{org_id}")
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
    # A manual org_type edit pins the classification (org_type_locked=1) so the
    # next ingest's auto-reclassification won't revert it.
    if "org_type" in updates:
        set_clauses += ", org_type_locked = 1"
    conn.execute(
        f"UPDATE organizations SET {set_clauses} WHERE id = ?",
        list(updates.values()) + [org_id],
    )
    conn.commit()
    row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    conn.close()
    return row_to_dict(row)


@router.get("/relationships")
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
