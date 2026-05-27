"""
WHO ICTRP puller — scrapes search result pages using Playwright.

The CSV export requires server-side ASP.NET state we cannot replicate.
Instead we paginate the GridView via __doPostBack and parse each page.
"""
import json
import os
import re
import time
from datetime import datetime

from db import get_connection
from registry_utils import (
    extract_nct, is_relevant, merge_or_insert,
    normalize_phase, normalize_status, snapshots_enabled,
)

SEARCH_TERMS = [
    "obesity", "GLP-1", "semaglutide", "tirzepatide", "type 2 diabetes",
    "heart failure", "atrial fibrillation", "metabolic syndrome", "NASH", "adherence",
]

SEARCH_URL = "https://trialsearch.who.int/Default.aspx"
SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")

REGISTRY_PREFIXES = {
    "ACTRN":  ("ANZCTR",  "anzctr_id"),
    "DRKS":   ("DRKS",    "drks_id"),
    "jRCT":   ("jRCT",    "jrct_id"),
    "NL":     ("NTR",     "ntr_id"),
    "ChiCTR": ("ChiCTR",  "chictr_id"),
    "CTRI":   ("CTRI",    "ctri_id"),
    "IRCT":   ("IRCT",    "irct_id"),
    "RBR":    ("ReBec",   "rebec_id"),
    "PACTR":  ("PACTR",   "pactr_id"),
    "TCTR":   ("TCTR",    None),
    "SLCTR":  ("SLCTR",   None),
    "LBCTR":  ("LBCTR",   None),
}

SKIP_PREFIXES = {"NCT", "EUCTR", "ISRCTN", "KCT"}

# Max pages per search term (100 results/page × 20 pages = 2000 results per term)
MAX_PAGES_PER_TERM = 20


def _detect_registry(trial_id: str):
    for prefix, (name, col) in REGISTRY_PREFIXES.items():
        if trial_id.startswith(prefix):
            return name, col
    for skip in SKIP_PREFIXES:
        if trial_id.startswith(skip):
            return None, None
    return f"WHO-{trial_id.split('-')[0]}", None


def _parse_gridview_html(html: str) -> list:
    """
    Parse the ICTRP GridView1 HTML and return trial record dicts.

    Column layout (observed):
    0: Recruitment status (text in <td>)
    1: Prospective Registration (span)
    2: Main ID  (span id="GridView1_ctl##_Label1")
    3: expand/action
    4: Public Title
    5: Date of Registration
    6: Results available
    """
    records = []

    # Extract trial IDs from Label1 spans
    trial_ids = re.findall(r'<span id="GridView1_ctl\d+_Label1">([^<]+)</span>', html)
    if not trial_ids:
        return records

    # Split html into per-row sections keyed by trial ID
    for tid in trial_ids:
        # Locate the span for this trial ID in the HTML
        span_tag = f'<span id="GridView1_'
        idx = html.find(tid)
        if idx == -1:
            continue

        # Find the enclosing <tr>
        row_start = html.rfind('<tr', 0, idx)
        row_end = html.find('</tr>', idx)
        if row_start == -1 or row_end == -1:
            continue

        row_html = html[row_start: row_end + 5]

        # Extract all <td> text content (strip inner HTML)
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL | re.IGNORECASE)
        cells_text = []
        for c in cells:
            t = re.sub(r'<[^>]+>', ' ', c)
            t = re.sub(r'\s+', ' ', t).strip()
            cells_text.append(t)

        # Known columns: [status, prospective_reg, main_id_cell, action, title, reg_date, results]
        status_raw = cells_text[0] if cells_text else ""
        title = cells_text[4].strip() if len(cells_text) > 4 else ""
        reg_date = cells_text[5].strip() if len(cells_text) > 5 else ""

        # Public title might also be in a Panel (collapsible) — grab it from span
        title_m = re.search(
            r'<span[^>]*id="GridView1_ctl\d+_Label2"[^>]*>([^<]+)</span>', row_html
        )
        if title_m:
            title = title_m.group(1).strip()

        # NCT cross-ref in secondary IDs area
        nct_cross = extract_nct(row_html)

        record = {
            "registry_id": tid,
            "source_url": f"https://trialsearch.who.int/Trial2.aspx?TrialID={tid}",
            "status": normalize_status(status_raw),
            "first_posted": reg_date or None,
        }
        if title:
            record["title_brief"] = title[:500]
        if nct_cross:
            record["_nct_cross_ref"] = nct_cross

        records.append(record)

    return records


