"""Seamless.AI contact enrichment for target orgs (§7).

Finds the CMO and related clinical decision-makers at a target organization and
upserts them into `org_contacts`. AiCure's lead is the sponsor's clinical /
medical leadership, so the title filter centers on CMO / CSO / VP-Clinical / Head
of Clinical Ops / Clinical Development.

CREDIT CACHE — the billing reality: Seamless.AI charges a credit per lookup
INCLUDING failed/no-result lookups, and credits don't roll over. So we persist
EVERY response (results AND known-empty negatives) in `seamless_cache`, keyed by
the normalized org name, and only call the API on a cache miss or an explicit
force_refresh — never re-paying for the same names.

Key-gated by SEAMLESS_API_KEY: with no key the enrichment no-ops with a clear
message (mirrors news_nlp / emailer fallbacks), so the app + tests run unchanged.
"""
import os
import json
import hashlib
from datetime import datetime, timedelta

from db import get_connection

# The "keyword search for CMO and related titles" — clinical decision-makers.
SEAMLESS_TITLE_KEYWORDS = [
    "chief medical officer", "cmo", "chief scientific officer", "cso",
    "chief development officer", "cdo", "vp clinical", "vice president clinical",
    "vp, clinical", "head of clinical", "clinical operations", "head of r&d",
    "svp development", "clinical development", "vp development",
    "vp medical", "head of development",
]
# Titles that mark a primary decision-maker.
_DECISION_MAKER_TITLES = [
    "chief medical officer", "cmo", "chief scientific officer", "cso",
    "chief development officer", "cdo", "head of clinical", "head of development",
]

_TTL_DAYS = int(os.environ.get("AICURE_SEAMLESS_TTL_DAYS", "90"))
_API_URL = os.environ.get("SEAMLESS_API_URL",
                          "https://api.seamless.ai/v1/search/contacts")


def is_enabled() -> bool:
    return bool(os.environ.get("SEAMLESS_API_KEY"))


def _cache_key(org_name: str) -> str:
    return "seamless:" + hashlib.sha1((org_name or "").strip().lower().encode()).hexdigest()


def _title_matches(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in SEAMLESS_TITLE_KEYWORDS)


def _is_decision_maker(title: str) -> bool:
    t = (title or "").lower()
    return any(k in t for k in _DECISION_MAKER_TITLES)


def _call_seamless(org_name: str):
    """HTTP seam (mocked in tests). Returns (contacts, credits_used). Raises on
    transport/HTTP error. Normalizes Seamless's contact shape to our columns."""
    import requests
    key = os.environ["SEAMLESS_API_KEY"]
    resp = requests.post(
        _API_URL,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"companyName": org_name, "titles": SEAMLESS_TITLE_KEYWORDS, "limit": 25},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json() or {}
    raw = data.get("contacts") or data.get("data") or []
    credits = data.get("creditsUsed")
    if credits is None:
        credits = len(raw)
    contacts = []
    for c in raw:
        name = c.get("name") or f"{c.get('firstName', '')} {c.get('lastName', '')}".strip()
        contacts.append({
            "full_name": name or None,
            "title": c.get("title"),
            "department": c.get("department"),
            "email": c.get("email"),
            "linkedin_url": c.get("linkedinUrl") or c.get("linkedin"),
        })
    return contacts, credits


def _cached_contacts(conn, cache_key):
    """Return cached contacts if a fresh (within TTL) entry exists, else None."""
    row = conn.execute(
        "SELECT response_json, fetched_at FROM seamless_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row["fetched_at"])
    except Exception:
        return None
    if datetime.utcnow() - fetched > timedelta(days=_TTL_DAYS):
        return None
    try:
        return json.loads(row["response_json"] or "[]")
    except Exception:
        return None


def _upsert_contacts(conn, org_id, contacts):
    """Insert contacts not already present for the org. Returns inserted count."""
    now = datetime.utcnow().isoformat()
    inserted = 0
    for c in contacts:
        name = c.get("full_name")
        if not name:
            continue
        exists = conn.execute(
            "SELECT 1 FROM org_contacts WHERE org_id = ? AND full_name = ? "
            "AND IFNULL(title, '') = IFNULL(?, '')",
            (org_id, name, c.get("title")),
        ).fetchone()
        if exists:
            continue
        conn.execute(
            """INSERT INTO org_contacts
               (org_id, full_name, title, department, email, linkedin_url,
                source_url, is_decision_maker, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (org_id, name, c.get("title"), c.get("department"), c.get("email"),
             c.get("linkedin_url"), "Seamless.AI",
             1 if _is_decision_maker(c.get("title")) else 0,
             "Enriched via Seamless.AI", now),
        )
        inserted += 1
    return inserted


def enrich_org_contacts(org_id, force_refresh=False):
    """Enrich one org's contacts, serving from the credit cache when possible.

    Returns a status dict incl. `api_calls` (0 when served from cache or no key),
    so callers/tests can confirm a repeat enrichment costs no credits.
    """
    conn = get_connection()
    try:
        org = conn.execute(
            "SELECT id, canonical_name FROM organizations WHERE id = ?", (org_id,)
        ).fetchone()
        if not org:
            return {"ok": False, "error": "organization not found", "api_calls": 0}
        org_name = org["canonical_name"]
        ck = _cache_key(org_name)

        if not force_refresh:
            cached = _cached_contacts(conn, ck)
            if cached is not None:
                inserted = _upsert_contacts(conn, org_id, cached)
                conn.commit()
                return {"ok": True, "source": "cache", "contacts": len(cached),
                        "inserted": inserted, "api_calls": 0}

        if not is_enabled():
            return {"ok": False, "source": "none", "api_calls": 0,
                    "error": "SEAMLESS_API_KEY not set — enrichment skipped"}

        contacts, credits = _call_seamless(org_name)
        # Keep only clinical decision-maker titles.
        contacts = [c for c in contacts if _title_matches(c.get("title"))]
        # Persist the result — INCLUDING an empty list — so we never re-pay credits
        # for this org within the TTL.
        conn.execute(
            """INSERT OR REPLACE INTO seamless_cache
               (cache_key, org_id, response_json, contact_count, credits_used, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ck, org_id, json.dumps(contacts), len(contacts), credits,
             datetime.utcnow().isoformat()),
        )
        inserted = _upsert_contacts(conn, org_id, contacts)
        conn.commit()
        return {"ok": True, "source": "seamless", "contacts": len(contacts),
                "inserted": inserted, "credits_used": credits, "api_calls": 1}
    finally:
        conn.close()
