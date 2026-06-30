import os
import re
import time
from datetime import datetime
from urllib.parse import urljoin

import requests

from grant_utils import (
    build_grant_record, is_medical, upsert_grant,
)
from db import get_connection

# AHA Crossref funder DOI — free, no registration needed
AHA_FUNDER_DOI = "10.13039/100000968"
CROSSREF_URL = "https://api.crossref.org/funders/{funder}/works"

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "grants"
)


def _first_author(authors: list) -> str:
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


def pull_aha():
    session = requests.Session()
    # Polite pool: faster rate limits when you identify yourself
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
                CROSSREF_URL.format(funder=AHA_FUNDER_DOI),
                params={"rows": 1000, "cursor": cursor, "select": (
                    "DOI,title,author,published,published-print,"
                    "funder,abstract,URL,container-title"
                )},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("message", {})
        except Exception as e:
            print(f"  [WARN] AHA Crossref fetch failed: {e}")
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
                # Strip JATS XML tags that Crossref sometimes includes
                abstract = re.sub(r"<[^>]+>", " ", abstract).strip()
                combined = f"{title} {abstract}"

                if not is_medical(combined):
                    continue

                # Extract grant numbers from funder metadata
                funders = item.get("funder") or []
                award_nums = []
                for f in funders:
                    if AHA_FUNDER_DOI in (f.get("DOI") or ""):
                        award_nums.extend(f.get("award") or [])

                # One record per unique award number; fall back to DOI if none
                keys = award_nums if award_nums else [doi]
                for award_id in keys:
                    if award_id in seen_awards:
                        continue
                    seen_awards.add(award_id)

                    year = _pub_year(item)
                    slug = re.sub(r"[^a-z0-9]+", "-", award_id.lower())[:80].strip("-")

                    record = {
                        "id": f"AHA-{slug}",
                        "source": "AHA",
                        "award_id": award_id,
                        "title": title[:500],
                        "abstract": abstract[:5000],
                        "org_type": "ACADEMIC",
                        "pi_name": _first_author(item.get("author") or []),
                        "sponsor_funder": "American Heart Association",
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
                print(f"  [WARN] AHA record error: {e}")

        conn.commit()
        next_cursor = data.get("next-cursor")
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(0.1)

    conn.close()
    print(f"  AHA: {total_inserted} grants inserted")
