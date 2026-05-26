import json
import os
import time
from datetime import datetime

import requests

from grant_utils import is_medical, classify_area, upsert_grant
from registry_utils import extract_nct

SEARCH_BODY = {
    "filters": {
        "award_type_codes": ["02", "03", "04", "05"],
        "naics_codes": ["541714", "541715", "621111", "621999"],
        "keywords": ["clinical trial", "obesity", "GLP-1", "diabetes", "cardiac adherence"],
        "time_period": [{"start_date": "2020-01-01", "end_date": "2099-12-31"}],
        "award_amounts": [{"lower_bound": 100000}],
    },
    "fields": [
        "Award ID", "Recipient Name", "Award Amount", "Total Outlays",
        "Description", "Start Date", "End Date", "Awarding Agency",
        "Awarding Sub Agency", "Award Type",
    ],
    "limit": 100,
    "page": 1,
    "sort": "Award Amount",
    "order": "desc",
}


def pull_usaspending():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    page = 1
    total_inserted = 0

    while True:
        body = dict(SEARCH_BODY)
        body["page"] = page
        try:
            resp = session.post(
                "https://api.usaspending.gov/api/v2/search/spending_by_award/",
                json=body,
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"  [WARN] USASpending fetch failed (page={page}): {e}")
            break

        results = data.get("results") or []

        for award in results:
            try:
                award_id = award.get("Award ID") or ""
                description = award.get("Description") or ""
                title = description[:500]
                combined = f"{title} {description}"

                if not is_medical(combined):
                    continue

                agency = award.get("Awarding Agency") or ""
                sub_agency = award.get("Awarding Sub Agency") or ""
                funder = f"{agency} / {sub_agency}".strip(" /") if sub_agency else agency
                nct = extract_nct(combined)

                record = {
                    "id": f"USA-{award_id}",
                    "source": "USASPENDING",
                    "award_id": award_id,
                    "title": title,
                    "abstract": description[:5000],
                    "organization": award.get("Recipient Name"),
                    "amount_usd": int(award.get("Award Amount") or 0) or None,
                    "start_date": award.get("Start Date"),
                    "end_date": award.get("End Date"),
                    "sponsor_funder": funder,
                    "status": "ACTIVE",
                    "country": "US",
                    "source_url": f"https://www.usaspending.gov/award/{award_id}",
                    "therapeutic_area": classify_area(combined),
                    "linked_trial_id": nct,
                    "has_trial_link": 1 if nct else 0,
                }
                upsert_grant(record)
                total_inserted += 1
            except Exception as e:
                print(f"  [WARN] USASpending record error: {e}")

        has_next = data.get("page_metadata", {}).get("hasNext", False)
        if not has_next or not results:
            break

        page += 1

    print(f"  USASpending: {total_inserted} grants inserted")
