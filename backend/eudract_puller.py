import json
import os
import re
import time
from datetime import datetime

import requests

from db import get_connection
from registry_utils import (
    extract_nct, is_relevant, merge_or_insert,
    normalize_phase, normalize_status, snapshots_enabled,
)

SEARCH_TERMS = [
    "obesity", "semaglutide", "tirzepatide", "liraglutide",
    "GLP-1", "metabolic syndrome", "type 2 diabetes mellitus",
]
DOWNLOAD_URL = "https://www.clinicaltrialsregister.eu/ctr-search/rest/download/summary"
MAX_PAGES_PER_TERM = 30  # 30 * 20 = 600 results max per term
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")

EUDRACT_ID_RE = re.compile(r'^20\d\d-\d{6}-\d\d$')


def _save_snapshot(term, page, text):
    if not snapshots_enabled():
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"eudract_{safe_term}_p{page}_{ts}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _parse_block(block):
    fields = {}
    for line in block.splitlines():
        line = line.strip()
        if ':' in line:
            key, _, val = line.partition(':')
            fields[key.strip()] = val.strip()
    return fields


def _infer_status(protocol_raw):
    statuses = re.findall(r'\(([^)]+)\)', protocol_raw)
    status_set = {s.lower() for s in statuses}
    if any("ongoing" in s or "recruiting" in s for s in status_set):
        return "Recruiting"
    if any("transitioned" in s for s in status_set):
        return "Active, not recruiting"
    if status_set and all("completed" in s for s in status_set):
        return "Completed"
    return ""


def _process_block(block):
    fields = _parse_block(block)
    eudract_id = fields.get("EudraCT Number", "").strip()
    if not eudract_id or not EUDRACT_ID_RE.match(eudract_id):
        return None

    title = fields.get("Full Title", "")
    condition = fields.get("Medical condition", "")
    if not is_relevant(f"{title} {condition}"):
        return None

    protocol_raw = fields.get("Trial protocol", "")
    country_codes = list(dict.fromkeys(re.findall(r'\b([A-Z]{2})\(', protocol_raw)))
    sponsor = fields.get("Sponsor Name") or None
    start_date = fields.get("Start Date") or None

    record = {
        "title_brief": title[:500] or None,
        "status": normalize_status(_infer_status(protocol_raw)),
        "phase": None,
        "conditions": json.dumps([condition]) if condition else json.dumps([]),
        "sponsor": sponsor,
        "start_date": start_date,
        "primary_completion": None,
        "enrollment": None,
        "countries": json.dumps(country_codes),
        "primary_endpoints": None,
        "source_url": fields.get("Link") or (
            f"https://www.clinicaltrialsregister.eu/ctr-search/search?query=eudract_number:{eudract_id}"
        ),
    }
    return eudract_id, record


def pull_all_eudract():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    conn = get_connection()
    seen_ids = set()

    try:
        for term in SEARCH_TERMS:
            for page in range(1, MAX_PAGES_PER_TERM + 1):
                try:
                    resp = session.get(
                        DOWNLOAD_URL,
                        params={"query": term, "mode": "current_page", "page": page},
                        timeout=20,
                    )
                    resp.raise_for_status()
                    text = resp.text
                except Exception as e:
                    print(f"  [WARN] EudraCT fetch failed (term={term!r}, page={page}): {e}")
                    break

                _save_snapshot(term, page, text)

                blocks = re.split(r'\r?\n\r?\n(?=EudraCT Number:)', text.strip())
                # Natural termination: empty page or page with no records.
                if not blocks or 'EudraCT Number:' not in blocks[0]:
                    break

                inserted_this_page = 0
                for block in blocks:
                    result = _process_block(block)
                    if result is None:
                        continue
                    eudract_id, record = result
                    if eudract_id in seen_ids:
                        continue
                    seen_ids.add(eudract_id)
                    inserted_this_page += 1
                    try:
                        merge_or_insert(record, "EudraCT", eudract_id,
                                        "eudract_id", conn=conn)
                    except Exception as e:
                        print(f"  [WARN] EudraCT merge error for {eudract_id}: {e}")

                conn.commit()

                # If a full page returned nothing new, assume we've exhausted
                # the relevant records for this term.
                if inserted_this_page == 0:
                    break

                time.sleep(0.4)

            time.sleep(0.5)
    finally:
        conn.commit()
        conn.close()

    print(f"  EudraCT: processed {len(seen_ids)} unique records")
