import csv
import io
import json
import os
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
CSV_URL = "https://www.trialregister.nl/export/export.csv"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")


def _save_snapshot(term: str, content: str):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"ntr_{safe_term}_{ts}.csv")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _process_row(row: dict):
    ntr_id = (
        row.get("Trial ID") or row.get("NTR number") or
        row.get("TrialID") or row.get("trialID") or ""
    ).strip()
    if not ntr_id:
        return

    title = row.get("Public title") or row.get("Scientific title") or ""
    condition = row.get("Health condition(s)") or row.get("Health conditions") or ""

    if not is_relevant(f"{title} {condition}"):
        return

    # Look for NCT cross-reference in secondary IDs
    secondary = row.get("Secondary IDs") or row.get("Secondary ID") or ""
    nct_ref = extract_nct(secondary)

    countries_raw = row.get("Countries of recruitment") or ""
    countries = [c.strip() for c in countries_raw.split(",") if c.strip()]

    record = {
        "title_brief": title[:500] or None,
        "title_official": (row.get("Scientific title") or "")[:500] or None,
        "status": normalize_status(row.get("Recruitment status") or row.get("Status") or ""),
        "phase": normalize_phase(row.get("Phase") or ""),
        "conditions": json.dumps([condition]) if condition else json.dumps([]),
        "sponsor": row.get("Primary sponsor") or row.get("Sponsor") or None,
        "start_date": row.get("Date of first enrollment") or row.get("Start date") or None,
        "enrollment": _safe_int(row.get("Target sample size") or row.get("Enrollment")),
        "countries": json.dumps(countries),
        "primary_endpoints": row.get("Primary outcome(s)") or row.get("Primary outcomes") or None,
        "source_url": f"https://www.trialregister.nl/trial/{ntr_id}",
    }
    if nct_ref:
        record["_nct_cross_ref"] = nct_ref

    try:
        merge_or_insert(record, "NTR", ntr_id, "ntr_id")
    except Exception as e:
        print(f"  [WARN] NTR merge error for {ntr_id}: {e}")


def _safe_int(val):
    try:
        return int(str(val).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def pull_all_ntr():
    print('  NTR: skipping — domain defunct, data covered by CTIS')
    return
    seen_ids = set()
    for term in SEARCH_TERMS:
        try:
            resp = requests.get(
                CSV_URL,
                params={"q": term},
                timeout=30,
                headers={"Accept": "text/csv,*/*"},
            )
            resp.raise_for_status()
            text = resp.text
        except Exception as e:
            print(f"  [WARN] NTR fetch failed (term={term!r}): {e}")
            time.sleep(0.5)
            continue

        _save_snapshot(term, text)

        try:
            reader = csv.DictReader(io.StringIO(text))
            for row in reader:
                ntr_id = (
                    row.get("Trial ID") or row.get("NTR number") or
                    row.get("TrialID") or ""
                ).strip()
                if ntr_id and ntr_id not in seen_ids:
                    seen_ids.add(ntr_id)
                    _process_row(row)
        except Exception as e:
            print(f"  [WARN] NTR CSV parse failed (term={term!r}): {e}")

        time.sleep(0.5)

    print(f"  NTR: processed {len(seen_ids)} unique records")
