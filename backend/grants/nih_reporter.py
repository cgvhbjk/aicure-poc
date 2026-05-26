import json
import os
import time
from datetime import datetime

import requests

from grant_utils import is_medical, classify_area, upsert_grant
from registry_utils import extract_nct

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "grants"
)

SEARCH_BODY = {
    "criteria": {
        "advanced_text_search": {
            "operator": "OR",
            "search_field": "terms",
            "search_text": (
                "obesity GLP-1 semaglutide tirzepatide diabetes "
                "cardiac heart failure adherence clinical trial"
            ),
        },
        "activity_codes": ["R01", "R44", "R43", "U01", "P01", "R21", "U54"],
        "is_active": True,
    },
    "limit": 500,
    "offset": 0,
    "sort_field": "award_amount",
    "sort_order": "desc",
}


def _save_snapshot(page: int, data: dict):
    if os.environ.get("AICURE_SNAPSHOTS") != "1":
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    path = os.path.join(SNAPSHOT_DIR, f"nih_reporter_{ts}_p{page}.json")
    with open(path, "w") as f:
        json.dump(data, f)


def pull_nih_reporter():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    offset = 0
    page = 1
    total_inserted = 0

    while True:
        body = dict(SEARCH_BODY)
        body["offset"] = offset
        try:
            resp = session.post(
                "https://api.reporter.nih.gov/v2/projects/search",
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [WARN] NIH RePORTER fetch failed (offset={offset}): {e}")
            break

        _save_snapshot(page, data)

        results = data.get("results") or []
        total = data.get("meta", {}).get("total", 0)

        for proj in results:
            try:
                title = proj.get("project_title") or ""
                abstract = proj.get("abstract_text") or ""
                combined = f"{title} {abstract}"

                if not is_medical(combined):
                    continue

                pis = proj.get("principal_investigators") or []
                pi = pis[0] if pis else {}
                org = proj.get("organization") or {}
                agency = proj.get("agency_ic_admin") or {}

                nct = extract_nct(combined)
                record = {
                    "id": f"NIH-{proj['project_num']}",
                    "source": "NIH_REPORTER",
                    "award_id": proj.get("project_num"),
                    "title": title[:500],
                    "abstract": abstract[:5000],
                    "pi_name": pi.get("full_name"),
                    "pi_email": pi.get("email"),
                    "organization": org.get("org_name"),
                    "org_type": org.get("org_type"),
                    "sponsor_funder": agency.get("abbreviation"),
                    "amount_usd": proj.get("award_amount"),
                    "start_date": proj.get("project_start_date"),
                    "end_date": proj.get("project_end_date"),
                    "award_date": proj.get("award_notice_date"),
                    "status": "ACTIVE" if proj.get("is_active") else "COMPLETED",
                    "therapeutic_area": classify_area(combined),
                    "country": proj.get("org_country", "US"),
                    "source_url": proj.get("project_detail_url"),
                    "linked_trial_id": nct,
                    "has_trial_link": 1 if nct else 0,
                }
                upsert_grant(record)
                total_inserted += 1
            except Exception as e:
                print(f"  [WARN] NIH record error: {e}")

        offset += len(results)
        page += 1

        if offset >= total or not results:
            break

        time.sleep(0.2)

    print(f"  NIH RePORTER: {total_inserted} grants inserted")
