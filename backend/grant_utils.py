import json
import re
from datetime import datetime

from db import get_connection
from registry_utils import extract_nct  # reuse existing NCT extractor

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

DRUG_KEYWORDS = [
    "semaglutide", "tirzepatide", "liraglutide", "dulaglutide", "ozempic",
    "wegovy", "mounjaro", "victoza", "saxenda", "rybelsus", "jardiance",
    "farxiga", "trulicity", "metformin", "insulin", "glp-1", "sglt2",
    "dpp-4", "sitagliptin", "empagliflozin", "dapagliflozin",
]

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


_ONCOLOGY_CUES = ["cancer", "tumor", "tumour", "oncolog", "carcinoma", "neoplas",
                  "melanoma", "lymphoma", "leukemia", "leukaemia", "metasta", "glioma"]
_STRONG_CM = ["semaglutide", "tirzepatide", "liraglutide", "dulaglutide", "glp-1",
              "obesity", "obese", "diabet", "heart failure", "atrial fib"]


def classify_area(text: str) -> str:
    t = (text or "").lower()
    # Oncology guard: cancer-metabolism / tumor grants were leaking into
    # "Metabolic / GLP-1" via loose substrings (e.g. "metabolic"). If the text is
    # clearly oncology and has no strong cardiometabolic anchor, it's off-focus.
    if any(k in t for k in _ONCOLOGY_CUES) and not any(k in t for k in _STRONG_CM):
        return "Other"
    if any(k in t for k in ["obes", "glp", "weight loss", "weight management",
                              "semaglutide", "tirzepatide", "liraglutide",
                              "dulaglutide", "bariatric", "cardiometabolic",
                              "metabolic syndrome"]):
        return "Metabolic / GLP-1"
    if "diabet" in t or "insulin" in t or "glycem" in t or "glycaem" in t:
        return "Diabetes"
    if any(k in t for k in ["cardiac", "heart", "coronary", "atrial", "cardiovascular",
                              "hypertens", "blood pressure", "stroke", "arrhythm", "vascular"]):
        return "Cardiovascular"
    if any(k in t for k in ["nash", "nafld", "fatty liver", "hepatic", "steatohep"]):
        return "Liver / NASH"
    if any(k in t for k in ["kidney", "renal", "nephro", "ckd"]):
        return "Renal"
    if "adher" in t or "compliance" in t:
        return "Adherence / Outcomes"
    return "Other"


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


def upsert_grant(record: dict):
    conn = get_connection()
    record = dict(record)
    record["ingested_at"] = datetime.utcnow().isoformat()
    cols = ", ".join(record.keys())
    placeholders = ", ".join("?" * len(record))
    conn.execute(
        f"INSERT OR REPLACE INTO grants ({cols}) VALUES ({placeholders})",
        list(record.values()),
    )
    conn.commit()
    conn.close()
