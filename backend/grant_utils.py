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
    # CNS / psychiatry & neurology (AiCure's primary focus — must be present here or
    # is_medical() would filter these grants out before they reach the pipeline)
    "schizophrenia", "psychosis", "depression", "major depressive", "ptsd",
    "post-traumatic", "bipolar", "adhd", "attention deficit", "anxiety",
    "addiction", "substance use", "alcohol", "opioid", "smoking cessation",
    "borderline personality", "tardive dyskinesia", "parkinson", "alzheimer",
    "dementia", "huntington", "amyotrophic", "multiple sclerosis", "epilepsy",
    "seizure", "essential tremor", "neurology", "psychiatric", "cns",
    # cardiometabolic (secondary)
    "obesity", "GLP-1", "diabetes", "type 2 diabetes", "weight loss", "cardiac",
    "heart failure", "atrial fibrillation", "metabolic", "bariatric",
    "cardiometabolic", "endocrinology", "cardiovascular", "hypertension",
    "insulin", "glucose", "NASH", "blood pressure", "coronary", "stroke",
    "kidney", "renal",
    # cross-cutting
    "adherence", "clinical trial", "randomized", "placebo",
    "phase 1", "phase 2", "phase 3",
]

PHASE_PATTERN = re.compile(r'\bphase\s*(1|2|3|4|I{1,3}V?)\b', re.IGNORECASE)

CONDITION_KEYWORDS = [
    # CNS / psychiatry & neurology (primary)
    "schizophrenia", "major depressive disorder", "depression", "PTSD",
    "bipolar disorder", "ADHD", "anxiety", "substance use disorder",
    "alcohol use disorder", "opioid use disorder", "smoking cessation",
    "tardive dyskinesia", "Parkinson's disease", "Alzheimer's disease", "dementia",
    "Huntington's disease", "amyotrophic lateral sclerosis", "multiple sclerosis",
    "epilepsy", "essential tremor",
    # cardiometabolic (secondary)
    "obesity", "overweight", "type 2 diabetes", "T2D", "heart failure",
    "atrial fibrillation", "cardiovascular", "hypertension", "dyslipidemia",
    "metabolic syndrome", "bariatric", "weight loss", "cardiometabolic",
    "non-alcoholic fatty liver", "NAFLD", "NASH", "chronic kidney disease",
    "medication adherence", "treatment adherence",
]


# ── Human-subjects gate (§3a) ─────────────────────────────────────────────────
# Many research grants (esp. NIH RePORTER R01s) fund preclinical / animal /
# in-vitro work that AiCure's adherence platform can never serve. Exclude grants
# whose abstract is dominated by animal/basic-science cues with no human-subjects
# signal. Conservative: a grant with ANY explicit human cue is kept.
_ANIMAL_CUES = [
    "mouse", "mice", "murine", "rodent", "zebrafish",
    "drosophila", "in vitro", "in-vitro", "preclinical", "pre-clinical",
    "animal model", "cell line", "xenograft",
    "knockout", "transgenic", "c. elegans", "non-human primate",
]
# Short ambiguous tokens matched on WORD BOUNDARIES so "rat"/"rats" catch
# "rat." / "rats," / "rat-derived" (which the old space-padded " rat " missed)
# without hiding inside "strategy" / "operate".
_ANIMAL_WORD_CUES = [re.compile(r"\brats?\b")]
_HUMAN_CUES = [
    "patient", "participant", "human subject", "clinical trial", "adults",
    "volunteers", "in humans", "human participants", "enrolled", "randomized",
    "phase 1", "phase 2", "phase 3", "phase i", "phase ii", "phase iii",
]


def is_human_subjects(text: str) -> bool:
    """True unless the text is clearly preclinical/animal with no human cue.

    Returns True for empty/unknown text (don't drop on missing data) and for any
    text carrying an explicit human-subjects cue. Returns False only when animal /
    basic-science cues are present AND no human cue is — i.e. a confidently
    non-human grant.
    """
    if not text:
        return True
    t = text.lower()
    has_human = any(c in t for c in _HUMAN_CUES)
    if has_human:
        return True
    has_animal = (any(c in t for c in _ANIMAL_CUES)
                  or any(p.search(t) for p in _ANIMAL_WORD_CUES))
    return not has_animal


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
    "first_seen", "human_subjects",
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
