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
    # Served from the error marker: NOT re-billed, and reported honestly as a
    # failure (not "ok with 0 contacts") so an outage isn't mistaken for an org
    # genuinely having no clinical decision-makers.
    assert r2["ok"] is False and r2["source"] == "cache-error"
    assert r2["api_calls"] == 0 and calls["n"] == 1   # NOT re-billed


def test_unparseable_response_raises_and_is_cached_as_error(monkeypatch):
    """A 200 whose body has no known contacts key is a billed-but-unusable result:
    _call_seamless raises SeamlessError, and enrich turns it into a short-TTL error
    marker + honest ok:False — NOT a bogus 90-day empty result."""
    _make_org("org-shape", "Shape Bio")
    monkeypatch.setenv("SEAMLESS_API_KEY", "test-key")

    def fake(org_name):
        raise seamless.SeamlessError("unparseable 200 response; keys=['results']")
    monkeypatch.setattr(seamless, "_call_seamless", fake)

    r1 = seamless.enrich_org_contacts("org-shape", force_refresh=True)
    assert r1["ok"] is False and r1["api_calls"] == 1
    r2 = seamless.enrich_org_contacts("org-shape")            # within error-TTL
    assert r2["ok"] is False and r2["source"] == "cache-error" and r2["api_calls"] == 0


def test_real_call_raises_on_unknown_shape(monkeypatch):
    """_call_seamless itself raises SeamlessError when the 200 has no contacts/data
    key, but treats {"contacts": []} as a legitimate empty result (no raise)."""
    monkeypatch.setenv("SEAMLESS_API_KEY", "test-key")

    class _Resp:
        def __init__(self, payload): self._p = payload
        def raise_for_status(self): pass
        def json(self): return self._p

    class _Stub:
        def __init__(self, payload): self._p = payload
        def post(self, *a, **k): return _Resp(self._p)

    import sys
    monkeypatch.setitem(sys.modules, "requests", _Stub({"results": [{"name": "X"}]}))
    try:
        seamless._call_seamless("Acme")
        assert False, "expected SeamlessError on unknown shape"
    except seamless.SeamlessError:
        pass

    monkeypatch.setitem(sys.modules, "requests", _Stub({"contacts": []}))
    contacts, _credits = seamless._call_seamless("Acme")     # legit empty, no raise
    assert contacts == []
