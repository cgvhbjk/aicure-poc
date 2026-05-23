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
    "obesity", "semaglutide", "tirzepatide", "GLP-1",
    "type 2 diabetes", "heart failure", "metabolic",
]
SEARCH_URL = "https://cris.nih.go.kr/cris/search/selectBasic.do"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://cris.nih.go.kr/cris/search/listDetail.do",
    "Accept": "application/xml, text/xml, */*",
}

# Extract English from "Korean(English)" format: "모집 중(Recruiting)" → "Recruiting"
_KO_EN_RE = re.compile(r'\(([A-Za-z][^)]+)\)')

# Strip only XML-1.0-invalid numeric character references (control range);
# leave legitimate refs like &#8217; (right single quote) intact.
_INVALID_XML_REF = re.compile(
    r'&#(?:'
    r'[0-8]|11|12|1[4-9]|2\d|3[01]|'
    r'x0?[0-8]|x0?[bB]|x0?[cC]|x0?[eE]|x0?[fF]|x1[0-9a-fA-F]'
    r');'
)


def _ko_en(val):
    if not val:
        return ""
    m = _KO_EN_RE.search(val)
    return m.group(1).strip() if m else val.strip()


def _safe_int(val):
    try:
        return int(re.sub(r"[^\d]", "", str(val))) if val else None
    except ValueError:
        return None


def _save_snapshot(term, page, content):
    if not snapshots_enabled():
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    safe_term = term.replace(" ", "_").replace("/", "-")
    path = os.path.join(SNAPSHOT_DIR, f"cris_{safe_term}_p{page}_{ts}.xml")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _process_item(item_el, conn):
    def ftext(tag):
        el = item_el.find(tag)
        return (el.text or "").strip() if el is not None else ""

    kct_id = ftext("system_number")
    if not kct_id:
        return

    title = ftext("research_title_en") or ftext("research_title_kr")
    condition = ftext("cp_contents_en") or ftext("cp_contents")

    if not is_relevant(f"{title} {condition}"):
        return

    nct_ref = extract_nct(ftext("research_number"))
    status_raw = ftext("research_step")
    phase_raw = ftext("clinical_step")
    sponsor = ftext("resrc_spp_en") or ftext("resrc_spp")
    enrollment_raw = ftext("target_number")

    record = {
        "title_brief": title[:500] or None,
        "status": normalize_status(_ko_en(status_raw)),
        "phase": normalize_phase(_ko_en(phase_raw)),
        "conditions": json.dumps([condition]) if condition else json.dumps([]),
        "sponsor": sponsor or None,
        "start_date": ftext("study_start_date") or None,
        "primary_completion": ftext("study_complete_date") or None,
        "enrollment": _safe_int(enrollment_raw),
        "countries": json.dumps(["Republic of Korea"]),
        "primary_endpoints": ftext("outcome_en") or ftext("outcome") or None,
        "source_url": f"https://cris.nih.go.kr/cris/search/listDetail.do?seq={ftext('seq')}&locale=en",
    }
    if nct_ref:
        record["_nct_cross_ref"] = nct_ref

    try:
        merge_or_insert(record, "CRIS", kct_id, "cris_id", conn=conn)
    except Exception as e:
        print(f"  [WARN] CRIS merge error for {kct_id}: {e}")


def pull_all_cris():
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        session.get("https://cris.nih.go.kr/cris/index/index.do", timeout=15)
    except Exception:
        pass

    conn = get_connection()
    seen_ids = set()

    try:
        for term in SEARCH_TERMS:
            page = 1
            while True:
                try:
                    resp = session.get(
                        SEARCH_URL,
                        params={"searchWord": term, "locale": "en", "page": page, "pageSize": 20},
                        timeout=30,
                    )
                    resp.raise_for_status()
                    text = resp.text
                except Exception as e:
                    print(f"  [WARN] CRIS fetch failed (term={term!r}, page={page}): {e}")
                    break

                _save_snapshot(term, page, text)

                text_clean = _INVALID_XML_REF.sub('', text)
                try:
                    root = ET.fromstring(text_clean)
                except ET.ParseError as e:
                    print(f"  [WARN] CRIS XML parse error (term={term!r}): {e}")
                    break

                try:
                    total = int(float(root.findtext("totalDataCnt") or "0"))
                except ValueError:
                    total = 0

                items = root.findall("item")
                if not items:
                    break

                for item in items:
                    kct_id = (item.findtext("system_number") or "").strip()
                    if kct_id and kct_id not in seen_ids:
                        seen_ids.add(kct_id)
                        _process_item(item, conn)

                conn.commit()

                if page * 20 >= total:
                    break
                page += 1
                time.sleep(0.5)

            time.sleep(0.5)
    finally:
        conn.commit()
        conn.close()

    print(f"  CRIS: processed {len(seen_ids)} unique records")
