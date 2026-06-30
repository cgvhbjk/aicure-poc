import json
import os
import time
from datetime import datetime

import requests

from grant_utils import (
    build_grant_record, is_medical, upsert_grant,
)
from db import get_connection

SEARCH_BODY = {
    "filters": {
        "award_type_codes": ["02", "03", "04", "05"],
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


def _infer_org_type(name: str) -> str:
    if not name:
        return "OTHER"
    n = name.lower()
    if any(k in n for k in ["university", "college", "institute", "hospital", "school of"]):
        return "ACADEMIC"
    if any(n.endswith(s) for s in [" inc", " inc.", " llc", " corp", " corp.", " ltd", " ltd."]):
        return "INDUSTRY"
    if "," in name:
        last = name.split(",")[-1].strip().lower()
        if last in ("inc", "inc.", "llc", "corp", "corp.", "ltd", "ltd."):
            return "INDUSTRY"
    return "OTHER"


def pull_usaspending():
    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    page = 1
    total_inserted = 0
    conn = get_connection()  # one connection for the whole pull; commit per page

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

                start_date = award.get("Start Date") or ""
                fiscal_year = int(start_date[:4]) if start_date and start_date[:4].isdigit() else None

                recipient_name = award.get("Recipient Name") or ""
                amount = int(award.get("Award Amount") or 0) or None

                record = {
                    "id": f"USA-{award_id}",
                    "source": "USASPENDING",
                    "award_id": award_id,
                    "title": title,
                    "abstract": description[:5000],
                    "organization": recipient_name,
                    "org_type": _infer_org_type(recipient_name),
                    "amount_usd": amount,
                    "amount_original": amount,
                    "currency": "USD",
                    "start_date": start_date or None,
                    "end_date": award.get("End Date"),
                    "sponsor_funder": funder,
                    "agency_division": sub_agency or None,
                    "fiscal_year": fiscal_year,
                    "status": "ACTIVE",
                    "country": "US",
                    "source_url": f"https://www.usaspending.gov/award/{award_id}",
                }
                upsert_grant(build_grant_record(combined, **record), conn)
                total_inserted += 1
            except Exception as e:
                print(f"  [WARN] USASpending record error ({award.get('Award ID')}): {e}")

        conn.commit()
        has_next = data.get("page_metadata", {}).get("hasNext", False)
        if not has_next or not results:
            break

        page += 1

    conn.close()
    print(f"  USASpending: {total_inserted} grants inserted")
