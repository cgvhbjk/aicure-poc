import json
import re
from datetime import datetime

from db import get_connection

NCT_RE = re.compile(r"^NCT\d{8}$")

_PREFIX_TO_ID_COL = {
    "EUCT":   "euct_id",
    "ISRCTN": "isrctn_id",
    "NTR":    "ntr_id",
    "ANZCTR": "anzctr_id",
    "DRKS":   "drks_id",
    "JRCT":   "jrct_id",
    "CRIS":   "cris_id",
}


def _is_nct(trial_id: str) -> bool:
    return bool(NCT_RE.match(trial_id or ""))


def _id_col_for(trial_id: str):
    for prefix, col in _PREFIX_TO_ID_COL.items():
        if trial_id.startswith(f"{prefix}-"):
            return col, trial_id.split("-", 1)[1]
    return None, None


def _jaccard_tokens(a: str, b: str) -> float:
    ta = set(re.sub(r"[^\w]", " ", a.lower()).split())
    tb = set(re.sub(r"[^\w]", " ", b.lower()).split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _parse_date(s: str):
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(s[:len(fmt) + fmt.count("-")], fmt)
        except ValueError:
            continue
    return None


def _score_pair(a: dict, b: dict) -> tuple:
    score = 0.0
    match_fields = []
    match_scores = {}

    # Sponsor — weight 0.30
    if a.get("sponsor") and b.get("sponsor"):
        s = _jaccard_tokens(a["sponsor"], b["sponsor"])
        match_scores["sponsor"] = round(s, 3)
        score += 0.30 * s
        if s >= 0.7:
            match_fields.append("sponsor")

    # Start date — weight 0.20 (within 90 days)
    if a.get("start_date") and b.get("start_date"):
        da, db_ = _parse_date(a["start_date"]), _parse_date(b["start_date"])
        if da and db_:
            diff = abs((da - db_).days)
            s = max(0.0, 1.0 - diff / 90.0)
            match_scores["start_date"] = round(s, 3)
            score += 0.20 * s
            if s >= 0.7:
                match_fields.append("start_date")

    # Enrollment — weight 0.15 (within 10%)
    try:
        ea = int(a["enrollment"]) if a.get("enrollment") else None
        eb = int(b["enrollment"]) if b.get("enrollment") else None
        if ea and eb and ea > 0 and eb > 0:
            ratio = min(ea, eb) / max(ea, eb)
            s = ratio if abs(ea - eb) / max(ea, eb) <= 0.10 else ratio * 0.5
            match_scores["enrollment"] = round(s, 3)
            score += 0.15 * s
            if s >= 0.9:
                match_fields.append("enrollment")
    except (TypeError, ValueError):
        pass

    # Conditions Jaccard — weight 0.20
    try:
        ca = set(json.loads(a["conditions"] or "[]")) if a.get("conditions") else set()
        cb = set(json.loads(b["conditions"] or "[]")) if b.get("conditions") else set()
        if ca and cb:
            s = len(ca & cb) / len(ca | cb)
            match_scores["conditions"] = round(s, 3)
            score += 0.20 * s
            if s >= 0.5:
                match_fields.append("conditions")
    except (json.JSONDecodeError, TypeError):
        pass

    # Phase — weight 0.15
    if a.get("phase") and b.get("phase"):
        s = 1.0 if a["phase"] == b["phase"] else 0.0
        match_scores["phase"] = s
        score += 0.15 * s
        if s == 1.0:
            match_fields.append("phase")

    return round(score, 4), match_fields, match_scores


def _candidate_exists(conn, a_id: str, b_id: str, entity_type: str) -> bool:
    return bool(conn.execute(
        """SELECT 1 FROM merge_candidates
           WHERE entity_type = ?
           AND ((record_a_id = ? AND record_b_id = ?) OR (record_a_id = ? AND record_b_id = ?))""",
        (entity_type, a_id, b_id, b_id, a_id),
    ).fetchone())


def _auto_merge(conn, a: dict, b: dict, score: float, match_fields: list, match_scores: dict):
    """Merge b into a (a is the canonical NCT record)."""
    a_id, b_id = a["id"], b["id"]

    # Update canonical's registry tracking
    a_sources = json.loads(a.get("registry_sources") or '["ClinicalTrials.gov"]')
    a_ids = json.loads(a.get("all_registry_ids") or "[]")
    b_sources = json.loads(b.get("registry_sources") or "[]")
    b_ids = json.loads(b.get("all_registry_ids") or "[]")
    for src in b_sources:
        if src not in a_sources:
            a_sources.append(src)
    for rid in b_ids + [b_id]:
        if rid not in a_ids:
            a_ids.append(rid)

    # Set the appropriate id column on the canonical row
    id_col, reg_val = _id_col_for(b_id)
    id_col_sql = f", {id_col} = ?" if id_col else ""
    id_col_params = [reg_val] if id_col else []

    conn.execute(
        f"UPDATE trials SET registry_sources = ?, all_registry_ids = ?{id_col_sql} WHERE id = ?",
        [json.dumps(a_sources), json.dumps(a_ids)] + id_col_params + [a_id],
    )

    # Reassign foreign keys
    conn.execute("UPDATE registry_source_records SET trial_id = ? WHERE trial_id = ?", (a_id, b_id))
    conn.execute("""
        INSERT OR IGNORE INTO trial_org_links (trial_id, org_id, role)
        SELECT ?, org_id, role FROM trial_org_links WHERE trial_id = ?
    """, (a_id, b_id))
    conn.execute("DELETE FROM trial_org_links WHERE trial_id = ?", (b_id,))
    conn.execute("""
        INSERT OR IGNORE INTO trial_news_links (trial_id, news_id, match_method)
        SELECT ?, news_id, match_method FROM trial_news_links WHERE trial_id = ?
    """, (a_id, b_id))
    conn.execute("DELETE FROM trial_news_links WHERE trial_id = ?", (b_id,))
    conn.execute("DELETE FROM trials WHERE id = ?", (b_id,))

    # Record in merge_candidates
    conn.execute(
        """INSERT OR IGNORE INTO merge_candidates
           (entity_type, record_a_id, record_b_id, confidence, match_fields, match_scores,
            status, merged_into, created_at)
           VALUES ('trials', ?, ?, ?, ?, ?, 'CONFIRMED_MERGE', ?, ?)""",
        (a_id, b_id, score, json.dumps(match_fields), json.dumps(match_scores),
         a_id, datetime.utcnow().isoformat()),
    )


def detect_trial_duplicates() -> int:
    conn = get_connection()
    rows = conn.execute(
        """SELECT id, title_brief, sponsor, start_date, enrollment, conditions, phase,
                  therapeutic_area, registry_sources, all_registry_ids
           FROM trials
           WHERE status NOT IN ('COMPLETED', 'TERMINATED')
             AND status IS NOT NULL"""
    ).fetchall()
    trials = [dict(r) for r in rows]

    # Group by therapeutic_area + phase
    groups: dict = {}
    for t in trials:
        key = (t.get("therapeutic_area") or "", t.get("phase") or "")
        groups.setdefault(key, []).append(t)

    now = datetime.utcnow().isoformat()
    pending = 0
    auto_merged = 0

    for group_trials in groups.values():
        nct = [t for t in group_trials if _is_nct(t["id"])]
        others = [t for t in group_trials if not _is_nct(t["id"])]
        if not nct or not others:
            continue

        for a in nct:
            for b in others:
                if _candidate_exists(conn, a["id"], b["id"], "trials"):
                    continue

                score, match_fields, match_scores = _score_pair(a, b)
                if score < 0.6:
                    continue

                if score > 0.85:
                    _auto_merge(conn, a, b, score, match_fields, match_scores)
                    auto_merged += 1
                else:
                    conn.execute(
                        """INSERT INTO merge_candidates
                           (entity_type, record_a_id, record_b_id, confidence, match_fields,
                            match_scores, status, created_at)
                           VALUES ('trials', ?, ?, ?, ?, ?, 'PENDING', ?)""",
                        (a["id"], b["id"], score, json.dumps(match_fields),
                         json.dumps(match_scores), now),
                    )
                    pending += 1

    conn.commit()
    conn.close()
    print(f"  Trial duplicates: {pending} pending, {auto_merged} auto-merged")
    return pending


def detect_org_duplicates() -> int:
    conn = get_connection()
    orgs = [dict(r) for r in conn.execute("SELECT id, canonical_name FROM organizations").fetchall()]

    now = datetime.utcnow().isoformat()
    pending = 0

    for i, a in enumerate(orgs):
        for b in orgs[i + 1:]:
            sim = _jaccard_tokens(a["canonical_name"], b["canonical_name"])
            if sim <= 0.7:
                continue
            if _candidate_exists(conn, a["id"], b["id"], "organizations"):
                continue
            conn.execute(
                """INSERT INTO merge_candidates
                   (entity_type, record_a_id, record_b_id, confidence, match_fields,
                    match_scores, status, created_at)
                   VALUES ('organizations', ?, ?, ?, ?, ?, 'PENDING', ?)""",
                (a["id"], b["id"], round(sim, 4),
                 json.dumps(["canonical_name"]), json.dumps({"canonical_name": round(sim, 4)}),
                 now),
            )
            pending += 1

    conn.commit()
    conn.close()
    print(f"  Org duplicates: {pending} pending")
    return pending


def run_merge_detection():
    detect_trial_duplicates()
    detect_org_duplicates()
    conn = get_connection()
    total_pending = conn.execute(
        "SELECT COUNT(*) FROM merge_candidates WHERE status = 'PENDING'"
    ).fetchone()[0]
    conn.close()
    print(f"  Merge detection: {total_pending} total candidates pending review")
