"""Seamless.AI enrichment + credit cache (§7).

Asserts the billing-critical property: a second enrichment of the same org is
served from the cache and makes ZERO new API calls (Seamless charges per lookup,
including failures), and that the title filter keeps only clinical decision-makers.
"""
import seamless
from db import get_connection


def _make_org(org_id, name):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO organizations (id, canonical_name, created_at) "
        "VALUES (?, ?, ?)", (org_id, name, "2024-01-01"))
    conn.commit()
    conn.close()


def _fake_contacts():
    return ([
        {"full_name": "Jane Doe", "title": "Chief Medical Officer", "email": "jane@x.com"},
        {"full_name": "Bob Roe", "title": "VP Clinical Operations", "email": "bob@x.com"},
        {"full_name": "Sam Sales", "title": "Account Executive", "email": "sam@x.com"},
    ], 3)


def test_cache_prevents_second_api_call(monkeypatch):
    _make_org("org-cache", "Cache Therapeutics")
    monkeypatch.setenv("SEAMLESS_API_KEY", "test-key")
    calls = {"n": 0}

    def fake(org_name):
        calls["n"] += 1
        return _fake_contacts()
    monkeypatch.setattr(seamless, "_call_seamless", fake)

    r1 = seamless.enrich_org_contacts("org-cache", force_refresh=True)
    assert r1["ok"] and r1["api_calls"] == 1 and calls["n"] == 1
    # Non-clinical title (Account Executive) filtered out.
    assert r1["contacts"] == 2

    r2 = seamless.enrich_org_contacts("org-cache")   # within TTL → cache
    assert r2["source"] == "cache" and r2["api_calls"] == 0
    assert calls["n"] == 1                            # NO new API call → no credits


def test_decision_maker_flagged(monkeypatch):
    _make_org("org-dm", "DM Bio")
    monkeypatch.setenv("SEAMLESS_API_KEY", "test-key")
    monkeypatch.setattr(seamless, "_call_seamless", lambda n: _fake_contacts())
    seamless.enrich_org_contacts("org-dm", force_refresh=True)
    conn = get_connection()
    rows = conn.execute(
        "SELECT full_name, is_decision_maker FROM org_contacts WHERE org_id = 'org-dm'"
    ).fetchall()
    conn.close()
    by_name = {r["full_name"]: r["is_decision_maker"] for r in rows}
    assert by_name.get("Jane Doe") == 1           # CMO → decision maker
    assert by_name.get("Bob Roe") == 0            # VP Clinical → kept, not DM


def test_no_key_noops(monkeypatch):
    _make_org("org-nokey", "NoKey Inc")
    monkeypatch.delenv("SEAMLESS_API_KEY", raising=False)
    r = seamless.enrich_org_contacts("org-nokey")
    assert r["ok"] is False and r["api_calls"] == 0


def test_failed_lookup_cached_not_rebilled(monkeypatch):
    """A failed (HTTP-error) lookup still bills a credit, so it's cached on a short
    TTL — an immediate retry must NOT re-spend (the documented invariant)."""
    _make_org("org-err", "Err Bio")
    monkeypatch.setenv("SEAMLESS_API_KEY", "test-key")
    calls = {"n": 0}

    def boom(org_name):
        calls["n"] += 1
        raise RuntimeError("HTTP 500")
    monkeypatch.setattr(seamless, "_call_seamless", boom)

    r1 = seamless.enrich_org_contacts("org-err", force_refresh=True)
    assert r1["ok"] is False and r1["api_calls"] == 1 and calls["n"] == 1

    r2 = seamless.enrich_org_contacts("org-err")     # immediate retry → error marker
    assert r2["source"] == "cache" and r2["api_calls"] == 0
    assert calls["n"] == 1                            # NOT re-billed
