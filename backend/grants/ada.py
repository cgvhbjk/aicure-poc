import os
import re
import time
from datetime import datetime

import requests

from grant_utils import (
    build_grant_record,
    is_medical, is_human_subjects, classify_area, upsert_grant,
    extract_phase, extract_conditions, extract_interventions,
)
from registry_utils import extract_nct
from db import get_connection

# ADA Crossref funder DOI — free, no registration needed
ADA_FUNDER_DOI = "10.13039/100000041"
CROSSREF_URL = "https://api.crossref.org/funders/{funder}/works"


def _first_author(authors: list):
    if not authors:
        return None
    a = authors[0]
    given = a.get("given", "")
    family = a.get("family", "")
    return f"{given} {family}".strip() or None


def _pub_year(item: dict):
    parts = (item.get("published") or item.get("published-print") or {}).get("date-parts") or []
    if parts and parts[0]:
        return parts[0][0]
    return None


def pull_ada():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "AiCurePOC/1.0 (mailto:admin@aicure.com)",
    })

    seen_awards: set = set()
    total_inserted = 0
    cursor = "*"
    conn = get_connection()  # one connection for the whole pull; commit per page

    while True:
        try:
            resp = session.get(
                CROSSREF_URL.format(funder=ADA_FUNDER_DOI),
                params={"rows": 1000, "cursor": cursor, "select": (
                    "DOI,title,author,published,published-print,"
                    "funder,abstract,URL,container-title"
                )},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("message", {})
        except Exception as e:
            print(f"  [WARN] ADA Crossref fetch failed: {e}")
            break

        items = data.get("items") or []
        if not items:
            break

        for item in items:
            try:
                doi = item.get("DOI") or ""
                titles = item.get("title") or []
                title = titles[0] if titles else doi
                abstract = item.get("abstract") or ""
                abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
                combined = f"{title} {abstract}"

                if not is_medical(combined):
                    continue

                funders = item.get("funder") or []
                award_nums = []
                for f in funders:
                    if ADA_FUNDER_DOI in (f.get("DOI") or ""):
                        award_nums.extend(f.get("award") or [])

                keys = award_nums if award_nums else [doi]
                for award_id in keys:
                    if award_id in seen_awards:
                        continue
                    seen_awards.add(award_id)

                    year = _pub_year(item)
                    slug = re.sub(r"[^a-z0-9]+", "-", award_id.lower())[:80].strip("-")

                    record = {
                        "id": f"ADA-{slug}",
                        "source": "ADA",
                        "award_id": award_id,
                        "title": title[:500],
                        "abstract": abstract[:5000],
                        "org_type": "ACADEMIC",
                        "pi_name": _first_author(item.get("author") or []),
                        "sponsor_funder": "American Diabetes Association",
                        "currency": "USD",
                        "fiscal_year": year,
                        "award_date": f"{year}-01-01" if year else None,
                        "country": "US",
                        "status": "COMPLETED" if year and year < 2024 else "ACTIVE",
                        "source_url": item.get("URL") or f"https://doi.org/{doi}",
                    }
                    upsert_grant(build_grant_record(combined, **record), conn)
                    total_inserted += 1
            except Exception as e:
                print(f"  [WARN] ADA record error: {e}")

        conn.commit()
        next_cursor = data.get("next-cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(0.1)

    conn.close()
    print(f"  ADA: {total_inserted} grants inserted")
