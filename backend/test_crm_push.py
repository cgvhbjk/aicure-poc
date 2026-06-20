"""Tests for the CRM hand-off (crm_push.py).

Lock in the selection rules (high-fit AND pre-start AND not-yet-pushed AND has a
sponsor), the trial→payload mapping (PI first, org-contact fallback, generic
last-name floor so the CRM's required field is always satisfied), and that run()
is a no-op until configured and stamps rows so they're never pushed twice. No
network: push_lead is monkeypatched.
"""
import pytest

import db
import crm_push


def _insert_trial(conn, **over):
    row = dict(
        id="NCT-TEST",
        title_brief="A pre-start cardiometabolic study",
        title_official=None,
        source_url="https://clinicaltrials.gov/study/NCT-TEST",
        status="NOT_YET_RECRUITING",
        phase="PHASE2",
        sponsor="Acme Therapeutics",
        conditions="Type 2 Diabetes",
        therapeutic_area="Metabolic",
        pi_name="Jane Q. Powell, MD",
        pi_email="jpowell@acmetx.com",
        aicure_fit=90,
    )
    row.update(over)
    cols = ", ".join(row.keys())
    qs = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO trials ({cols}) VALUES ({qs})", list(row.values()))
    conn.commit()


@pytest.fixture
def conn():
    c = db.get_connection()
    for tbl in ("trials", "trial_org_links", "org_contacts"):
        c.execute(f"DELETE FROM {tbl}")
    c.commit()
    yield c
    c.close()


def test_selection_filters(conn):
    _insert_trial(conn, id="good")                                   # qualifies
    _insert_trial(conn, id="lowfit", aicure_fit=20)                  # below threshold
    _insert_trial(conn, id="recruiting", status="RECRUITING")        # not pre-start
    _insert_trial(conn, id="nosponsor", sponsor="")                  # no company
    _insert_trial(conn, id="already", crm_pushed_at="2026-01-01")    # already pushed

    ids = [r["id"] for r in crm_push.select_crm_candidates(conn, threshold=70, limit=100)]
    assert ids == ["good"]


def test_payload_prefers_pi(conn):
    _insert_trial(conn, id="p1")
    p = crm_push.build_payload(conn.execute("SELECT * FROM trials WHERE id='p1'").fetchone(), conn)
    assert (p["firstName"], p["lastName"]) == ("Jane", "Powell")
    assert p["email"] == "jpowell@acmetx.com"
    assert p["company"] == "Acme Therapeutics"
    assert p["externalSource"] == "Trial Pipeline"
    assert p["externalId"] == "p1"
    assert p["fitScore"] == 90
    assert "clinicaltrials.gov" in p["description"]


def test_payload_falls_back_to_org_contact(conn):
    _insert_trial(conn, id="p2", pi_name=None, pi_email=None)
    conn.execute("INSERT INTO trial_org_links (trial_id, org_id, role) VALUES (?,?,?)",
                 ("p2", "org-1", "sponsor"))
    conn.execute(
        "INSERT INTO org_contacts (org_id, full_name, title, email, is_decision_maker) "
        "VALUES (?,?,?,?,?)",
        ("org-1", "Sam Director", "VP Clinical Ops", "sam@acmetx.com", 1),
    )
    conn.commit()
    p = crm_push.build_payload(conn.execute("SELECT * FROM trials WHERE id='p2'").fetchone(), conn)
    assert (p["firstName"], p["lastName"]) == ("Sam", "Director")
    assert p["email"] == "sam@acmetx.com"
    assert p["title"] == "VP Clinical Ops"


def test_payload_floor_lastname_when_no_contact(conn):
    _insert_trial(conn, id="p3", pi_name=None, pi_email=None)
    p = crm_push.build_payload(conn.execute("SELECT * FROM trials WHERE id='p3'").fetchone(), conn)
    assert p["lastName"]  # never empty — CRM requires it
    assert p["email"] is None  # let the CRM enrich


