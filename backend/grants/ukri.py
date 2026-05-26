import json
import os
import time
from datetime import datetime

import requests

from grant_utils import is_medical, classify_area, upsert_grant, GBP_TO_USD
from registry_utils import extract_nct

SEARCH_TERMS = [
    "obesity", "GLP-1", "semaglutide", "tirzepatide", "type 2 diabetes",
    "heart failure", "atrial fibrillation", "metabolic", "NASH", "adherence",
]

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "grants"
)


def _save_snapshot(term: str, page: int, data: dict):
    if os.environ.get("AICURE_SNAPSHOTS") != "1":
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"ukri_{safe_term}_{ts}_p{page}.json")
    with open(path, "w") as f:
        json.dump(data, f)


def pull_ukri():
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "AiCurePOC/1.0 (research use)",
    })

    total_inserted = 0

    for term in SEARCH_TERMS:
        page = 1
        while True:
            try:
                resp = session.get(
                    "https://gtr.ukri.org/gtr/api/projects",
                    params={"q": term, "s": 100, "p": page, "sf": "pro.sd", "so": "D"},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"  [WARN] UKRI fetch failed (term={term!r}, page={page}): {e}")
                break

            _save_snapshot(term, page, data)

            projects = data.get("project") or []
            total_pages = data.get("totalPages", 1)

            for proj in projects:
                try:
                    proj_id = proj.get("id") or ""
                    title = proj.get("title") or ""
                    abstract = proj.get("abstractText") or ""
                    combined = f"{title} {abstract}"

                    if not is_medical(combined):
                        continue

                    fund = proj.get("fund") or {}
                    funder_info = fund.get("funder") or {}
                    value_gbp = fund.get("valuePounds")
                    amount_usd = int(float(value_gbp) * GBP_TO_USD) if value_gbp else None

                    nct = extract_nct(combined)
                    record = {
                        "id": f"UKRI-{proj_id}",
                        "source": "UKRI",
                        "award_id": proj_id,
                        "title": title[:500],
                        "abstract": abstract[:5000],
                        "organization": proj.get("leadOrganisationDepartment"),
                        "amount_original": float(value_gbp) if value_gbp else None,
                        "currency": "GBP",
                        "amount_usd": amount_usd,
                        "start_date": fund.get("start"),
                        "end_date": fund.get("end"),
                        "status": "ACTIVE" if proj.get("status") in ("Active", "live") else "COMPLETED",
                        "sponsor_funder": funder_info.get("name"),
                        "country": "UK",
                        "therapeutic_area": classify_area(combined),
                        "source_url": f"https://gtr.ukri.org/projects?ref={proj_id}",
                        "linked_trial_id": nct,
                        "has_trial_link": 1 if nct else 0,
                    }
                    upsert_grant(record)
                    total_inserted += 1
                except Exception as e:
                    print(f"  [WARN] UKRI record error: {e}")

            if page >= total_pages or not projects:
                break
            page += 1

    print(f"  UKRI: {total_inserted} grants inserted")