def _paginate_term(page, term: str) -> list:
    """
    Search ICTRP for *term*, set page size to 100, paginate up to MAX_PAGES_PER_TERM.
    Returns list of raw record dicts.
    """
    from playwright.sync_api import TimeoutError as PWTimeoutError

    all_records = []
    current_page = 1

    # Search
    try:
        page.wait_for_selector("#TextBox1", timeout=10000)
        page.fill("#TextBox1", term)
        page.click("#Button1")
        page.wait_for_load_state("load", timeout=30000)
    except PWTimeoutError as e:
        print(f"  [WARN] ICTRP search failed for {term!r}: {e}")
        return []

    # Change to 100 results per page (triggers a page reload via DropDownList1)
    try:
        page.wait_for_selector("#DropDownList1", timeout=10000)
        page.select_option("#DropDownList1", "100")
        page.wait_for_load_state("load", timeout=30000)
    except PWTimeoutError:
        pass  # Stay with default page size if this fails

    while current_page <= MAX_PAGES_PER_TERM:
        try:
            page.wait_for_selector("#GridView1", timeout=15000)
        except PWTimeoutError:
            break

        html = page.content()

        if snapshots_enabled():
            os.makedirs(SNAPSHOT_DIR, exist_ok=True)
            ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            safe = term.replace(" ", "_").replace("/", "-")
            path = os.path.join(SNAPSHOT_DIR, f"ictrp_{safe}_p{current_page}_{ts}.html")
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

        records = _parse_gridview_html(html)
        if not records:
            break

        all_records.extend(records)

        # Check if there's a next page
        has_next = bool(re.search(r"__doPostBack\('GridView1','Page\$Next'\)", html) or
                        re.search(r"__doPostBack\('GridView1','Page\$(\d+)'\)", html))
        if not has_next or current_page >= MAX_PAGES_PER_TERM:
            break

        # Navigate to next page via __doPostBack
        current_page += 1
        try:
            page.evaluate(f"__doPostBack('GridView1','Page${current_page}')")
            page.wait_for_load_state("load", timeout=20000)
            time.sleep(1.0)
        except Exception as e:
            print(f"  [WARN] ICTRP pagination failed at page {current_page}: {e}")
            break

    return all_records


def pull_all_ictrp():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  [WARN] playwright not installed; skipping ICTRP.")
        return

    conn = get_connection()
    seen_ids: set = set()
    total_inserted = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context()
        page = ctx.new_page()
        page.set_extra_http_headers({"Accept-Language": "en-US,en;q=0.9"})

        try:
            for term in SEARCH_TERMS:
                print(f"  ICTRP: searching {term!r}")
                try:
                    page.goto(SEARCH_URL, wait_until="load", timeout=30000)
                except Exception as e:
                    print(f"  [WARN] ICTRP navigation failed: {e}")
                    continue

                raw_records = _paginate_term(page, term)
                print(f"    → {len(raw_records)} records found")

                for rec in raw_records:
                    tid = rec["registry_id"]
                    if tid in seen_ids:
                        continue

                    registry_name, id_col = _detect_registry(tid)
                    if registry_name is None:
                        continue

                    # Relevance filter on title
                    title = rec.get("title_brief", "")
                    if title and not is_relevant(title):
                        continue

                    seen_ids.add(tid)
                    nct_cross = rec.pop("_nct_cross_ref", None)

                    if id_col:
                        try:
                            if nct_cross:
                                rec["_nct_cross_ref"] = nct_cross
                            merge_or_insert(rec, registry_name, tid, id_col, conn=conn)
                            total_inserted += 1
                        except Exception as e:
                            print(f"  [WARN] ICTRP merge error for {tid}: {e}")
                    else:
                        try:
                            prefix_code = registry_name.replace("WHO-", "")
                            rec["id"] = f"{prefix_code}-{tid}"
                            rec["registry_sources"] = json.dumps([registry_name])
                            rec["all_registry_ids"] = json.dumps([tid])
                            rec["ingested_at"] = datetime.utcnow().isoformat()
                            cols = ", ".join(rec.keys())
                            placeholders = ", ".join("?" * len(rec))
                            conn.execute(
                                f"INSERT OR REPLACE INTO trials ({cols}) VALUES ({placeholders})",
                                list(rec.values()),
                            )
                            total_inserted += 1
                        except Exception as e:
                            print(f"  [WARN] ICTRP insert error for {tid}: {e}")

                conn.commit()
                time.sleep(2.0)

        finally:
            ctx.close()
            browser.close()
            conn.commit()
            conn.close()

    print(f"  ICTRP: {len(seen_ids)} unique records, {total_inserted} inserted/updated")
