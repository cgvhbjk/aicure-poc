import requests
import json
import re
import time
from datetime import datetime
from db import get_connection
from registry_utils import upsert_trial

CTIS_SEARCH = "https://euclinicaltrials.eu/ctis-public-api/search"
CTIS_DETAIL = "https://euclinicaltrials.eu/ctis-public-api/retrieve/{}"

SEARCH_TERMS = [
    "GLP-1", "semaglutide", "tirzepatide", "obesity", "weight loss",
    "type 2 diabetes", "heart failure", "atrial fibrillation", "liraglutide",
]

# Numeric status codes from search results
STATUS_CODE_MAP = {
    1: "NOT_YET_RECRUITING",   # Authorised
    2: "NOT_YET_RECRUITING",   # Submitted
    3: "RECRUITING",           # In Progress
    4: "RECRUITING",           # Ongoing
    5: "ACTIVE_NOT_RECRUITING",# Temporarily halted
    6: "ACTIVE_NOT_RECRUITING",# Suspended
    7: "COMPLETED",            # Completed
    8: "COMPLETED",            # Ended
    9: "COMPLETED",            # Terminated
}

# String status from detail endpoint
STATUS_STR_MAP = {
    "authorised":         "NOT_YET_RECRUITING",
    "in progress":        "RECRUITING",
    "ongoing":            "RECRUITING",
    "temporarily halted": "ACTIVE_NOT_RECRUITING",
    "suspended":          "ACTIVE_NOT_RECRUITING",
    "ended":              "COMPLETED",
    "completed":          "COMPLETED",
    "terminated":         "COMPLETED",
}

# Order matters: longer/more-specific strings must come before shorter ones
# so "phase iv" is checked before "phase i", preventing false substring matches.
PHASE_MAP = [
    ("therapeutic use",          "PHASE4"),
    ("phase iv",                 "PHASE4"),
    ("phase 4",                  "PHASE4"),
    ("therapeutic confirmatory", "PHASE3"),
    ("phase iii",                "PHASE3"),
    ("phase 3",                  "PHASE3"),
    ("therapeutic exploratory",  "PHASE2"),
    ("phase ii",                 "PHASE2"),
    ("phase 2",                  "PHASE2"),
    ("human pharmacology",       "PHASE1"),
    ("first in human",           "PHASE1"),
    ("phase i",                  "PHASE1"),
    ("phase 1",                  "PHASE1"),
]

NCT_RE = re.compile(r'NCT\d{8}')


def _map_status_code(code):
    if isinstance(code, int):
        return STATUS_CODE_MAP.get(code)
    if isinstance(code, str):
        return STATUS_STR_MAP.get(code.lower().strip())
    return None


def _map_phase(raw):
    if not raw:
        return None
    lower = raw.lower()
    for key, val in PHASE_MAP:
        if key in lower:
            return val
    return None


def _extract_nct(text):
    m = NCT_RE.search(text)
    return m.group(0) if m else None


def _search(term, page=1, size=100):
    body = {
        "searchCriteria": {"containAll": term},
        "pagination": {"page": page, "size": size},
    }
    try:
        r = requests.post(
            CTIS_SEARCH, json=body, timeout=30,
            headers={"Accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [CTIS] Search error for '{term}' p{page}: {e}")
        return {}


def _detail(ct_number):
    try:
        r = requests.get(CTIS_DETAIL.format(ct_number), timeout=30)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"  [CTIS] Detail error for '{ct_number}': {e}")
        return None


def _upsert_registry_record(conn, trial_id, registry_trial_id, raw_data, ingested_at):
    conn.execute("""
        INSERT OR REPLACE INTO registry_source_records
            (trial_id, registry, registry_trial_id, raw_data, ingested_at)
        VALUES (?, 'CTIS', ?, ?, ?)
    """, (trial_id, registry_trial_id, raw_data, ingested_at))