def test_run_noop_when_disabled(conn, monkeypatch):
    monkeypatch.delenv("CRM_PUSH_ENABLED", raising=False)
    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    called = []
    monkeypatch.setattr(crm_push, "push_lead", lambda payload: called.append(payload))
    _insert_trial(conn, id="d1")
    assert crm_push.run(conn) == 0
    assert called == []  # nothing pushed


def test_run_pushes_and_stamps(conn, monkeypatch):
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.test")
    monkeypatch.setenv("CRM_PUSH_ENABLED", "1")
    monkeypatch.setattr(
        crm_push, "push_lead",
        lambda payload: {"leadId": "L-123", "action": "created"},
    )
    _insert_trial(conn, id="r1")

    assert crm_push.run(conn) == 0
    row = conn.execute("SELECT crm_lead_id, crm_pushed_at FROM trials WHERE id='r1'").fetchone()
    assert row["crm_lead_id"] == "L-123"
    assert row["crm_pushed_at"]
    # No longer a candidate (idempotent across runs).
    assert crm_push.select_crm_candidates(conn) == []


# ── name parsing ──────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "full,expected",
    [
        ("Jane Q. Powell, MD", ("Jane", "Powell")),
        ("Powell, Jane", ("Jane", "Powell")),
        ("Powell, Jane MD", ("Jane", "Powell")),
        ("Smith", (None, "Smith")),
        ("Mary Anne Smith", ("Mary", "Smith")),
        ("Sean O'Brien, PhD, MPH", ("Sean", "O'Brien")),
        ("", (None, None)),
        (None, (None, None)),
    ],
)
def test_split_name(full, expected):
    assert crm_push._split_name(full) == expected


# ── push_lead HTTP wiring ─────────────────────────────────────────────────
class _Resp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


def test_push_lead_prepends_https_for_bare_host(monkeypatch):
    captured = {}
    import requests

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, headers=headers, json=json)
        return _Resp(201, {"leadId": "L1", "action": "created"})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setenv("CRM_BASE_URL", "crm.onrender.com")  # no scheme
    monkeypatch.setenv("CRM_INGEST_TOKEN", "tok")

    out = crm_push.push_lead({"externalId": "x"})
    assert out == {"leadId": "L1", "action": "created"}
    assert captured["url"] == "https://crm.onrender.com/api/ingest/pipeline-lead"
    assert captured["headers"]["X-Ingest-Token"] == "tok"


def test_push_lead_keeps_explicit_scheme_and_strips_trailing_slash(monkeypatch):
    captured = {}
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **k: captured.update(url=url) or _Resp(200, {}))
    monkeypatch.setenv("CRM_BASE_URL", "http://localhost:4000/")
    monkeypatch.delenv("CRM_INGEST_TOKEN", raising=False)
    crm_push.push_lead({})
    assert captured["url"] == "http://localhost:4000/api/ingest/pipeline-lead"


def test_push_lead_raises_on_error(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "post", lambda url, **k: _Resp(500, text="boom"))
    monkeypatch.setenv("CRM_BASE_URL", "https://crm.test")
    with pytest.raises(RuntimeError):
        crm_push.push_lead({})


# ── env parsing ───────────────────────────────────────────────────────────
def test_threshold_limit_enabled_env(monkeypatch):
    monkeypatch.delenv("CRM_FIT_THRESHOLD", raising=False)
    assert crm_push._threshold() == 70
    monkeypatch.setenv("CRM_FIT_THRESHOLD", "85")
    assert crm_push._threshold() == 85
    monkeypatch.setenv("CRM_FIT_THRESHOLD", "junk")  # bad value falls back
    assert crm_push._threshold() == 70

    monkeypatch.setenv("CRM_PUSH_LIMIT", "5")
    assert crm_push._limit() == 5

    monkeypatch.delenv("CRM_BASE_URL", raising=False)
    monkeypatch.delenv("CRM_PUSH_ENABLED", raising=False)
    assert crm_push._enabled() is False
    monkeypatch.setenv("CRM_BASE_URL", "https://x")
    monkeypatch.setenv("CRM_PUSH_ENABLED", "yes")
    assert crm_push._enabled() is True
    monkeypatch.setenv("CRM_PUSH_ENABLED", "0")  # disabled even with a base URL
    assert crm_push._enabled() is False
