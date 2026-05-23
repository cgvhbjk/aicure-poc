import json
import os
import re
import time
from datetime import datetime

import requests

from registry_utils import (
    extract_nct, is_relevant, merge_or_insert,
    normalize_phase, normalize_status,
)

SEARCH_TERMS = [
    "obesity", "semaglutide", "tirzepatide", "GLP-1",
    "type 2 diabetes", "heart failure", "metabolic",
]
API_ENDPOINTS = [
    "https://jrct.niph.go.jp/en-api/trials",
    "https://jrct.niph.go.jp/api/trials",
]
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")


def _save_snapshot(term: str, page: int, data: dict):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"jrct_{safe_term}_p{page}_{ts}.json")
    with open(path, "w") as f:
        json.dump(data, f)


def _process_item(item: dict):
    jrct_id = (
        item.get("jRCTId") or item.get("id") or
        item.get("jrctId") or item.get("trialId") or ""
    ).strip()
    if not jrct_id:
        return

    title = (
        item.get("JapaneseTitleInEnglish") or item.get("title") or
        item.get("Title") or item.get("publicTitle") or ""
    )
    condition = (
        item.get("TargetDisease") or item.get("targetDisease") or
        item.get("condition") or item.get("healthCondition") or ""
    )

    if not is_relevant(f"{title} {condition}"):
        return

    # Check for NCT cross-reference
    nct_ref = None
    for key in ("OtherID", "otherId", "SecondaryID", "secondaryId", "otherIdentifier"):
        val = item.get(key) or ""
        nct = extract_nct(str(val))
        if nct:
            nct_ref = nct
            break

    countries_raw = item.get("Countries") or item.get("countries") or "Japan"
    if isinstance(countries_raw, list):
        countries = countries_raw
    else:
        countries = [c.strip() for c in str(countries_raw).replace(";", ",").split(",") if c.strip()]

    enrollment_raw = item.get("PlannedNumberOfSubjects") or item.get("plannedSubjects") or item.get("targetEnrollment")
    enrollment = _safe_int(enrollment_raw)

    record = {
        "title_brief": title[:500] or None,
        "status": normalize_status(item.get("RecruitmentStatus") or item.get("recruitmentStatus") or ""),
        "phase": normalize_phase(item.get("Phase") or item.get("phase") or ""),
        "conditions": json.dumps([condition]) if condition else json.dumps([]),
        "sponsor": item.get("SponsorOrganization") or item.get("sponsorOrganization") or item.get("sponsor") or None,
        "start_date": item.get("StartDate") or item.get("startDate") or None,
        "enrollment": enrollment,
        "countries": json.dumps(countries),
        "primary_endpoints": item.get("PrimaryOutcome") or item.get("primaryOutcome") or None,
        "source_url": f"https://jrct.niph.go.jp/en-latest-data/{jrct_id}",
    }
    if nct_ref:
        record["_nct_cross_ref"] = nct_ref

    try:
        merge_or_insert(record, "jRCT", jrct_id, "jrct_id")
    except Exception as e:
        print(f"  [WARN] jRCT merge error for {jrct_id}: {e}")


def _safe_int(val):
    try:
        return int(re.sub(r"[^\d]", "", str(val))) if val else None
    except ValueError:
        return None


def pull_all_jrct():
    print('  jRCT: skipping — API endpoints unreachable')
    return
    # Try each API endpoint until one works
    working_endpoint = None
    for endpoint in API_ENDPOINTS:
        try:
            resp = requests.get(
                endpoint,
                params={"query": "obesity", "page": 1, "limit": 1, "lang": "en"},
                timeout=15,
            )
            if resp.status_code == 200:
                working_endpoint = endpoint
                break
        except Exception:
            continue

    if not working_endpoint:
        print("  [WARN] jRCT: no working API endpoint found, skipping")
        return

    seen_ids = set()
    for term in SEARCH_TERMS:
        page = 1
        while True:
            try:
                resp = requests.get(
                    working_endpoint,
                    params={"query": term, "page": page, "limit": 100, "lang": "en"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  [WARN] jRCT fetch failed (term={term!r}, page={page}): {e}")
                break

            _save_snapshot(term, page, data)

            # Handle both list response and wrapped response
            if isinstance(data, list):
                items = data
            else:
                items = data.get("trials") or data.get("results") or data.get("content") or []

            if not items:
                break

            for item in items:
                jrct_id = (
                    item.get("jRCTId") or item.get("id") or
                    item.get("jrctId") or ""
                ).strip()
                if jrct_id and jrct_id not in seen_ids:
                    seen_ids.add(jrct_id)
                    _process_item(item)

            if len(items) < 100:
                break
            page += 1
            time.sleep(0.5)

    print(f"  jRCT: processed {len(seen_ids)} unique records")
