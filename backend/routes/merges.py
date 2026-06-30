"""Merges routes — split out of api.py.

Shared helpers/models/constants stay in api.py; we copy its module globals so
the moved handler bodies resolve bare names (row_to_dict, get_connection,
_trials_where, OrgUpdate, …) exactly as before — no fragile per-name import list.
"""
from fastapi import APIRouter
from routes._shared import *  # noqa: F401,F403 (shared helpers/models + framework re-exports)

router = APIRouter()


@router.get("/merges")
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


@router.post("/merges/{merge_id}/confirm")
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


@router.post("/merges/{merge_id}/undo")
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


@router.post("/merges/{merge_id}/reject")
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


@router.post("/merges/{merge_id}/snooze")
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


@router.get("/merges/stats")
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
