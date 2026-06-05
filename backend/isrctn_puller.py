import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from db import get_connection
from registry_utils import (
    extract_nct, is_relevant, merge_or_insert,
    normalize_phase, normalize_status, snapshots_enabled,
)

SEARCH_TERMS = [
    "obesity", "semaglutide", "tirzepatide", "liraglutide",
    "GLP-1", "type 2 diabetes", "heart failure", "metabolic",
]
API_URL = "https://www.isrctn.com/api/query/format/who"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")

_INVALID_XML_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')


def _save_snapshot(term, page, text):
    if not snapshots_enabled():
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"isrctn_{safe_term}_p{page}_{ts}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _ftext(el, tag):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None else ""


def _parse_isrctn_date(raw):
    """Convert DD/MM/YYYY to YYYY-MM-DD."""
    if not raw:
        return None
    m = re.match(r'(\d{1,2})/(\d{1,2})/(\d{4})', raw)
    if m:
        return f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"
    return raw


def _process_trial(trial_el):
    main = trial_el.find("main") or trial_el
    isrctn_id = _ftext(main, "trial_id")
    if not isrctn_id:
        return None

    title = _ftext(main, "public_title") or _ftext(main, "scientific_title")
    condition = _ftext(main, "hc_freetext")
    if not is_relevant(f"{title} {condition}"):
        return None

    countries = []
    countries_el = trial_el.find("countries")
    if countries_el is not None:
        countries = [c.text.strip() for c in countries_el.findall("country2") if c.text]

    nct_ref = None
    secondary_el = trial_el.find("secondary_ids")
    if secondary_el is not None:
        for sid in secondary_el.findall("secondary_id"):
            nct = extract_nct(_ftext(sid, "identifier"))
            if nct:
                nct_ref = nct
                break

    phase_raw = _ftext(main, "phase")
    target_raw = _ftext(main, "target_size")
    enrollment = None
    try:
        enrollment = int(re.sub(r"[^\d]", "", target_raw)) if target_raw else None
    except ValueError:
        pass

    prim_el = trial_el.find("primary_outcome")
    primary_outcome = None
    if prim_el is not None:
        outcomes = [o.text.strip() for o in prim_el.findall("prim_outcome")
                    if o.text and o.text.strip()]
        primary_outcome = "; ".join(outcomes) if outcomes else None

    start_date = _parse_isrctn_date(_ftext(main, "date_enrolment"))
    completion_date = _parse_isrctn_date(_ftext(main, "results_date_completed"))

    record = {
        "title_brief": title[:500] or None,
        "status": normalize_status(_ftext(main, "recruitment_status")),
        "phase": normalize_phase(phase_raw),
        "conditions": json.dumps([condition]) if condition else json.dumps([]),
        "sponsor": _ftext(main, "primary_sponsor") or None,
        "start_date": start_date,
        "primary_completion": completion_date,
        "enrollment": enrollment,
        "countries": json.dumps(countries),
        "primary_endpoints": primary_outcome,
        "source_url": _ftext(main, "url") or f"https://www.isrctn.com/{isrctn_id}",
    }
    if nct_ref:
        record["_nct_cross_ref"] = nct_ref
    return isrctn_id, record


def pull_all_isrctn():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    conn = get_connection()
    seen_ids = set()

    PAGE_SIZE = 100
    MAX_PAGES = 100  # hard cap so an API that ignores `page` can't loop forever

    try:
        for term in SEARCH_TERMS:
            page = 1
            while page <= MAX_PAGES:
                try:
                    resp = session.get(
                        API_URL,
                        params={"q": term, "page": page, "pageSize": PAGE_SIZE},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    text = resp.text
                except Exception as e:
                    print(f"  [WARN] ISRCTN fetch failed (term={term!r}, page={page}): {e}")
                    break

                _save_snapshot(term, page, text)

                text_clean = _INVALID_XML_CHARS.sub('', text)
                try:
                    root = ET.fromstring(text_clean)
                except ET.ParseError as e:
                    print(f"  [WARN] ISRCTN XML parse error (term={term!r}, p{page}): {e}")
                    break

                trials = root.findall("trial")
                if not trials:
                    break

                new_this_page = 0
                for trial_el in trials:
                    result = _process_trial(trial_el)
                    if result is None:
                        continue
                    isrctn_id, record = result
                    if isrctn_id in seen_ids:
                        continue
                    seen_ids.add(isrctn_id)
                    new_this_page += 1
                    try:
                        merge_or_insert(record, "ISRCTN", isrctn_id,
                                        "isrctn_id", conn=conn)
                    except Exception as e:
                        print(f"  [WARN] ISRCTN merge error for {isrctn_id}: {e}")

                conn.commit()

                # Stop on a short (last) page, or when a full page adds no new
                # records — the latter means the API is repeating results
                # (ignoring `page`), which previously caused an infinite loop.
                if len(trials) < PAGE_SIZE or new_this_page == 0:
                    break
                page += 1
                time.sleep(0.4)
            else:
                print(f"  [WARN] ISRCTN hit page cap ({MAX_PAGES}) for term={term!r}")

            time.sleep(0.5)
    finally:
        conn.commit()
        conn.close()

    print(f"  ISRCTN: processed {len(seen_ids)} unique records")
