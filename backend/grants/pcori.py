import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests

from grant_utils import is_medical, classify_area, upsert_grant
from registry_utils import extract_nct

BASE_URL = "https://www.pcori.org"
SEARCH_TERMS = ["obesity", "diabetes", "heart failure", "adherence", "weight loss"]


class _CardParser(HTMLParser):
    """Extract project card links from PCORI listing page."""

    def __init__(self):
        super().__init__()
        self._in_card = False
        self._depth = 0
        self.links = []
        self._current_title = None
        self._capture = False

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        classes = attrs_dict.get("class", "")
        if "views-row" in classes or "funded-project" in classes:
            self._in_card = True
            self._depth = 0
        if self._in_card and tag == "a" and attrs_dict.get("href"):
            href = attrs_dict["href"]
            if "/research-results/" in href or "/research/" in href:
                self.links.append(urljoin(BASE_URL, href))
        if tag in ("h2", "h3") and self._in_card:
            self._capture = True

    def handle_data(self, data):
        if self._capture:
            self._current_title = data.strip()
            self._capture = False


class _DetailParser(HTMLParser):
    """Extract abstract text from a PCORI project detail page."""

    def __init__(self):
        super().__init__()
        self._in_abstract = False
        self._depth = 0
        self.abstract = []

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if "field--name-body" in attrs_dict.get("class", ""):
            self._in_abstract = True
            self._depth = 0
        if self._in_abstract:
            self._depth += 1

    def handle_endtag(self, tag):
        if self._in_abstract:
            self._depth -= 1
            if self._depth <= 0:
                self._in_abstract = False

    def handle_data(self, data):
        if self._in_abstract and data.strip():
            self.abstract.append(data.strip())


def _slug_from_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1]


def pull_pcori():
    session = requests.Session()
    session.headers.update({"User-Agent": "AiCurePOC/1.0 (research use)"})

    seen_urls: set = set()
    total_inserted = 0

    for term in SEARCH_TERMS:
        page = 0
        while True:
            url = f"{BASE_URL}/research-results/find-pcori-funded-project?topic={term}&page={page}"
            try:
                resp = session.get(url, timeout=20)
                resp.raise_for_status()
                html = resp.text
            except Exception as e:
                print(f"  [WARN] PCORI fetch failed (term={term!r}, page={page}): {e}")
                break

            parser = _CardParser()
            parser.feed(html)
            links = [l for l in parser.links if l not in seen_urls]

            if not links:
                break

            for proj_url in links:
                seen_urls.add(proj_url)
                try:
                    detail_resp = session.get(proj_url, timeout=20)
                    detail_resp.raise_for_status()
                    dp = _DetailParser()
                    dp.feed(detail_resp.text)
                    abstract = " ".join(dp.abstract)[:5000]

                    title_match = re.search(r"<title>([^<]+)</title>", detail_resp.text)
                    title = title_match.group(1).replace(" | PCORI", "").strip() if title_match else proj_url

                    combined = f"{title} {abstract}"
                    if not is_medical(combined):
                        continue

                    slug = _slug_from_url(proj_url)
                    nct = extract_nct(combined)

                    record = {
                        "id": f"PCORI-{slug}",
                        "source": "PCORI",
                        "award_id": slug,
                        "title": title[:500],
                        "abstract": abstract,
                        "sponsor_funder": "PCORI",
                        "country": "US",
                        "status": "ACTIVE",
                        "therapeutic_area": classify_area(combined),
                        "source_url": proj_url,
                        "linked_trial_id": nct,
                        "has_trial_link": 1 if nct else 0,
                    }
                    upsert_grant(record)
                    total_inserted += 1
                    time.sleep(1.0)
                except Exception as e:
                    print(f"  [WARN] PCORI detail error ({proj_url}): {e}")

            page += 1
            time.sleep(1.0)

    print(f"  PCORI: {total_inserted} grants inserted")
