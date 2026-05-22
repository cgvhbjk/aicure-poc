import requests
import json
import os
import re
from datetime import datetime
from db import get_connection

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

BASE_URL = "https://clinicaltrials.gov/api/v2/studies"

CONDITIONS = [
    "GLP-1",
    "obesity",
    "semaglutide",
    "tirzepatide",
    "liraglutide",
    "Type 2 Diabetes",
    "weight loss",
    "cardiac care",
    "heart failure",
    "atrial fibrillation",
]

_EPRO_KEYWORDS = [
    "epro", "ecoa", "patient-reported outcome", "patient reported outcome",
    "electronic diary", "e-diary", "electronic patient", "pro assessment",
    "clinical outcome assessment",
]
_DIGITAL_BIO_KEYWORDS = [
    "digital biomarker", "wearable", "accelerometer", "continuous glucose monitor",
    " cgm ", "biosensor", "actigraphy", "fitbit", "smartwatch", "apple watch",
    "digital health", "smartphone", "mobile health", "mhealth",
]
_DCT_KEYWORDS = [
    "decentralized", "remote visit", "telemedicine", "telehealth",
    "home visit", "virtual visit", " dct ", "home-based", "at-home",
]


def classify_therapeutic_area(conditions_list, mesh_list, interventions_list):
    combined = " ".join(
        (conditions_list or []) + (mesh_list or []) + (interventions_list or [])
    ).lower()
    if any(k in combined for k in ["obes", "glp", "weight", "semaglutide", "tirzepatide", "liraglutide"]):
        return "Metabolic / GLP-1"
    if "diabet" in combined:
        return "Diabetes"
    if any(k in combined for k in ["cardiac", "heart", "coronary", "atrial"]):
        return "Cardiovascular"
    return "Other"


def _parse_eligibility(text):
    """Split free-text eligibility criteria into inclusion and exclusion sections."""
    if not text:
        return None, None
    lower = text.lower()
    excl_idx = lower.find("exclusion criteria")
    if excl_idx == -1:
        return text[:2000] or None, None
    inclusion = text[:excl_idx].strip()
    incl_idx = inclusion.lower().find("inclusion criteria")
    if incl_idx != -1:
        inclusion = inclusion[incl_idx + len("inclusion criteria"):].lstrip(":").strip()
    exclusion = text[excl_idx:].strip()
    newline_idx = exclusion.find("\n")
    if newline_idx != -1:
        exclusion = exclusion[newline_idx:].strip()
    return (inclusion[:2000] or None), (exclusion[:2000] or None)


def _flag(text, keywords):
    t = text.lower()
    return 1 if any(k in t for k in keywords) else 0


