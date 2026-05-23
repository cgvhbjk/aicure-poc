import os
import re
import json
from datetime import datetime

from db import get_connection

# Multi-word, word-boundaried domain keywords — narrower than the old
# substring match so we don't pull in "cardiac arrest" or "metabolic panel".
RELEVANCE_PATTERN = re.compile(
    r"\b("
    r"obesity|over[-\s]?weight|weight[-\s]?loss|"
    r"GLP[-\s]?1|GLP[-\s]?1\s?(?:receptor\s+agonist|RA)|"
    r"semaglutide|tirzepatide|liraglutide|dulaglutide|retatrutide|cagrilintide|"
    r"type[-\s]?2[-\s]?diabetes|T2D|T2DM|diabetes\s+mellitus|"
    r"heart\s+failure|HFpEF|HFrEF|"
    r"atrial\s+fibrillation|"
    r"metabolic\s+syndrome|cardiometabolic|MASH|NASH|NAFLD|MAFLD"
    r")\b",
    re.IGNORECASE,
)

NCT_PATTERN = re.compile(r'\bNCT\d{8}\b')

# Allowlist of registry-id columns that may be interpolated into UPDATE/INSERT.
_ALLOWED_ID_COLUMNS = {
    "isrctn_id", "ntr_id", "anzctr_id", "drks_id",
    "jrct_id", "cris_id", "eudract_id", "euct_id",
    "eudract_number",
}

_PREFIX_MAP = {
    "ISRCTN": "ISRCTN",
    "NTR": "NTR",
    "ANZCTR": "ANZCTR",
    "DRKS": "DRKS",
    "jRCT": "JRCT",
    "CRIS": "CRIS",
    "EudraCT": "EUCTR",
}

# Status mapping checked in precedence order — more specific first so
# "NOT_YET_RECRUITING" doesn't match "RECRUITING".
_STATUS_PRECEDENCE = [
    ("NOT_YET_RECRUITING", "NOT_YET_RECRUITING"),
    ("ACTIVE_NOT_RECRUITING", "ACTIVE_NOT_RECRUITING"),
    ("ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING"),
    ("RECRUITING", "RECRUITING"),
    ("ENROLLING", "RECRUITING"),
    ("OPEN", "RECRUITING"),
    ("TERMINATED", "TERMINATED"),
    ("STOPPED", "TERMINATED"),
    ("WITHDRAWN", "WITHDRAWN"),
    ("SUSPENDED", "SUSPENDED"),
    ("COMPLETED", "COMPLETED"),
    ("FINISHED", "COMPLETED"),
    ("ENDED", "COMPLETED"),
    ("CLOSED", "COMPLETED"),
    ("ONGOING", "ACTIVE_NOT_RECRUITING"),
]

_PHASE_PATTERNS = [
    (re.compile(r"\b(4|IV)\b"),   "PHASE4"),
    (re.compile(r"\b(3|III)\b"),  "PHASE3"),
    (re.compile(r"\b(2|II)\b"),   "PHASE2"),
    (re.compile(r"\b(1|I)\b"),    "PHASE1"),
]


def extract_nct(text: str):
    if not text:
        return None
    m = NCT_PATTERN.search(text)
    return m.group(0) if m else None


_STATUS_TOKEN_RE = re.compile(r'[^A-Z0-9]+')


def normalize_status(raw: str) -> str:
    if not raw:
        return "UNKNOWN"
    r = _STATUS_TOKEN_RE.sub("_", raw.upper()).strip("_")
    for needle, mapped in _STATUS_PRECEDENCE:
        if needle in r:
            return mapped
    return "UNKNOWN"


def normalize_phase(raw: str):
    if not raw:
        return None
    r = raw.upper()
    for pattern, label in _PHASE_PATTERNS:
        if pattern.search(r):
            return label
    return None


def is_relevant(text: str) -> bool:
    if not text:
        return False
    return bool(RELEVANCE_PATTERN.search(text))


def snapshots_enabled() -> bool:
    """Snapshots are off by default; opt in with AICURE_SNAPSHOTS=1."""
    return os.environ.get("AICURE_SNAPSHOTS") == "1"


