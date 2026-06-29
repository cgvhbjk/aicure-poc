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
        start_date=days(240),      # ~8 months out — the 6–12mo ideal window
        therapeutic_area="CNS / Psychiatry",
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
    """Core principle: AiCure must reach the sponsor 6–12mo before it starts; a
    trial already started (or starting in <6mo) is gated out (score 0)."""
    pre_start = make_trial(start_date=days(240))   # ideal window
    already_started = make_trial(start_date=days(-60))
    assert score_trial(pre_start) > score_trial(already_started)
    assert score_trial(already_started) == 0       # too late — suppressed


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


# ── new targeting behaviour (CNS-led, gates, graduates, known sponsors) ─────────

# A deliberately thin trial so additive bonuses don't saturate the 0-100 cap and
# the one varied dimension (area / sponsor) is visible.
def thin_trial(**over):
    t = dict(
        status="NOT_YET_RECRUITING", start_date=days(900), phase="PHASE1",
        enrollment=None, num_sites=None, lead_country=None,
        registry_sources='["ClinicalTrials.gov"]', sponsor=None,
        brief_summary="oral tablet self-administered at home", pi_email=None,
        interventions="oral tablet", conditions=None, title_brief="trial",
        therapeutic_area="Other", digital_biomarkers=0, dct_elements=0, epro_ecoa=0,
    )
    t.update(over)
    return t


def test_cns_outranks_metabolic_and_oncology_oral():
    """The won book is CNS/psych — a self-administered CNS trial must outrank a
    cardiometabolic one, and an oncology oral (those patients adhere) ranks low."""
    cns = thin_trial(therapeutic_area="CNS / Psychiatry", conditions="schizophrenia",
                     title_brief="Schizophrenia oral antipsychotic")
    metabolic = thin_trial(therapeutic_area="Metabolic / GLP-1", conditions="obesity",
                           title_brief="Obesity oral therapy")
    onc = thin_trial(therapeutic_area="Other", conditions="lung cancer",
                     title_brief="Oncology oral", brief_summary="oral tablet for tumor")
    assert score_trial(cns) > score_trial(metabolic) > score_trial(onc)


def test_phase4_trial_excluded():
    assert score_trial(make_trial(phase="PHASE4")) == 0


def test_starts_under_six_months_excluded():
    assert score_trial(make_trial(start_date=days(90))) == 0
    assert score_trial(make_trial(start_date=days(240))) > 0


def test_transdermal_patch_excluded():
    patch = make_trial(interventions="transdermal patch", brief_summary="a skin patch",
                       title_brief="patch study", digital_biomarkers=0,
                       dct_elements=0, epro_ecoa=0)
    assert score_trial(patch) == 0


def test_no_touchpoint_trial_excluded():
    no_touch = make_trial(interventions="implanted device",
                          brief_summary="device-based therapy", title_brief="device",
                          conditions=None, digital_biomarkers=0, dct_elements=0,
                          epro_ecoa=0)
    assert score_trial(no_touch) == 0


def test_phase1_graduate_flagged_and_scored():
    grad = make_trial(status="COMPLETED", phase="PHASE1", start_date=days(-400),
                      primary_completion=days(-30))
    is_grad, why = scoring._trial_phase1_graduate(grad)
    assert is_grad and "P2" in why
    # A graduate is NOT gated by the underway-status rule — it scores > 0.
    assert score_trial(grad) > 0


def test_known_sponsor_boosted():
    base = thin_trial(therapeutic_area="CNS / Psychiatry", sponsor="Acme Pharma")
    known = thin_trial(therapeutic_area="CNS / Psychiatry",
                       sponsor="Neumora Therapeutics, Inc.")
    assert score_trial(known) > score_trial(base)


def test_grant_animal_study_excluded():
    g = make_grant(human_subjects=0)
    assert score_grant(g) == 0


def test_grant_cns_adherence_beats_metabolic():
    # Thin grants (no award $/linked trial/dates) so the area term is the swing.
    def thin_grant(**over):
        g = dict(title="t", abstract="x", conditions=None, therapeutic_area="Other",
                 amount_usd=None, country=None, award_date=None, start_date=None,
                 end_date=None, linked_trial_id=None, organization=None, pi_name=None)
        g.update(over)
        return g
    cns = thin_grant(
        therapeutic_area="CNS / Psychiatry", conditions="schizophrenia",
        title="Medication adherence in schizophrenia",
        abstract="A clinical trial of medication adherence in patients with "
                 "schizophrenia taking oral antipsychotics. " + "x" * 250)
    metabolic = thin_grant(
        therapeutic_area="Diabetes", conditions="diabetes",
        title="Medication adherence in diabetes",
        abstract="A clinical trial of medication adherence in patients with "
                 "diabetes taking oral therapy. " + "x" * 250)
    assert score_grant(cns) > score_grant(metabolic)


# ── therapeutic-area taxonomy must stay consistent across the 3 files ──────────

def test_area_taxonomy_consistent():
    """Every area classify_area can emit must have a scoring._AREA_FIT entry, and
    every non-Other area must have a news_nlp._AREA_TO_CATEGORY entry. Guards the
    silent -14 'off-core' penalty / 'Off-focus' label a mismatch would otherwise
    apply with no error (the 4-file split flagged in review)."""
    import news_nlp
    import text_match

    KNOWN_AREAS = {
        "Other", "CNS / Psychiatry", "Neurology", "Metabolic / GLP-1", "Diabetes",
        "Cardiovascular", "Liver / NASH", "Renal", "Adherence / Outcomes",
    }
    for area in KNOWN_AREAS:
        assert area in scoring._AREA_FIT, f"{area!r} missing from scoring._AREA_FIT"
    for area in KNOWN_AREAS - {"Other"}:
        assert area in news_nlp._AREA_TO_CATEGORY, \
            f"{area!r} missing from news_nlp._AREA_TO_CATEGORY"

    # classify_area must never return a bucket outside the known taxonomy.
    samples = [
        "schizophrenia antipsychotic trial", "parkinson disease therapy",
        "obesity glp-1 weight loss", "type 2 diabetes insulin",
        "heart failure cardiovascular", "nash fatty liver", "chronic kidney disease",
        "medication adherence outcomes", "lung cancer tumor oncology",
        "completely unrelated subject matter",
    ]
    for s in samples:
        assert text_match.classify_area(s) in KNOWN_AREAS
