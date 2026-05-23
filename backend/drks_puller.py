import json
import os
import re
import time
from datetime import datetime
from html.parser import HTMLParser

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
BASE_URL = "https://drks.de"
SEARCH_PATH = "/search/en"
DETAIL_PATH = "/search/en/trial"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")
MAX_SEARCH_PAGES = 20

DRKS_ID_RE = re.compile(r'DRKS\d{8}')
JSESSION_RE = re.compile(r'jsessionid=([A-F0-9]+)', re.IGNORECASE)
VIEWSTATE_RE = re.compile(r'name="jakarta\.faces\.ViewState"[^>]+value="([^"]+)"')
# JSF generates field names like "searchForm:j_idt76"; scrape them out of
# the form so we don't hard-code IDs that change on every redeploy.
SEARCH_INPUT_RE = re.compile(r'name="(searchForm:j_idt\d+)"[^>]*type="text"')
SEARCH_BUTTON_RE = re.compile(r'name="(searchForm:j_idt\d+)"[^>]*value="Search"')


def _save_snapshot(drks_id, html):
    if not snapshots_enabled():
        return
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"drks_{drks_id}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


class _DtDdParser(HTMLParser):
    """Collect <dt>/<dd> label-value pairs from a DRKS detail page,
    plus the page's first non-DRKS-ID <h3> as the trial title."""

    def __init__(self):
        super().__init__()
        self.fields = {}
        self.title = ""
        self._mode = None      # "dt" | "dd" | "h3"
        self._buf = []
        self._current_label = None
        self._tag_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("dt", "dd", "h3"):
            self._mode = tag
            self._buf = []
            self._tag_depth = 1
        elif self._mode:
            self._tag_depth += 1

    def handle_endtag(self, tag):
        if self._mode and self._tag_depth > 1:
            self._tag_depth -= 1
            return
        text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
        if tag == "dt":
            self._current_label = text.rstrip(":").strip() or None
        elif tag == "dd" and self._current_label:
            if text and text != "No Entry":
                self.fields[self._current_label] = text
            self._current_label = None
        elif tag == "h3":
            # First non-empty, non-DRKS, non-Register heading wins.
            if (text and not self.title and not text.startswith("DRKS")
                    and "Register" not in text):
                self.title = text
        if tag in ("dt", "dd", "h3"):
            self._mode = None
            self._buf = []
            self._tag_depth = 0

    def handle_data(self, data):
        if self._mode:
            self._buf.append(data)


def _get_form_state(session):
    """Fetch the search page and return (session_id, viewstate, search_field, submit_field)."""
    try:
        resp = session.get(BASE_URL + SEARCH_PATH, timeout=20)
        html = resp.text
        session_m = JSESSION_RE.search(html)
        vs_m = VIEWSTATE_RE.search(html)
        input_m = SEARCH_INPUT_RE.search(html)
        button_m = SEARCH_BUTTON_RE.search(html)
        return (
            session_m.group(1) if session_m else "",
            vs_m.group(1) if vs_m else "",
            input_m.group(1) if input_m else "searchForm:j_idt76",
            button_m.group(1) if button_m else "searchForm:j_idt74",
        )
    except Exception as e:
        print(f"  [WARN] DRKS session init failed: {e}")
        return "", "", "searchForm:j_idt76", "searchForm:j_idt74"


def _search(session, term):
    """Submit search form, follow redirect, return list of result HTML pages
    (one per pagination step)."""
    session_id, viewstate, search_field, submit_field = _get_form_state(session)
    if not session_id:
        return []

    url = f"{BASE_URL}{SEARCH_PATH};jsessionid={session_id}"
    data = {
        "searchForm": "searchForm",
        search_field: term,
        submit_field: "",
        "jakarta.faces.ViewState": viewstate,
    }
    try:
        resp = session.post(url, data=data, timeout=30, allow_redirects=True)
    except Exception as e:
        print(f"  [WARN] DRKS search failed (term={term!r}): {e}")
        return []

    pages = [resp.text]
    next_url = _find_next_page(resp.text, resp.url)
    visited = {resp.url}
    safety = 0
    while next_url and next_url not in visited and safety < MAX_SEARCH_PAGES:
        visited.add(next_url)
        safety += 1
        try:
            r = session.get(next_url, timeout=20)
            pages.append(r.text)
            next_url = _find_next_page(r.text, r.url)
        except Exception as e:
            print(f"  [WARN] DRKS pagination failed: {e}")
            break
        time.sleep(0.3)
    return pages


