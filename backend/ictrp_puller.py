import csv
import io
import json
import os
import time
from datetime import datetime

import requests

from db import get_connection
from registry_utils import (
    extract_nct, is_relevant, merge_or_insert,
    normalize_phase, normalize_status, snapshots_enabled,
)

SEARCH_TERMS = [
    "obesity", "GLP-1", "semaglutide", "tirzepatide", "type 2 diabetes",
    "heart failure", "atrial fibrillation", "metabolic syndrome", "NASH", "adherence",
]

WHO_CSV_URL = "https://trialsearch.who.int/Results.aspx"

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")

# Maps TrialID prefix → (registry_name, id_column or None)
REGISTRY_PREFIXES = {
    "ACTRN":  ("ANZCTR",  "anzctr_id"),
    "DRKS":   ("DRKS",    "drks_id"),
    "jRCT":   ("jRCT",    "jrct_id"),
    "NL":     ("NTR",     "ntr_id"),
    "ChiCTR": ("ChiCTR",  "chictr_id"),
    "CTRI":   ("CTRI",    "ctri_id"),
    "IRCT":   ("IRCT",    "irct_id"),
    "RBR":    ("ReBec",   "rebec_id"),
    "PACTR":  ("PACTR",   "pactr_id"),
    "TCTR":   ("TCTR",    None),
    "SLCTR":  ("SLCTR",   None),
    "LBCTR":  ("LBCTR",   None),
}

# Already covered by direct connectors — skip these
SKIP_PREFIXES = {"NCT", "EUCTR", "ISRCTN", "KCT"}


def _detect_registry(trial_id: str):
    """Return (registry_name, id_column) for a TrialID, or (None, None) to skip."""
    for prefix, (name, col) in REGISTRY_PREFIXES.items():
        if trial_id.startswith(prefix):
            return name, col
    for skip in SKIP_PREFIXES:
        if trial_id.startswith(skip):
            return None, None
    # Unknown prefix — store under generic registry name, no dedicated column
    return f"WHO-{trial_id.split('-')[0]}", None


def _save_snapshot(term: str, text: str):
    if not snapshots_enabled():
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"ictrp_{safe_term}_{ts}.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _derive_sponsor_type(source_text: str) -> str:
    if not source_text:
        return "OTHER"
    t = source_text.lower()
    if any(k in t for k in ["industry", "pharmaceutical", "biotech", "pharma"]):
        return "INDUSTRY"
    if any(k in t for k in ["university", "hospital", "academic", "college", "institute"]):
        return "ACADEMIC"
    return "OTHER"


def _parse_json_list(text: str) -> str:
    """Split semicolon-separated text into a JSON array."""
    if not text:
        return json.dumps([])
    items = [s.strip() for s in text.split(";") if s.strip()]
    return json.dumps(items)


def pull_all_ictrp():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    conn = get_connection()
    seen_ids: set = set()

    try:
        for term in SEARCH_TERMS:
            try:
                resp = session.get(
                    WHO_CSV_URL,
                    params={
                        "conditions": term,
                        "Format": "CSV",
                        "pageno": 1,
                    },
                    timeout=30,
                )
                resp.raise_for_status()
                text = resp.text
            except Exception as e:
                print(f"  [WARN] ICTRP fetch failed (term={term!r}): {e}")
                time.sleep(2.0)
                continue

            _save_snapshot(term, text)

            try:
                reader = csv.DictReader(io.StringIO(text))
                rows = list(reader)
            except Exception as e:
                print(f"  [WARN] ICTRP CSV parse failed (term={term!r}): {e}")
                time.sleep(2.0)
                continue

            for row in rows:
                trial_id = (row.get("TrialID") or "").strip()
                if not trial_id:
                    continue
                if trial_id in seen_ids:
                    continue

                registry_name, id_col = _detect_registry(trial_id)
                if registry_name is None:
                    continue

                conditions_text = row.get("Health condition(s)") or ""
                interventions_text = row.get("Intervention(s)") or ""
                if not is_relevant(f"{conditions_text} {interventions_text}"):
                    continue

                seen_ids.add(trial_id)

                nct_cross = extract_nct(row.get("Secondary IDs") or "")

                record = {
                    "title_brief": (row.get("Public title") or "")[:500] or None,
                    "title_official": (row.get("Scientific title") or "")[:1000] or None,
                    "sponsor": (row.get("Primary sponsor") or "")[:300] or None,
                    "sponsor_type": _derive_sponsor_type(row.get("Source of Monetary Support") or ""),
                    "start_date": row.get("Date of first enrolment") or None,
                    "first_posted": row.get("Date of registration") or None,
                    "enrollment": (
                        int(row["Target sample size"])
                        if (row.get("Target sample size") or "").strip().isdigit()
                        else None
                    ),
                    "status": normalize_status(row.get("Recruitment status") or ""),
                    "phase": normalize_phase(row.get("Phase") or ""),
                    "countries": _parse_json_list(row.get("Countries of recruitment") or ""),
                    "conditions": _parse_json_list(conditions_text),
                    "interventions": _parse_json_list(interventions_text),
                    "primary_endpoints": (row.get("Primary outcome(s)") or "")[:2000] or None,
                    "secondary_endpoints": (row.get("Secondary outcome(s)") or "")[:2000] or None,
                    "source_url": row.get("web address") or None,
                    "registry_id": trial_id,
                }

                if nct_cross:
                    record["_nct_cross_ref"] = nct_cross

                if id_col:
                    try:
                        merge_or_insert(record, registry_name, trial_id, id_col, conn=conn)
                    except Exception as e:
                        print(f"  [WARN] ICTRP merge error for {trial_id}: {e}")
                else:
                    # No dedicated column — plain upsert with prefixed id
                    try:
                        prefix_code = registry_name.replace("WHO-", "")
                        record.pop("_nct_cross_ref", None)
                        record["id"] = f"{prefix_code}-{trial_id}"
                        record["registry_sources"] = json.dumps([registry_name])
                        record["all_registry_ids"] = json.dumps([trial_id])
                        record["ingested_at"] = datetime.utcnow().isoformat()
                        cols = ", ".join(record.keys())
                        placeholders = ", ".join("?" * len(record))
                        conn.execute(
                            f"INSERT OR REPLACE INTO trials ({cols}) VALUES ({placeholders})",
                            list(record.values()),
                        )
                        conn.execute(
                            "INSERT OR IGNORE INTO registry_source_records "
                            "(trial_id, registry, registry_trial_id, ingested_at) VALUES (?, ?, ?, ?)",
                            (record["id"], registry_name, trial_id, datetime.utcnow().isoformat()),
                        )
                    except Exception as e:
                        print(f"  [WARN] ICTRP insert error for {trial_id}: {e}")

            conn.commit()
            time.sleep(2.0)

    finally:
        conn.commit()
        conn.close()

    print(f"  ICTRP: processed {len(seen_ids)} unique records")
