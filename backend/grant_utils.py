import json
import re
from datetime import datetime

from db import get_connection
from registry_utils import extract_nct  # reuse existing NCT extractor
# Single source of truth — see text_match.py (was duplicated/diverging here).
from text_match import DRUG_KEYWORDS, classify_area  # noqa: F401 (re-exported)

GBP_TO_USD = 1.27
EUR_TO_USD = 1.08

MEDICAL_KEYWORDS = [
    "obesity", "GLP-1", "semaglutide", "tirzepatide", "liraglutide",
    "diabetes", "type 2 diabetes", "weight loss", "cardiac", "heart failure",
    "atrial fibrillation", "dulaglutide", "metabolic", "bariatric",
    "cardiometabolic", "endocrinology", "adherence", "clinical trial",
    "randomized", "placebo", "phase 1", "phase 2", "phase 3",
    "cardiovascular", "hypertension", "insulin", "glucose", "NASH",
    "blood pressure", "coronary", "stroke", "kidney", "renal",
]

PHASE_PATTERN = re.compile(r'\bphase\s*(1|2|3|4|I{1,3}V?)\b', re.IGNORECASE)

CONDITION_KEYWORDS = [
    "obesity", "overweight", "type 2 diabetes", "T2D", "heart failure",
    "atrial fibrillation", "cardiovascular", "hypertension", "dyslipidemia",
    "metabolic syndrome", "bariatric", "weight loss", "cardiometabolic",
    "non-alcoholic fatty liver", "NAFLD", "NASH", "chronic kidney disease",
    "medication adherence", "treatment adherence",
]


def is_medical(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(k.lower() in t for k in MEDICAL_KEYWORDS)


def extract_phase(text: str):
    if not text:
        return None
    m = PHASE_PATTERN.search(text)
    if not m:
        return None
    raw = m.group(1).upper()
    mapping = {
        "1": "Phase 1", "I": "Phase 1",
        "2": "Phase 2", "II": "Phase 2",
        "3": "Phase 3", "III": "Phase 3",
        "4": "Phase 4", "IV": "Phase 4",
    }
    return mapping.get(raw)


def extract_conditions(text: str) -> str:
    if not text:
        return "[]"
    t = text.lower()
    found = [k for k in CONDITION_KEYWORDS if k.lower() in t]
    return json.dumps(list(dict.fromkeys(found)))


def extract_interventions(text: str) -> str:
    if not text:
        return "[]"
    t = text.lower()
    found = [k for k in DRUG_KEYWORDS if k.lower() in t]
    return json.dumps(list(dict.fromkeys(found)))


# Columns upsert_grant is allowed to write. Guards the dynamic column-name
# interpolation below: column names come from record.keys() (ultimately derived
# from external grant-API field maps), so an unexpected key must be rejected
# rather than spliced into the SQL. Keep in sync with the grants schema in db.py.
_GRANT_COLUMNS = frozenset({
    "id", "source", "award_id", "title", "abstract", "pi_name", "pi_email",
    "organization", "org_type", "sponsor_funder", "amount_usd", "currency",
    "amount_original", "start_date", "end_date", "award_date", "status",
    "therapeutic_area", "conditions", "interventions", "phase_mentioned",
    "linked_trial_id", "country", "source_url", "raw_snapshot_path",
    "ingested_at", "has_trial_link", "aicure_fit", "activity_code",
    "agency_division", "fiscal_year", "project_acronym", "research_type",
    "first_seen",
})


def upsert_grant(record: dict, conn=None):
    """Insert/update one grant. Pass an open `conn` to batch many upserts on a
    single connection and commit once (the pullers do this) — avoids the
    open-commit-close-per-row fsync storm. With no `conn`, opens/commits/closes
    its own (backward-compatible)."""
    own = conn is None
    if own:
        conn = get_connection()
    try:
        record = dict(record)
        now = datetime.utcnow().isoformat()
        record["ingested_at"] = now
        # first_seen is set once and preserved on re-pull (excluded from the
        # UPDATE below), so the weekly digest can tell genuinely-new grants from
        # re-pulled ones. INSERT OR REPLACE would reset it, so we use a targeted
        # upsert.
        record.setdefault("first_seen", now)
        cols = list(record.keys())
        unknown = [c for c in cols if c not in _GRANT_COLUMNS]
        if unknown:
            raise ValueError(f"upsert_grant: refusing unknown grant column(s): {unknown}")
        collist = ", ".join(cols)
        placeholders = ", ".join("?" * len(cols))
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c not in ("id", "first_seen"))
        conn.execute(
            f"INSERT INTO grants ({collist}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}",
            list(record.values()),
        )
        if own:
            conn.commit()
    finally:
        if own:
            conn.close()