def _find_next_page(html, current_url):
    """Look for a 'next page' link in the result list."""
    m = re.search(r'<a[^>]+href="([^"]+)"[^>]*>(?:Next|»|&raquo;)</a>', html, re.IGNORECASE)
    if not m:
        return None
    href = m.group(1)
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        return BASE_URL + href
    base = current_url.rsplit("/", 1)[0]
    return f"{base}/{href}"


def _extract_drks_ids(html):
    return list(dict.fromkeys(DRKS_ID_RE.findall(html)))


def _parse_detail(html, drks_id):
    parser = _DtDdParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    title = parser.title
    fields = parser.fields

    condition = fields.get("Free text", "")
    if not is_relevant(f"{title} {condition}"):
        return None

    countries_raw = fields.get("Recruitment countries", "")
    countries = [c.strip() for c in re.split(r"[,;]", countries_raw) if c.strip()]

    start_date = fields.get("Actual study start date") or fields.get("Planned study start date")

    enrollment_raw = fields.get("Target Sample Size", "")
    try:
        enrollment = int(re.sub(r"[^\d]", "", enrollment_raw)) if enrollment_raw else None
    except ValueError:
        enrollment = None

    sponsor = None
    for key in ("Primary Sponsor", "Sponsor", "Funding Source"):
        val = fields.get(key, "")
        if val:
            sponsor = val.split(",")[0].strip()
            break

    nct_ref = None
    for key in ("EudraCT Number", "Other WHO Primary Registry or Data Provider ID", "Secondary ID"):
        nct = extract_nct(fields.get(key, ""))
        if nct:
            nct_ref = nct
            break

    record = {
        "title_brief": title[:500] or None,
        "status": normalize_status(fields.get("Recruitment Status", "")),
        "phase": normalize_phase(fields.get("Phase", "")),
        "conditions": json.dumps([condition]) if condition else json.dumps([]),
        "sponsor": sponsor,
        "start_date": start_date,
        "primary_completion": fields.get("Planned study completion date"),
        "enrollment": enrollment,
        "countries": json.dumps(countries),
        "primary_endpoints": fields.get("Primary outcome"),
        "source_url": f"{BASE_URL}{DETAIL_PATH}/{drks_id}",
    }
    if nct_ref:
        record["_nct_cross_ref"] = nct_ref
    return record


def pull_all_drks():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": BASE_URL + SEARCH_PATH,
    })

    conn = get_connection()
    seen_ids = set()

    try:
        for term in SEARCH_TERMS:
            pages = _search(session, term)
            if not pages:
                time.sleep(1.0)
                continue

            page_ids = []
            for html in pages:
                for drks_id in _extract_drks_ids(html):
                    if drks_id not in seen_ids:
                        seen_ids.add(drks_id)
                        page_ids.append(drks_id)

            for drks_id in page_ids:
                try:
                    resp = session.get(
                        f"{BASE_URL}{DETAIL_PATH}/{drks_id}",
                        timeout=30, allow_redirects=True,
                    )
                    resp.raise_for_status()
                    detail_html = resp.text
                except Exception as e:
                    print(f"  [WARN] DRKS detail fetch failed ({drks_id}): {e}")
                    time.sleep(0.5)
                    continue

                _save_snapshot(drks_id, detail_html)
                record = _parse_detail(detail_html, drks_id)
                if record:
                    try:
                        merge_or_insert(record, "DRKS", drks_id, "drks_id", conn=conn)
                    except Exception as e:
                        print(f"  [WARN] DRKS merge error for {drks_id}: {e}")
                time.sleep(0.8)

            conn.commit()
            time.sleep(0.5)
    finally:
        conn.commit()
        conn.close()

    print(f"  DRKS: processed {len(seen_ids)} unique records")
