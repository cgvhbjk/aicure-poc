import csv
import io
import os
import time
from datetime import datetime, date
from urllib.parse import quote

import requests

from grant_utils import (
    build_grant_record,
    classify_area, upsert_grant, is_human_subjects, EUR_TO_USD,
    extract_phase, extract_conditions, extract_interventions,
)
from registry_utils import extract_nct
from db import get_connection

SEARCH_TERMS = [
    "obesity", "GLP-1", "semaglutide", "tirzepatide", "type 2 diabetes",
    "heart failure", "atrial fibrillation", "metabolic", "NASH", "adherence",
]

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "grants"
)


def _save_snapshot(term: str, page_num: int, text: str):
    if os.environ.get("AICURE_SNAPSHOTS") != "1":
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"cordis_{safe_term}_p{page_num}_{ts}.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _fetch_page(session, base_q: str, term: str, page_num: int):
    url = (
        f"https://cordis.europa.eu/search"
        f"?q={base_q}&format=csv&num=100&p={page_num}"
    )
    for attempt in range(3):
        try:
            resp = session.get(url, timeout=45, headers={"Accept": "text/csv,*/*"})
            resp.raise_for_status()
            text = resp.text.lstrip('﻿')  # strip BOM
            if text.strip().startswith('<'):
                time.sleep(3.0 * (attempt + 1))
                continue
            _save_snapshot(term, page_num, text)
            reader = csv.DictReader(io.StringIO(text), delimiter=';')
            return list(reader)
        except Exception as e:
            print(f"  [WARN] CORDIS fetch attempt {attempt+1} failed (term={term!r}, p={page_num}): {e}")
            time.sleep(3.0 * (attempt + 1))
    return None


def pull_cordis():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    seen_ids: set = set()
    total_inserted = 0
    conn = get_connection()  # one connection for the whole pull; commit per page

    for term in SEARCH_TERMS:
        q_term = f'"{term}"' if ('-' in term or ' ' in term) else term
        base_q = quote(q_term)

        for page_num in range(1, 11):
            rows = _fetch_page(session, base_q, term, page_num)

            if rows is None:
                print(f"  [WARN] CORDIS: skipping term={term!r} after 3 failed attempts")
                break

            if not rows:
                break

            for row in rows:
                try:
                    proj_id = (row.get("ID") or "").strip()
                    if not proj_id or proj_id in seen_ids:
                        continue
                    seen_ids.add(proj_id)

                    title = row.get("Title") or ""
                    teaser = row.get("Teaser") or ""
                    objective = row.get("objective") or row.get("Objective") or ""
                    combined = f"{title} {teaser} {objective}"

                    raw_cost = row.get("Total cost") or row.get("totalCost") or ""
                    try:
                        cost_eur = float(str(raw_cost).replace(",", "").replace(" ", ""))
                        amount_eur = cost_eur
                        amount_usd = int(cost_eur * EUR_TO_USD)
                    except (ValueError, AttributeError):
                        amount_eur = None
                        amount_usd = None

                    start = row.get("Project start date") or row.get("startDate") or ""
                    end = row.get("Project end date") or row.get("endDate") or ""
                    try:
                        end_d = date.fromisoformat(end[:10]) if end else None
                        status = "COMPLETED" if end_d and end_d < date.today() else "ACTIVE"
                    except ValueError:
                        status = "ACTIVE"

                    fiscal_year = int(start[:4]) if start and start[:4].isdigit() else None

                    programme = row.get("programme") or row.get("Programme") or ""
                    call = row.get("call") or row.get("Call") or ""
                    agency_division = f"{programme} / {call}".strip(" /") if call else programme or None

                    source_url = row.get("URL") or f"https://cordis.europa.eu/project/id/{proj_id}"

                    record = {
                        "id": f"CORDIS-{proj_id}",
                        "source": "CORDIS",
                        "award_id": proj_id,
                        "title": title[:500],
                        "abstract": (teaser or objective)[:5000],
                        "org_type": "ACADEMIC",
                        "amount_original": amount_eur,
                        "currency": "EUR",
                        "amount_usd": amount_usd,
                        "start_date": start or None,
                        "end_date": end or None,
                        "fiscal_year": fiscal_year,
                        "status": status,
                        "sponsor_funder": "European Commission / Horizon",
                        "agency_division": agency_division,
                        "activity_code": row.get("frameworkProgramme") or row.get("FrameworkProgramme") or None,
                        "project_acronym": row.get("acronym") or row.get("Acronym") or None,
                        "research_type": row.get("legalBasis") or row.get("LegalBasis") or None,
                        "source_url": source_url,
                    }
                    upsert_grant(build_grant_record(combined, **record), conn)
                    total_inserted += 1
                except Exception as e:
                    print(f"  [WARN] CORDIS record error: {e}")

            conn.commit()
            if len(rows) < 100:
                break

            time.sleep(1.0)

        time.sleep(1.0)

    conn.close()
    print(f"  CORDIS: {total_inserted} grants inserted")
