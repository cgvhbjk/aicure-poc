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
import threading
from datetime import datetime, timedelta, timezone

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
# A FAILED lookup (HTTP/transport error) still bills a credit, so we cache it —
# but with a short TTL so a transient outage can be retried tomorrow while an
# immediate retry storm doesn't re-bill within the window.
_ERROR_TTL_DAYS = int(os.environ.get("AICURE_SEAMLESS_ERROR_TTL_DAYS", "1"))
_API_URL = os.environ.get("SEAMLESS_API_URL",
                          "https://api.seamless.ai/v1/search/contacts")


class SeamlessError(Exception):
    """A billed lookup we couldn't use — e.g. a 200 whose shape we can't parse
    (likely a Seamless response-schema change). Cached short-TTL + surfaced, not
    persisted as a bogus 90-day "empty"."""


# Sentinel returned by _cached_contacts for a FRESH error marker (a prior failed
# lookup) — distinct from None (cache miss) and [] (a legitimately-empty result),
# so the caller can report the failure honestly instead of as "0 contacts".
_CACHE_ERROR = object()

# Per-org locks: serialize the read→API→write critical section so two concurrent
# enrichments of the same org (or a force_refresh racing a normal call) can't
# both miss the cache and both spend a credit.
# CAVEAT: this is PROCESS-LOCAL. Under a multi-worker deploy (uvicorn/gunicorn
# --workers N, the ECS/Fargate target) two workers can still each miss the cache
# and each bill one credit; the DB cache caps the steady-state cost but the lock
# does not make double-spend impossible across processes. A DB-level claim (e.g.
# an INSERT OR IGNORE in-flight sentinel row) would be needed for cross-process
# safety. The dict also grows one Lock per distinct org for the process lifetime
# (a bounded, minor leak for a per-org admin action).
_locks_guard = threading.Lock()
_org_locks: dict = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _org_lock(cache_key: str) -> threading.Lock:
    with _locks_guard:
        lk = _org_locks.get(cache_key)
        if lk is None:
            lk = threading.Lock()
            _org_locks[cache_key] = lk
        return lk


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
    # A 200 whose body has NEITHER known contacts key means Seamless changed its
    # response shape (e.g. nested the list under a new key). We were billed, so
    # raise — enrich caches a short-TTL error marker and surfaces it — rather than
    # persisting a bogus 90-day "empty". ({"contacts": []} IS a legit empty result
    # and is NOT treated as an error.)
    if not raw and data and "contacts" not in data and "data" not in data:
        raise SeamlessError(f"unparseable 200 response; keys={list(data)[:10]}")
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
    """Return cached contacts (a list) if a fresh entry exists, else None.

    A corrupt row is logged and treated as a MISS would re-spend a credit, so the
    log makes a systematically-bad cache visible instead of silently bleeding
    credits. A fresh error marker (a failed lookup) returns the _CACHE_ERROR
    sentinel within the short error-TTL so the caller reports the failure honestly
    (no re-bill); after that window it expires to a miss so a retry hits the API."""
    row = conn.execute(
        "SELECT response_json, fetched_at FROM seamless_cache WHERE cache_key = ?",
        (cache_key,),
    ).fetchone()
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row["fetched_at"])
    except (ValueError, TypeError) as e:
        print(f"[seamless] corrupt fetched_at for {cache_key} ({e}); treating as miss")
        return None
    if fetched.tzinfo is None:                      # tolerate legacy naive rows
        fetched = fetched.replace(tzinfo=timezone.utc)
    age = _utcnow() - fetched
    try:
        data = json.loads(row["response_json"] or "[]")
    except (ValueError, TypeError) as e:
        print(f"[seamless] corrupt response_json for {cache_key} ({e}); treating as miss")
        return None
    if isinstance(data, dict) and "error" in data:
        # A cached FAILED lookup: within the short error-TTL, report it as an error
        # (no re-bill, but honestly distinguishable from "0 contacts"); after the
        # error-TTL, treat as a miss so a later retry can hit the API again.
        return _CACHE_ERROR if age <= timedelta(days=_ERROR_TTL_DAYS) else None
    if not isinstance(data, list):
        print(f"[seamless] unexpected cache shape for {cache_key} "
              f"({type(data).__name__}); treating as miss")
        return None
    if age > timedelta(days=_TTL_DAYS):
        return None
    return data


def _upsert_contacts(conn, org_id, contacts):
    """Insert contacts not already present for the org. Returns inserted count."""
    now = _utcnow().isoformat()
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


def _cache_put(conn, ck, org_id, payload, contact_count, credits):
    conn.execute(
        """INSERT OR REPLACE INTO seamless_cache
           (cache_key, org_id, response_json, contact_count, credits_used, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (ck, org_id, json.dumps(payload), contact_count, credits, _utcnow().isoformat()),
    )


def enrich_org_contacts(org_id, force_refresh: bool = False) -> dict:
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

        # Serialize the whole read→API→write section per org so concurrent calls
        # (or a force_refresh racing a normal call) can't both spend a credit.
        with _org_lock(ck):
            if not force_refresh:
                cached = _cached_contacts(conn, ck)
                if cached is _CACHE_ERROR:
                    # A recent lookup failed; we're suppressing re-bills within the
                    # error-TTL but must not pretend it succeeded with 0 contacts.
                    return {"ok": False, "source": "cache-error", "api_calls": 0,
                            "error": "previous Seamless lookup failed; cached, "
                                     "will retry after the error window"}
                if cached is not None:
                    inserted = _upsert_contacts(conn, org_id, cached)
                    conn.commit()
                    return {"ok": True, "source": "cache", "contacts": len(cached),
                            "inserted": inserted, "api_calls": 0}

            if not is_enabled():
                return {"ok": False, "source": "none", "api_calls": 0,
                        "error": "SEAMLESS_API_KEY not set — enrichment skipped"}

            try:
                contacts, credits = _call_seamless(org_name)
            except Exception as e:
                # Seamless bills a credit even on a failed/HTTP-error lookup, so
                # persist a short-TTL error marker: an immediate retry serves it
                # (no re-bill), a retry after the error-TTL hits the API again.
                _cache_put(conn, ck, org_id, {"error": str(e)[:200]}, 0, None)
                conn.commit()
                print(f"[seamless] lookup failed for {org_name!r}: {e}")
                return {"ok": False, "source": "seamless-error",
                        "error": str(e), "api_calls": 1}

            # Keep only clinical decision-maker titles.
            contacts = [c for c in contacts if _title_matches(c.get("title"))]
            # Persist the result — INCLUDING an empty list — so we never re-pay
            # credits for this org within the TTL.
            _cache_put(conn, ck, org_id, contacts, len(contacts), credits)
            inserted = _upsert_contacts(conn, org_id, contacts)
            conn.commit()
            return {"ok": True, "source": "seamless", "contacts": len(contacts),
                    "inserted": inserted, "credits_used": credits, "api_calls": 1}
    finally:
        conn.close()
