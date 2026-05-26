import re
from datetime import datetime

from db import get_connection
from registry_utils import extract_nct  # reuse existing NCT extractor

GBP_TO_USD = 1.27
EUR_TO_USD = 1.08

MEDICAL_KEYWORDS = [
    "obesity", "GLP-1", "semaglutide", "tirzepatide", "liraglutide",
    "type 2 diabetes", "weight loss", "cardiac", "heart failure",
    "atrial fibrillation", "dulaglutide", "metabolic", "bariatric",
    "cardiometabolic", "endocrinology", "adherence", "clinical trial",
    "randomized", "placebo", "phase 1", "phase 2", "phase 3",
]


def is_medical(text: str) -> bool:
    if not text:
        return False
    t = text.lower()
    return any(k.lower() in t for k in MEDICAL_KEYWORDS)


def classify_area(text: str) -> str:
    t = (text or "").lower()
    if any(k in t for k in ["obes", "glp", "weight", "semaglutide", "tirzepatide",
                              "liraglutide", "dulaglutide"]):
        return "Metabolic / GLP-1"
    if "diabet" in t:
        return "Diabetes"
    if any(k in t for k in ["cardiac", "heart", "coronary", "atrial", "cardiovascular"]):
        return "Cardiovascular"
    if "adher" in t or "compliance" in t:
        return "Adherence / Outcomes"
    return "Other"


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