def _record_cross_ref_candidate(cur, new_id: str, missing_nct: str):
    """Queue a merge candidate so the audit UI can reconcile a registry
    record that claims to be the same trial as an NCT we haven't ingested yet."""
    existing = cur.execute(
        """SELECT id FROM merge_candidates
           WHERE entity_type = 'trials'
             AND ((record_a_id = ? AND record_b_id = ?)
                  OR (record_a_id = ? AND record_b_id = ?))""",
        (new_id, missing_nct, missing_nct, new_id),
    ).fetchone()
    if existing:
        return
    cur.execute(
        """INSERT INTO merge_candidates
           (entity_type, record_a_id, record_b_id, confidence,
            match_fields, match_scores, status, created_at)
           VALUES ('trials', ?, ?, ?, ?, ?, 'PENDING', ?)""",
        (
            new_id,
            missing_nct,
            0.9,
            json.dumps(["cross_ref_nct"]),
            json.dumps({"cross_ref_nct": 0.9}),
            datetime.utcnow().isoformat(),
        ),
    )


def merge_or_insert(record: dict, registry_name: str, registry_id: str,
                    id_column: str, conn=None):
    """
    Insert or merge a registry record into the trials table.

    If `record["_nct_cross_ref"]` points to an existing NCT row, only update
    the registry-tracking columns on that row. Otherwise INSERT a new row
    prefixed with the registry's short code; if the cross-ref points to an
    NCT we haven't ingested yet, also queue a merge candidate for later
    reconciliation.

    `conn`: optional shared sqlite3 connection. When None, a connection is
    opened and committed per call (legacy behavior). When provided, the
    caller owns the lifecycle — no commit, no close — enabling batching.
    """
    if id_column not in _ALLOWED_ID_COLUMNS:
        raise ValueError(f"merge_or_insert: id_column {id_column!r} is not allowed")

    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    cur = conn.cursor()

    nct_id = record.pop("_nct_cross_ref", None)
    existing_id = None

    if nct_id:
        row = cur.execute(
            "SELECT id, registry_sources, all_registry_ids FROM trials WHERE id = ?",
            (nct_id,),
        ).fetchone()
        if row:
            existing_id = row["id"]
            existing_sources = json.loads(row["registry_sources"] or "[]")
            existing_ids = json.loads(row["all_registry_ids"] or "[]")
            if registry_name not in existing_sources:
                existing_sources.append(registry_name)
            if registry_id not in existing_ids:
                existing_ids.append(registry_id)
            cur.execute(
                f"UPDATE trials SET {id_column} = ?, registry_sources = ?, "
                "all_registry_ids = ? WHERE id = ?",
                (registry_id, json.dumps(existing_sources),
                 json.dumps(existing_ids), existing_id),
            )

    if not existing_id:
        prefix = _PREFIX_MAP.get(registry_name, registry_name)
        new_id = f"{prefix}-{registry_id}"
        record["id"] = new_id
        record[id_column] = registry_id
        record["registry_sources"] = json.dumps([registry_name])
        record["all_registry_ids"] = json.dumps([registry_id])
        record["ingested_at"] = datetime.utcnow().isoformat()

        cols = ", ".join(record.keys())
        placeholders = ", ".join("?" * len(record))
        cur.execute(
            f"INSERT OR REPLACE INTO trials ({cols}) VALUES ({placeholders})",
            list(record.values()),
        )
        existing_id = new_id

        # NCT cross-ref existed but target row didn't — queue a candidate
        # so the merge auditor can resolve it once the NCT is ingested.
        if nct_id:
            _record_cross_ref_candidate(cur, new_id, nct_id)

    cur.execute(
        "INSERT OR IGNORE INTO registry_source_records "
        "(trial_id, registry, registry_trial_id, ingested_at) VALUES (?, ?, ?, ?)",
        (existing_id, registry_name, registry_id, datetime.utcnow().isoformat()),
    )

    if own_conn:
        conn.commit()
        conn.close()
