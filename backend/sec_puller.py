"""SEC EDGAR scanner — 10-Q-first early-development + acquisition signals (§5).

AiCure's ideal entry point is the quarter a sponsor FIRST mentions a planned trial
in a 10-Q — long before it posts to a registry. We also scan 10-Ks (annual pipeline
language) and 8-Ks (Item 1.01/2.01 acquisitions → the "this got bought, why?"
stream). Results flow into the existing `news_items` table and ride the normal
news_nlp.analyze() → digest pipeline (the rss_parser event vocabulary is reused:
10-Q/10-K early-dev → 'protocol_planning'; 8-K M&A → 'acquisition').

EDGAR full-text search (efts.sec.gov) indexes all forms incl. 10-Q, needs NO API
key — just a descriptive User-Agent and polite rate limiting. The whole puller is
key-gated by AICURE_SEC_ENABLED so it stays off (and the pipeline/tests are
unaffected) until explicitly switched on.
"""
import os
import json
import time
import sqlite3
from datetime import datetime, timezone

import requests

from db import get_connection


def _utcnow_iso() -> str:
    """Naive-UTC ISO timestamp (datetime.utcnow() is deprecated / removal-tracked),
    matching the format the rest of the DB stores."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

_EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
# SEC requires a descriptive UA with contact info; override via env in deploy.
_USER_AGENT = os.environ.get(
    "AICURE_SEC_USER_AGENT", "AiCure Research (aicure-poc) contact@aicure.example"
)

# (form, event_type, query). Early-development language for 10-Q (primary) and
# 10-K; acquisition language for 8-K. EFTS `q` is a single boolean/phrase string.
_SEARCHES = [
    ("10-Q", "protocol_planning",
     '"plan to initiate" OR "expect to initiate" OR "initiating a Phase" '
     'OR "IND-enabling" OR "expect to begin a Phase"'),
    ("10-K", "protocol_planning",
     '"plan to initiate" OR "expect to initiate" OR "initiating a Phase" '
     'OR "IND-enabling"'),
    ("8-K", "acquisition",
     '"definitive agreement to acquire" OR "agreement and plan of merger" '
     'OR "to acquire" OR "completes acquisition"'),
]


def _build_url(hit):
    """Reconstruct a filing-document URL from an EFTS hit. Falls back to the
    EDGAR full-text-search UI link if the pieces aren't present."""
    src = hit.get("_source", {}) or {}
    _id = hit.get("_id", "") or ""
    ciks = src.get("ciks") or src.get("cik") or []
    cik = (ciks[0] if isinstance(ciks, list) and ciks else ciks) or ""
    cik = str(cik).lstrip("0") or "0"
    if ":" in _id:
        accession, doc = _id.split(":", 1)
        acc_nodash = accession.replace("-", "")
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
    return f"https://efts.sec.gov/LATEST/search-index?q=&forms={src.get('form', '')}"


def parse_hits(data: dict, form: str, event_type: str) -> list:
    """Pure transform: EFTS JSON → list of news_items dicts. Kept side-effect-free
    so it can be unit-tested against a captured sample response."""
    now = _utcnow_iso()
    out = []
    hits = (((data or {}).get("hits") or {}).get("hits")) or []
    for h in hits:
        src = h.get("_source", {}) or {}
        names = src.get("display_names") or []
        company = (names[0] if names else src.get("entity") or "Unknown filer").strip()
        # display_names look like "Acme Therapeutics Inc  (CIK 0001234567)" — trim the CIK.
        company = company.split("  (")[0].split(" (CIK")[0].strip()
        file_date = src.get("file_date") or src.get("fileDate") or ""
        url = _build_url(h)  # always non-empty (has a UI fallback)
        title = f"{company} — {form} filing" + (f" ({file_date})" if file_date else "")
        out.append({
            "source": f"SEC EDGAR — {form}",
            "title": title,
            "url": url,
            "published_at": file_date or now,
            "body_snippet": f"{company} {form} matched: early-development / M&A language.",
            "sponsor_mentioned": company,
            "drug_mentioned": None,
            "phase_mentioned": None,
            "nct_ids_found": "[]",
            "trial_id": None,
            "is_trial_announcement": 1 if event_type != "acquisition" else 0,
            "is_trial_results": 0,
            "event_type": event_type,
            "ingested_at": now,
        })
    return out


def _fetch(query: str, form: str) -> dict:
    # EFTS paginates with `from`/`size` (it returns ~10 hits/page); the previously
    # passed `hits` param is not recognized, so we just take the first page here.
    params = {"q": query, "forms": form}
    resp = requests.get(_EFTS_URL, params=params,
                        headers={"User-Agent": _USER_AGENT}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def pull_sec() -> None:
    """Entry point used by ingest.py. No-op (and prints why) unless
    AICURE_SEC_ENABLED=1, so it never affects the default pipeline or tests."""
    if os.environ.get("AICURE_SEC_ENABLED") != "1":
        print("  SEC EDGAR disabled (set AICURE_SEC_ENABLED=1 to enable) — skipping")
        return

    conn = get_connection()
    inserted = 0
    for form, event_type, query in _SEARCHES:
        # Catch ONLY the network/transport error here — a bug in the pure
        # parse_hits transform should surface as itself, not be mislabeled a
        # "fetch failed".
        try:
            data = _fetch(query, form)
        except requests.RequestException as e:
            print(f"  [WARN] SEC EDGAR fetch failed for {form}: {e}")
            continue
        rows = parse_hits(data, form, event_type)
        for item in rows:
            try:
                # url is UNIQUE — a filing already seen (e.g. a prior quarter's
                # 10-Q) is skipped, so each filing's FIRST appearance wins.
                conn.execute(
                    """
                    INSERT OR IGNORE INTO news_items
                      (source, title, url, published_at, body_snippet,
                       sponsor_mentioned, drug_mentioned, phase_mentioned,
                       nct_ids_found, trial_id, is_trial_announcement,
                       is_trial_results, event_type, ingested_at)
                    VALUES
                      (:source, :title, :url, :published_at, :body_snippet,
                       :sponsor_mentioned, :drug_mentioned, :phase_mentioned,
                       :nct_ids_found, :trial_id, :is_trial_announcement,
                       :is_trial_results, :event_type, :ingested_at)
                    """,
                    item,
                )
                if conn.execute("SELECT changes()").fetchone()[0]:
                    inserted += 1
            except sqlite3.Error as e:
                print(f"  [WARN] SEC insert failed for {item.get('url')}: {e}")
        conn.commit()
        time.sleep(0.3)  # polite to EDGAR
    conn.close()
    print(f"  SEC EDGAR: {inserted} new filings ingested")
