"""Tests for the shared AiCure opportunity scorer (scoring.py).

These lock in the behaviours the grids and the email digest both depend on:
scores stay in 0-100, a strong lead outranks a weak one, AiCure must engage
BEFORE a trial starts (a passed start date is penalised), and a real product
touchpoint (self-administered / remote / PRO) beats one with none.
"""
import os
from datetime import datetime, timedelta

import scoring
from scoring import score_grant, score_trial


def days(n):
    """ISO date string n days from today (negative = past)."""
    return (datetime.utcnow() + timedelta(days=n)).strftime("%Y-%m-%d")


def make_grant(**over):
    g = dict(
        title="Medication adherence in oral diabetes therapy",
        abstract="A decentralized study of medication adherence with remote "
                 "monitoring and patient-reported outcomes. " + "x" * 250,
        conditions="diabetes",
        therapeutic_area="Diabetes",
        amount_usd=12_000_000,
        country="United States",
        award_date=days(-30),     # just awarded
        start_date=days(-30),
        end_date=days(500),       # long runway
        linked_trial_id="NCT01",
        organization="Yale University",
        pi_name="Dr. Smith",
    )
    g.update(over)
    return g


def make_trial(**over):
    t = dict(
        status="NOT_YET_RECRUITING",
        start_date=days(60),       # future — pre-start, the ideal window
        therapeutic_area="Diabetes",
        phase="PHASE3",
        enrollment=1500,
        num_sites=30,
        lead_country="United States",
        registry_sources='["ClinicalTrials.gov","CTIS"]',
        sponsor="Acme Pharma",
        brief_summary="An oral once-daily tablet self-administered by patients "
                      "at home. " + "y" * 60,
        pi_email="pi@example.com",
        interventions="oral tablet",
        conditions="diabetes",
        title_brief="Oral therapy trial",
        digital_biomarkers=1,
        dct_elements=1,
        epro_ecoa=1,
    )
    t.update(over)
    return t


WEAK_GRANT = dict(
    title="Synthesis of novel polymers",
    abstract="Materials science research on polymer chemistry.",
    conditions=None,
    therapeutic_area="Other",
    amount_usd=50_000,
    country="China",
    award_date=days(-1500),
    start_date=days(-1500),
    end_date=days(-200),
    linked_trial_id=None,
    organization=None,
    pi_name=None,
)

WEAK_TRIAL = dict(
    status="COMPLETED",
    start_date=days(-1500),
    therapeutic_area="Other",
    phase="PHASE1",
    enrollment=20,
    num_sites=1,
    lead_country="China",
    registry_sources='["ChiCTR"]',
    sponsor=None,
    brief_summary=None,
    pi_email=None,
    interventions="implanted device",
    conditions=None,
    title_brief="Device study",
    digital_biomarkers=0,
    dct_elements=0,
    epro_ecoa=0,
)


# ── invariants ────────────────────────────────────────────────────────────────

def test_scores_are_ints_in_range():
    for g in (make_grant(), WEAK_GRANT, make_grant(amount_usd=None, abstract=None)):
        s = score_grant(g)
        assert isinstance(s, int) and 0 <= s <= 100
    for t in (make_trial(), WEAK_TRIAL, make_trial(enrollment=None, start_date=None)):
        s = score_trial(t)
        assert isinstance(s, int) and 0 <= s <= 100


def test_scorer_returns_score_and_why():
    score, why = scoring._illustrative_grant_score(make_grant())
    assert score == score_grant(make_grant())
    assert isinstance(why, str) and why


# ── ranking behaviour the product relies on ───────────────────────────────────

def test_strong_grant_beats_weak_grant():
    assert score_grant(make_grant()) > score_grant(WEAK_GRANT)


def test_strong_trial_beats_weak_trial():
    assert score_trial(make_trial()) > score_trial(WEAK_TRIAL)


def test_grant_without_aicure_touchpoint_is_penalised():
    with_fit = make_grant()
    no_fit = make_grant(
        title="Polymer chemistry", abstract="materials research", conditions=None)
    assert score_grant(with_fit) > score_grant(no_fit)


def test_trial_passed_start_ranks_below_pre_start():
    """Core principle: AiCure must reach the sponsor before the trial starts."""
    pre_start = make_trial(start_date=days(60))
    already_started = make_trial(start_date=days(-60))
    assert score_trial(pre_start) > score_trial(already_started)


def test_trial_with_touchpoint_beats_one_without():
    oral = make_trial()
    no_touch = make_trial(
        interventions="implanted device", brief_summary="device-based therapy",
        title_brief="device", conditions=None,
        digital_biomarkers=0, dct_elements=0, epro_ecoa=0)
    assert score_trial(oral) > score_trial(no_touch)


def test_us_geography_beats_offshore():
    # A moderate grant (not capped at 100) so the geography delta is visible.
    base = make_grant(
        amount_usd=500_000, linked_trial_id=None, organization=None,
        pi_name=None, abstract="adherence study", therapeutic_area="Other",
        award_date=days(-800), end_date=days(60))
    us = {**base, "country": "United States"}
    offshore = {**base, "country": "China"}
    assert score_grant(us) > score_grant(offshore)


# ── the scorer is the single source of truth ──────────────────────────────────

def test_emailer_uses_shared_scorer_no_duplicate():
    """emailer.py must import the scorer, not redefine it."""
    src = open(os.path.join(os.path.dirname(__file__), "emailer.py")).read()
    assert "from scoring import" in src
    assert "def _illustrative_grant_score" not in src
    assert "def _illustrative_trial_score" not in src