def parse_study(study, snapshot_path=None):
    ps = study.get("protocolSection", {})

    id_mod        = ps.get("identificationModule", {})
    status_mod    = ps.get("statusModule", {})
    design_mod    = ps.get("designModule", {})
    cond_mod      = ps.get("conditionsModule", {})
    interv_mod    = ps.get("armsInterventionsModule", {})
    sponsor_mod   = ps.get("sponsorCollaboratorsModule", {})
    contacts_mod  = ps.get("contactsLocationsModule", {})
    outcomes_mod  = ps.get("outcomesModule", {})
    eligibility   = ps.get("eligibilityModule", {})
    description   = ps.get("descriptionModule", {})
    derived       = study.get("derivedSection", {})

    design_info  = design_mod.get("designInfo", {})
    masking_info = design_info.get("maskingInfo", {})

    nct_id = id_mod.get("nctId", "")

    # ── Conditions & interventions ──────────────────────────────────────
    conditions = cond_mod.get("conditions", [])
    keywords   = cond_mod.get("keywords", [])

    interventions_raw = interv_mod.get("interventions", [])
    interventions = [i.get("name", "") for i in interventions_raw if i.get("name")]

    arm_groups = interv_mod.get("armGroups", [])
    num_arms = len(arm_groups) if arm_groups else None

    # ── Sponsor ─────────────────────────────────────────────────────────
    lead_sponsor      = sponsor_mod.get("leadSponsor", {})
    responsible_party = sponsor_mod.get("responsibleParty", {})

    # ── Phase / status / type ───────────────────────────────────────────
    phases = design_mod.get("phases", [])
    phase  = phases[0] if phases else None

    # ── Enrollment ──────────────────────────────────────────────────────
    enroll_info = design_mod.get("enrollmentInfo", {})
    enrollment  = enroll_info.get("count")

    # ── Dates ───────────────────────────────────────────────────────────
    start_date          = (status_mod.get("startDateStruct") or {}).get("date")
    primary_completion  = (status_mod.get("primaryCompletionDateStruct") or {}).get("date")
    study_completion    = (status_mod.get("completionDateStruct") or {}).get("date")
    first_posted        = (status_mod.get("studyFirstPostDateStruct") or {}).get("date")
    last_updated        = (status_mod.get("lastUpdatePostDateStruct") or {}).get("date")

    # ── Locations ───────────────────────────────────────────────────────
    locations   = contacts_mod.get("locations", [])
    num_sites   = len(locations) if locations else None
    countries   = list(dict.fromkeys(
        loc.get("country", "") for loc in locations if loc.get("country")
    ))
    lead_country = countries[0] if countries else None

    # ── PI / contact ────────────────────────────────────────────────────
    pi_name = responsible_party.get("investigatorFullName")
    central_contacts = contacts_mod.get("centralContacts", [])
    pi_email = next((c.get("email") for c in central_contacts if c.get("email")), None)

    # ── Endpoints ───────────────────────────────────────────────────────
    primary_outcomes   = outcomes_mod.get("primaryOutcomes", [])
    secondary_outcomes = outcomes_mod.get("secondaryOutcomes", [])
    primary_endpoints   = "; ".join(
        o.get("measure", "") for o in primary_outcomes[:3] if o.get("measure")
    ) or None
    secondary_endpoints = json.dumps(
        [o.get("measure", "") for o in secondary_outcomes[:5] if o.get("measure")]
    )

    # ── MeSH / therapeutic area ─────────────────────────────────────────
    cond_browse = derived.get("conditionBrowseModule", {})
    mesh_terms  = [m.get("term", "") for m in cond_browse.get("meshes", []) if m.get("term")]
    therapeutic_area = classify_therapeutic_area(conditions + keywords, mesh_terms, interventions)

    # ── Study design ────────────────────────────────────────────────────
    randomized = design_info.get("allocation")        # RANDOMIZED / NON_RANDOMIZED
    masking    = masking_info.get("masking")          # NONE / SINGLE / DOUBLE / TRIPLE

    # ── Eligibility ─────────────────────────────────────────────────────
    min_age        = eligibility.get("minimumAge")
    max_age        = eligibility.get("maximumAge")
    sex_eligibility = eligibility.get("sex")
    std_ages       = eligibility.get("stdAges", [])
    is_pediatric   = 1 if "CHILD" in std_ages else 0

    eligibility_text = eligibility.get("eligibilityCriteria", "") or ""
    inclusion_criteria, exclusion_criteria = _parse_eligibility(eligibility_text)

    # ── Keyword flags ───────────────────────────────────────────────────
    brief_summary    = (description.get("briefSummary") or "")[:3000]
    detailed_desc    = description.get("detailedDescription") or ""
    full_text = " ".join([
        brief_summary, detailed_desc, eligibility_text,
        " ".join(o.get("measure", "") for o in primary_outcomes),
        " ".join(o.get("measure", "") for o in secondary_outcomes),
    ])
    epro_ecoa         = _flag(full_text, _EPRO_KEYWORDS)
    digital_biomarkers = _flag(full_text, _DIGITAL_BIO_KEYWORDS)
    dct_elements      = _flag(full_text, _DCT_KEYWORDS)

    return {
        "id":                  nct_id,
        "title_brief":         id_mod.get("briefTitle"),
        "title_official":      id_mod.get("officialTitle"),
        "registry_id":         nct_id,
        "source_url":          f"https://clinicaltrials.gov/study/{nct_id}",
        "raw_snapshot_path":   snapshot_path,
        "status":              status_mod.get("overallStatus"),
        "phase":               phase,
        "study_type":          design_mod.get("studyType"),
        "sponsor":             lead_sponsor.get("name"),
        "sponsor_type":        lead_sponsor.get("class"),
        "cro_named":           None,
        "lead_country":        lead_country,
        "countries":           json.dumps(countries),
        "num_sites":           num_sites,
        "randomized":          randomized,
        "masking":             masking,
        "num_arms":            num_arms,
        "conditions":          json.dumps(conditions),
        "interventions":       json.dumps(interventions),
        "therapeutic_area":    therapeutic_area,
        "mesh_terms":          json.dumps(mesh_terms),
        "enrollment":          enrollment,
        "min_age":             min_age,
        "max_age":             max_age,
        "sex_eligibility":     sex_eligibility,
        "is_pediatric":        is_pediatric,
        "inclusion_criteria":  inclusion_criteria,
        "exclusion_criteria":  exclusion_criteria,
        "pi_name":             pi_name,
        "pi_email":            pi_email,
        "start_date":          start_date,
        "primary_completion":  primary_completion,
        "study_completion":    study_completion,
        "first_posted":        first_posted,
        "last_updated":        last_updated,
        "primary_endpoints":   primary_endpoints,
        "secondary_endpoints": secondary_endpoints,
        "epro_ecoa":           epro_ecoa,
        "digital_biomarkers":  digital_biomarkers,
        "dct_elements":        dct_elements,
        "brief_summary":       brief_summary or None,
        "has_news":            0,
        "ingested_at":         datetime.utcnow().isoformat(),
        "registry_sources":    json.dumps(["ClinicalTrials.gov"]),
        "all_registry_ids":    json.dumps([nct_id]) if nct_id else json.dumps([]),
        "euct_id":             None,
        "eudract_number":      None,
        "eu_member_states":    None,
    }