def _upsert(conn, summary, detail):
    ct_number = summary.get("ctNumber")
    if not ct_number:
        return

    raw_text = json.dumps({"summary": summary, "detail": detail})
    nct_id = _extract_nct(raw_text)

    # Title: prefer ctTitle from summary, fall back to detail fields
    title = summary.get("ctTitle") or summary.get("shortTitle") or ct_number

    # Status: detail has string, summary has numeric
    if detail:
        status = _map_status_code(detail.get("ctStatus"))
    else:
        status = _map_status_code(summary.get("ctStatus"))

    phase = _map_phase(summary.get("trialPhase", ""))
    sponsor = summary.get("sponsor", "")
    start_date = summary.get("startDateEU")

    # Member states from search: ["Germany:8", "France:4", ...]
    countries_raw = summary.get("trialCountries") or []
    member_states = [c.split(":")[0] for c in countries_raw if isinstance(c, str)]
    eu_member_states = json.dumps(member_states)

    # EudraCT cross-reference from detail
    eudract_num = None
    if detail:
        aa = detail.get("authorizedApplication", {})
        eudract_info = aa.get("eudraCt", {})
        if eudract_info.get("isTransitioned"):
            eudract_num = eudract_info.get("eudraCTNumber")

    ingested_at = datetime.utcnow().isoformat()

    if nct_id:
        existing = conn.execute(
            "SELECT id, registry_sources, all_registry_ids FROM trials WHERE id = ?", (nct_id,)
        ).fetchone()
        if existing:
            reg_sources = json.loads(existing["registry_sources"] or '["ClinicalTrials.gov"]')
            if "CTIS" not in reg_sources:
                reg_sources.append("CTIS")
            all_ids = json.loads(existing["all_registry_ids"] or json.dumps([nct_id]))
            if ct_number not in all_ids:
                all_ids.append(ct_number)
            conn.execute("""
                UPDATE trials
                SET euct_id = ?, registry_sources = ?, all_registry_ids = ?,
                    eu_member_states = ?, eudract_number = COALESCE(eudract_number, ?)
                WHERE id = ?
            """, (ct_number, json.dumps(reg_sources), json.dumps(all_ids),
                  eu_member_states, eudract_num, nct_id))
            _upsert_registry_record(conn, nct_id, ct_number, raw_text, ingested_at)
            return

    trial_id = f"EUCT-{ct_number}"
    source_url = f"https://euclinicaltrials.eu/search-for-clinical-trials/?lang=en&query=ctNumber:{ct_number}"
    reg_sources = ["CTIS"]
    all_ids = [ct_number]
    if eudract_num:
        reg_sources.append("EU-CTR")
        all_ids.append(eudract_num)

    # ON CONFLICT upsert (not INSERT OR REPLACE): a re-pull / cross-registry
    # enrichment keeps server-owned crm_*/aicure_fit AND the richer columns a
    # fuller puller (e.g. ct_puller) may have already set on this row.
    upsert_trial(conn, {
        "id": trial_id, "title_brief": title, "status": status, "phase": phase,
        "sponsor": sponsor, "start_date": start_date, "registry_id": ct_number,
        "source_url": source_url,
        "registry_sources": json.dumps(reg_sources),
        "all_registry_ids": json.dumps(all_ids),
        "euct_id": ct_number, "eu_member_states": eu_member_states,
        "eudract_number": eudract_num, "has_news": 0, "ingested_at": ingested_at,
    })
    _upsert_registry_record(conn, trial_id, ct_number, raw_text, ingested_at)


def pull_all_ctis():
    conn = get_connection()
    seen = set()
    total = 0

    for term in SEARCH_TERMS:
        print(f"  [CTIS] Searching '{term}'...")
        page = 1
        while True:
            data = _search(term, page=page)
            results = data.get("data") or []
            if not results:
                break
            for summary in results:
                ct_number = summary.get("ctNumber")
                if not ct_number or ct_number in seen:
                    continue
                seen.add(ct_number)
                time.sleep(0.3)
                detail = _detail(ct_number)
                _upsert(conn, summary, detail)
                total += 1
            conn.commit()
            pagination = data.get("pagination", {})
            if not pagination.get("nextPage"):
                break
            page += 1

    conn.close()
    print(f"  [CTIS] Total processed: {total}")


if __name__ == "__main__":
    pull_all_ctis()
