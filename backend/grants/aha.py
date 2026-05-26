import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from grant_utils import is_medical, classify_area, upsert_grant
from registry_utils import extract_nct

BASE_URL = "https://professional.heart.org"
LIST_URL = f"{BASE_URL}/en/research-programs/aha-funded-research"


class _ProjectParser(HTMLParser):
    """Extract project card links from AHA funded research listing."""

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        attrs_dict = dict(attrs)
        href = attrs_dict.get("href", "")
        if "/research-programs/" in href and href not in self.links:
            self.links.append(urljoin(BASE_URL, href))


class _DetailParser(HTMLParser):
    """Extract body text from an AHA project detail page."""

    def __init__(self):
        super().__init__()
        self._in_body = False
        self._depth = 0
        self.text_parts = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")
        if "body" in classes or "description" in classes or "content" in classes:
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


def pull_aha():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    total_inserted = 0
    seen_urls: set = set()

    try:
        resp = session.get(LIST_URL, timeout=20)
        resp.raise_for_status()
        parser = _ProjectParser()
        parser.feed(resp.text)
        links = parser.links
    except Exception as e:
        print(f"  [WARN] AHA listing fetch failed: {e}")
        print(f"  AHA: {total_inserted} grants inserted")
        return

    for proj_url in links:
        if proj_url in seen_urls or proj_url == LIST_URL:
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
                "id": f"AHA-{slug}",
                "source": "AHA",
                "award_id": slug,
                "title": title[:500],
                "abstract": abstract,
                "sponsor_funder": "American Heart Association",
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
            print(f"  [WARN] AHA detail error ({proj_url}): {e}")

        time.sleep(1.0)

    print(f"  AHA: {total_inserted} grants inserted")
