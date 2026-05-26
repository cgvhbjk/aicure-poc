import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from grant_utils import is_medical, classify_area, upsert_grant
from registry_utils import extract_nct

BASE_URL = "https://diabetes.org"
LIST_URL = f"{BASE_URL}/research/funded-research"


class _ProjectParser(HTMLParser):
    """Extract project links from ADA funded research listing."""

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")
        if "/research/" in href and href not in self.links:
            full = urljoin(BASE_URL, href)
            if full not in self.links:
                self.links.append(full)


class _DetailParser(HTMLParser):
    """Extract body text from an ADA project detail page."""

    def __init__(self):
        super().__init__()
        self._in_body = False
        self._depth = 0
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")
        if any(k in classes for k in ["body", "description", "field-item", "content"]):
            self._in_body = True
            self._depth = 0
        if self._in_body:
            self._depth += 1

    def handle_endtag(self, tag):
        if self._in_body:
            self._depth -= 1
            if self._depth <= 0:
                self._in_body = False

    def handle_data(self, data):
        if self._in_body and data.strip():
            self.text_parts.append(data.strip())


def pull_ada():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    total_inserted = 0
    seen_urls: set = set()

    try:
        resp = session.get(LIST_URL, timeout=20)
        resp.raise_for_status()
        parser = _ProjectParser()
        parser.feed(resp.text)
        links = [l for l in parser.links if l != LIST_URL]
    except Exception as e:
        print(f"  [WARN] ADA listing fetch failed: {e}")
        print(f"  ADA: {total_inserted} grants inserted")
        return

    for proj_url in links:
        if proj_url in seen_urls:
            continue
        seen_urls.add(proj_url)

        try:
            detail_resp = session.get(proj_url, timeout=20)
            detail_resp.raise_for_status()

            dp = _DetailParser()
            dp.feed(detail_resp.text)
            abstract = " ".join(dp.text_parts)[:5000]

            title_match = re.search(r"<title>([^<]+)</title>", detail_resp.text)
            title = title_match.group(1).strip() if title_match else proj_url

            combined = f"{title} {abstract}"
            if not is_medical(combined):
                time.sleep(1.0)
                continue

            slug = proj_url.rstrip("/").split("/")[-1]
            nct = extract_nct(combined)

            record = {
                "id": f"ADA-{slug}",
                "source": "ADA",
                "award_id": slug,
                "title": title[:500],
                "abstract": abstract,
                "sponsor_funder": "American Diabetes Association",
                "country": "US",
                "status": "ACTIVE",
                "therapeutic_area": classify_area(combined),
                "source_url": proj_url,
                "linked_trial_id": nct,
                "has_trial_link": 1 if nct else 0,
            }
            upsert_grant(record)
            total_inserted += 1
        except Exception as e:
            print(f"  [WARN] ADA detail error ({proj_url}): {e}")

        time.sleep(1.0)

    print(f"  ADA: {total_inserted} grants inserted")
