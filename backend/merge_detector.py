import json
import re
from datetime import datetime

from db import get_connection

NCT_RE = re.compile(r"^NCT\d{8}$")

_PREFIX_TO_ID_COL = {
    "EUCT":   "euct_id",
    "ISRCTN": "isrctn_id",
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


def _load_existing_pairs(conn, entity_type: str) -> set:
    """All (a, b) pairs already recorded for this entity_type, as order-independent
    frozensets. Loading them once lets the detectors test membership in memory
    instead of issuing a per-pair existence query inside their O(N^2) loops."""
    rows = conn.execute(
        "SELECT record_a_id, record_b_id FROM merge_candidates WHERE entity_type = ?",
        (entity_type,),
    ).fetchall()
    return {frozenset((a, b)) for a, b in rows}


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


def _sponsor_block_key(sponsor: str) -> str:
    if not sponsor:
        return ""
    tokens = re.sub(r"[^\w]", " ", sponsor.lower()).split()
    return tokens[0] if tokens else ""


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
    existing = _load_existing_pairs(conn, "trials")

    # Two blocking strategies — a pair is considered if it shares either block.
    # 1. (therapeutic_area, phase) catches NCT↔NCT with a populated area.
    # 2. (phase, sponsor first-token) catches cross-registry pairs where the
    #    non-NCT registry didn't populate therapeutic_area but the sponsor name
    #    overlaps. Without this fallback, no CRIS/EUCT/ISRCTN trial can ever
    #    match an NCT trial because therapeutic_area is "" on every non-NCT row.
    blocks: dict = {}
    for t in trials:
        ta = t.get("therapeutic_area") or ""
        ph = t.get("phase") or ""
        sb = _sponsor_block_key(t.get("sponsor") or "")
        if ta and ph:
            blocks.setdefault(("ta", ta, ph), []).append(t)
        if sb and ph:
            blocks.setdefault(("sp", sb, ph), []).append(t)

    now = datetime.utcnow().isoformat()
    pending = 0
    auto_merged = 0
    seen_pairs: set = set()

    for group_trials in blocks.values():
        nct = [t for t in group_trials if _is_nct(t["id"])]
        others = [t for t in group_trials if not _is_nct(t["id"])]
        if not nct or not others:
            continue

        for a in nct:
            for b in others:
                pair_key = tuple(sorted([a["id"], b["id"]]))
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                if frozenset((a["id"], b["id"])) in existing:
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
    existing = _load_existing_pairs(conn, "organizations")

    now = datetime.utcnow().isoformat()
    pending = 0

    # Token blocking. Two names with Jaccard > 0.7 necessarily share most of their
    # tokens (Jaccard is 0 when they share none), so a real match always co-occurs
    # in at least one token's bucket. Comparing only within buckets skips the vast
    # majority of pairs that share no token and can't clear the threshold — turning
    # the O(N^2) all-pairs scan into ~O(sum of bucket^2) without missing any match.
    token_buckets: dict = {}
    for idx, o in enumerate(orgs):
        toks = set(re.sub(r"[^\w]", " ", (o["canonical_name"] or "").lower()).split())
        for tok in toks:
            token_buckets.setdefault(tok, []).append(idx)

    checked: set = set()
    for idxs in token_buckets.values():
        for ii in range(len(idxs)):
            for jj in range(ii + 1, len(idxs)):
                i, j = (idxs[ii], idxs[jj]) if idxs[ii] < idxs[jj] else (idxs[jj], idxs[ii])
                if (i, j) in checked:
                    continue
                checked.add((i, j))
                a, b = orgs[i], orgs[j]
                if frozenset((a["id"], b["id"])) in existing:
                    continue
                sim = _jaccard_tokens(a["canonical_name"], b["canonical_name"])
                if sim <= 0.7:
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
