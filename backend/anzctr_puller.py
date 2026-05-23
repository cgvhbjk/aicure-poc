import json
import os
import re
import time
from datetime import datetime
from html.parser import HTMLParser

import requests

from registry_utils import (
    extract_nct, is_relevant, merge_or_insert,
    normalize_phase, normalize_status,
)

SEARCH_TERMS = [
    "obesity", "semaglutide", "tirzepatide", "GLP-1",
    "type 2 diabetes", "heart failure", "metabolic",
]
SEARCH_URL = "https://www.anzctr.org.au/TrialSearch.aspx"
DETAIL_URL = "https://www.anzctr.org.au/Trial/Registration/TrialReview.aspx"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")

ACTRN_PATTERN = re.compile(r'ACTRN\d+')


class _ACTRNExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.actrn_ids = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")
        m = ACTRN_PATTERN.search(href)
        if m:
            actrn = m.group(0)
            if actrn not in self.actrn_ids:
                self.actrn_ids.append(actrn)


class _DetailParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}
        self._current_label = None
        self._capture = False
        self._in_label = False
        self._label_buf = []
        self._value_buf = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        cls = attrs_dict.get("class", "")
        if tag == "dt":
            self._in_label = True
            self._label_buf = []
        elif tag == "dd":
            self._capture = True
            self._value_buf = []

    def handle_endtag(self, tag):
        if tag == "dt":
            self._in_label = False
            self._current_label = " ".join(self._label_buf).strip()
        elif tag == "dd":
            self._capture = False
            if self._current_label:
                self.fields[self._current_label] = " ".join(self._value_buf).strip()
            self._current_label = None

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._in_label:
            self._label_buf.append(text)
        elif self._capture:
            self._value_buf.append(text)


def _extract_actrn_ids(html: str) -> list:
    parser = _ACTRNExtractor()
    parser.feed(html)
    return parser.actrn_ids


def _parse_detail(html: str, actrn: str) :
    parser = _DetailParser()
    try:
        parser.feed(html)
    except Exception:
        pass

    f = parser.fields
    title = f.get("Title") or f.get("Public title") or ""
    condition = f.get("Health condition(s) or problem(s) studied") or f.get("Condition") or ""

    if not is_relevant(f"{title} {condition}"):
        return None

    secondary = f.get("Secondary ID(s)") or f.get("Universal Trial Number (UTN)") or ""
    nct_ref = extract_nct(secondary)

    countries_raw = f.get("Countries of recruitment") or ""
    countries = [c.strip() for c in countries_raw.replace(";", ",").split(",") if c.strip()]

    enrollment_raw = f.get("Target sample size") or f.get("Anticipated sample size") or ""
    try:
        enrollment = int(re.sub(r"[^\d]", "", enrollment_raw)) if enrollment_raw else None
    except ValueError:
        enrollment = None

    record = {
        "title_brief": title[:500] or None,
        "status": normalize_status(f.get("Recruitment status") or ""),
        "phase": normalize_phase(f.get("Phase") or ""),
        "conditions": json.dumps([condition]) if condition else json.dumps([]),
        "sponsor": f.get("Principal investigator") or f.get("Sponsor") or None,
        "start_date": f.get("Actual start date") or f.get("Anticipated start date") or None,
        "enrollment": enrollment,
        "countries": json.dumps(countries),
        "primary_endpoints": f.get("Primary outcome") or f.get("Primary outcomes") or None,
        "source_url": f"{DETAIL_URL}?ACTRN={actrn}",
    }
    if nct_ref:
        record["_nct_cross_ref"] = nct_ref

    return record


def _save_snapshot(actrn: str, html: str):
    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
    path = os.path.join(SNAPSHOT_DIR, f"anzctr_{actrn}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


def pull_all_anzctr():
    print('  ANZCTR: skipping — registry blocks automated access')
    return
    seen_ids = set()

    for term in SEARCH_TERMS:
        try:
            resp = requests.get(
                SEARCH_URL,
                params={"searchTxt": term, "isBasicSearch": "true"},
                timeout=30,
                headers={"User-Agent": "Mozilla/5.0 (compatible; AiCurePOC/1.0)"},
            )
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            print(f"  [WARN] ANZCTR search failed (term={term!r}): {e}")
            time.sleep(1.0)
            continue

        actrn_ids = _extract_actrn_ids(html)

        for actrn in actrn_ids:
            if actrn in seen_ids:
                continue
            seen_ids.add(actrn)

            try:
                detail_resp = requests.get(
                    DETAIL_URL,
                    params={"ACTRN": actrn, "showOriginal": "true", "isReview": "false"},
                    timeout=30,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; AiCurePOC/1.0)"},
                )
                detail_resp.raise_for_status()
                detail_html = detail_resp.text
            except Exception as e:
                print(f"  [WARN] ANZCTR detail fetch failed ({actrn}): {e}")
                time.sleep(1.0)
                continue

            _save_snapshot(actrn, detail_html)
            record = _parse_detail(detail_html, actrn)
            if record:
                try:
                    merge_or_insert(record, "ANZCTR", actrn, "anzctr_id")
                except Exception as e:
                    print(f"  [WARN] ANZCTR merge error for {actrn}: {e}")

            time.sleep(1.0)

        time.sleep(0.5)

    print(f"  ANZCTR: processed {len(seen_ids)} unique records")
