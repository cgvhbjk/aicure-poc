import os
import time
from datetime import datetime
from urllib.parse import urljoin

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeoutError
    _HAS_PLAYWRIGHT = True
except ImportError:
    _HAS_PLAYWRIGHT = False

from grant_utils import (
    classify_area, upsert_grant,
    extract_phase, extract_conditions, extract_interventions,
)
from registry_utils import extract_nct
from db import get_connection

BASE_URL = "https://www.pcori.org"
PORTFOLIO_URL = f"{BASE_URL}/explore-our-portfolio"
SEARCH_TERMS = ["obesity", "diabetes", "heart failure", "adherence", "weight loss"]

SNAPSHOT_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "grants"
)


def _parse_cards(html: str) -> list:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    cards = []

    for row in soup.select("div.table-row__content"):
        try:
            link_el = row.select_one("a.table-row__link")
            if not link_el:
                continue

            href = link_el.get("href", "")
            title_span = link_el.select_one("span")
            title = title_span.get_text(strip=True) if title_span else link_el.get_text(strip=True)

            proj_url = urljoin(BASE_URL, href) if href else None
            if not proj_url or not title:
                continue

            org_el = row.select_one("div.table-row__organization")
            pi_el = row.select_one("div.table-row__lead")
            status_el = row.select_one("div.table-row__awarded")
            type_el = row.select_one("div.table-row__project-type")

            cards.append({
                "title": title,
                "url": proj_url,
                "organization": org_el.get_text(strip=True) if org_el else None,
                "pi_name": pi_el.get_text(strip=True) if pi_el else None,
                "status_raw": status_el.get_text(strip=True) if status_el else "",
                "research_type": type_el.get_text(strip=True) if type_el else None,
            })
        except Exception:
            continue

    return cards


def pull_pcori():
    if not _HAS_PLAYWRIGHT:
        print("  [WARN] playwright not installed; skipping PCORI.")
        print("  PCORI: 0 grants inserted")
        return

    seen_urls: set = set()
    total_inserted = 0
    conn = get_connection()  # one connection for the whole pull; commit per term

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()

        try:
            for term in SEARCH_TERMS:
                url = f"{PORTFOLIO_URL}?keyword={term}"
                try:
                    page.goto(url, wait_until="networkidle", timeout=45000)
                except PWTimeoutError:
                    try:
                        page.wait_for_selector("div.table-row__content", timeout=10000)
                    except PWTimeoutError:
                        print(f"  [WARN] PCORI timeout loading term={term!r}")
                        continue
                except Exception as e:
                    print(f"  [WARN] PCORI navigate failed (term={term!r}): {e}")
                    continue

                # Click "Load more" / "Next page" until exhausted (up to 20 pages)
                for _ in range(20):
                    try:
                        btn = page.locator(
                            "button:has-text('Load more'), "
                            "a:has-text('Load more'), "
                            ".pager__item--next a, "
                            "li.pager__item--next a"
                        )
                        if btn.count() > 0 and btn.first.is_visible(timeout=2000):
                            btn.first.click()
                            page.wait_for_load_state("networkidle", timeout=10000)
                            time.sleep(0.5)
                        else:
                            break
                    except Exception:
                        break

                html = page.content()

                if os.environ.get("AICURE_SNAPSHOTS") == "1":
                    os.makedirs(SNAPSHOT_DIR, exist_ok=True)
                    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
                    safe = term.replace(" ", "_")
                    path = os.path.join(SNAPSHOT_DIR, f"pcori_{safe}_{ts}.html")
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(html)

                cards = _parse_cards(html)

                for card in cards:
                    proj_url = card["url"]
                    if proj_url in seen_urls:
                        continue
                    seen_urls.add(proj_url)

                    title = card["title"]
                    combined = title
                    status_raw = card.get("status_raw", "")
                    status = "COMPLETED" if "complet" in status_raw.lower() else "ACTIVE"

                    slug = proj_url.rstrip("/").split("/")[-1]
                    nct = extract_nct(title)

                    record = {
                        "id": f"PCORI-{slug}",
                        "source": "PCORI",
                        "award_id": slug,
                        "title": title[:500],
                        "organization": card.get("organization"),
                        "org_type": "ACADEMIC",
                        "pi_name": card.get("pi_name"),
                        "sponsor_funder": "PCORI",
                        "research_type": card.get("research_type"),
                        "currency": "USD",
                        "country": "US",
                        "status": status,
                        "therapeutic_area": classify_area(combined),
                        "conditions": extract_conditions(combined),
                        "interventions": extract_interventions(combined),
                        "phase_mentioned": extract_phase(combined),
                        "source_url": proj_url,
                        "linked_trial_id": nct,
                        "has_trial_link": 1 if nct else 0,
                    }
                    upsert_grant(record, conn)
                    total_inserted += 1

                conn.commit()
                time.sleep(2.0)

        finally:
            ctx.close()
            browser.close()

    conn.close()
    print(f"  PCORI: {total_inserted} grants inserted")
