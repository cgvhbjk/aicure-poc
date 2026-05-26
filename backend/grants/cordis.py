import csv
import io
import os
import time
from datetime import datetime
from urllib.parse import quote

import requests

from grant_utils import is_medical, classify_area, upsert_grant, EUR_TO_USD
from registry_utils import extract_nct

SEARCH_TERMS = [
    "obesity", "GLP-1", "semaglutide", "tirzepatide", "type 2 diabetes",
    "heart failure", "atrial fibrillation", "metabolic", "NASH", "adherence",
]

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "grants"
)


def _save_snapshot(term: str, text: str):
    if os.environ.get("AICURE_SNAPSHOTS") != "1":
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"cordis_{safe_term}_{ts}.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def pull_cordis():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    total_inserted = 0

    for term in SEARCH_TERMS:
        url = (
            f"https://cordis.europa.eu/search/result_en?"
            f"q=contenttype%3Dproject+AND+programme%2Fcode%3DHORIZON+AND+({quote(term)})"
            f"&p=1&num=200&srt=Relevance:decreasing&format=csv"
        )
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            print(f"  [WARN] CORDIS fetch failed (term={term!r}): {e}")
            time.sleep(1.0)
            continue

        _save_snapshot(term, text)

        try:
            reader = csv.DictReader(io.StringIO(text))
            rows = list(reader)
        except Exception as e:
            print(f"  [WARN] CORDIS CSV parse failed (term={term!r}): {e}")
            time.sleep(1.0)
            continue

        for row in rows:
            try:
                proj_id = (row.get("id") or "").strip()
                if not proj_id:
                    continue

                title = row.get("title") or ""
                objective = row.get("objective") or ""
                combined = f"{title} {objective}"

                if not is_medical(combined):
                    continue

                raw_cost = row.get("totalCost") or ""
                try:
                    cost_eur = float(raw_cost.replace(",", ""))
                    amount_eur = cost_eur
                    amount_usd = int(cost_eur * EUR_TO_USD)
                except (ValueError, AttributeError):
                    amount_eur = None
                    amount_usd = None

                raw_status = (row.get("status") or "").upper()
                status = "COMPLETED" if "CLOSED" in raw_status else "ACTIVE"

                nct = extract_nct(combined)
                record = {
                    "id": f"CORDIS-{proj_id}",
                    "source": "CORDIS",
                    "award_id": proj_id,
                    "title": title[:500],
                    "abstract": objective[:5000],
                    "organization": row.get("coordinator"),
                    "country": row.get("coordinatorCountry"),
                    "amount_original": amount_eur,
                    "currency": "EUR",
                    "amount_usd": amount_usd,
                    "start_date": row.get("startDate"),
                    "end_date": row.get("endDate"),
                    "status": status,
                    "sponsor_funder": row.get("fundingScheme"),
                    "therapeutic_area": classify_area(combined),
                    "source_url": f"https://cordis.europa.eu/project/id/{proj_id}",
                    "linked_trial_id": nct,
                    "has_trial_link": 1 if nct else 0,
                }
                upsert_grant(record)
                total_inserted += 1
            except Exception as e:
                print(f"  [WARN] CORDIS record error: {e}")

        time.sleep(1.0)

    print(f"  CORDIS: {total_inserted} grants inserted")