def fetch_condition(condition):
    studies = []
    params = {"query.cond": condition, "pageSize": 200, "format": "json"}
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_cond = re.sub(r"[^a-zA-Z0-9]", "_", condition)
    page_num = 0

    while True:
        try:
            resp = requests.get(BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            print(f"  [ERROR] Condition '{condition}' page {page_num}: {e}")
            break

        data = resp.json()
        snapshot_path = os.path.join(
            SNAPSHOT_DIR, f"ct_{safe_cond}_{timestamp}_p{page_num}.json"
        )
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(data, f)

        for s in data.get("studies", []):
            parsed = parse_study(s, snapshot_path)
            studies.append(parsed)

        next_token = data.get("nextPageToken")
        if not next_token:
            break
        params["pageToken"] = next_token
        page_num += 1

    return studies


def pull_all():
    conn = get_connection()
    seen_ids = set()

    for condition in CONDITIONS:
        print(f"  Fetching '{condition}'...")
        studies = fetch_condition(condition)
        new_count = 0
        for trial in studies:
            nct_id = trial.get("id")
            if not nct_id or nct_id in seen_ids:
                continue
            seen_ids.add(nct_id)
            conn.execute("""
                INSERT OR REPLACE INTO trials (
                    id, title_brief, title_official, registry_id, source_url, raw_snapshot_path,
                    status, phase, study_type,
                    sponsor, sponsor_type, cro_named, lead_country, countries, num_sites,
                    randomized, masking, num_arms,
                    conditions, interventions, therapeutic_area, mesh_terms,
                    enrollment, min_age, max_age, sex_eligibility, is_pediatric,
                    inclusion_criteria, exclusion_criteria,
                    pi_name, pi_email,
                    start_date, primary_completion, study_completion, first_posted, last_updated,
                    primary_endpoints, secondary_endpoints,
                    epro_ecoa, digital_biomarkers, dct_elements,
                    brief_summary, has_news, ingested_at,
                    registry_sources, all_registry_ids, euct_id, eudract_number, eu_member_states
                ) VALUES (
                    :id, :title_brief, :title_official, :registry_id, :source_url, :raw_snapshot_path,
                    :status, :phase, :study_type,
                    :sponsor, :sponsor_type, :cro_named, :lead_country, :countries, :num_sites,
                    :randomized, :masking, :num_arms,
                    :conditions, :interventions, :therapeutic_area, :mesh_terms,
                    :enrollment, :min_age, :max_age, :sex_eligibility, :is_pediatric,
                    :inclusion_criteria, :exclusion_criteria,
                    :pi_name, :pi_email,
                    :start_date, :primary_completion, :study_completion, :first_posted, :last_updated,
                    :primary_endpoints, :secondary_endpoints,
                    :epro_ecoa, :digital_biomarkers, :dct_elements,
                    :brief_summary, :has_news, :ingested_at,
                    :registry_sources, :all_registry_ids, :euct_id, :eudract_number, :eu_member_states
                )
            """, trial)
            new_count += 1
        conn.commit()
        print(f"    → {new_count} trials upserted")

    conn.close()
    print(f"  Total unique trials this run: {len(seen_ids)}")
