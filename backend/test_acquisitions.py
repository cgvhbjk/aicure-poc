"""Acquisition / M&A news stream (§4) — incl. private-company buyouts.

A buyout headline must classify as the 'acquisition' event type and surface as a
lead regardless of any trial touchpoint ("this got bought — why?").
"""
from rss_parser import classify_event_type
import news_nlp


def test_buyout_headline_classified_as_acquisition():
    assert classify_event_type("BigPharma to acquire SmallBio in $2B deal") == "acquisition"
    assert classify_event_type("NovaCorp completes acquisition of GeneCo") == "acquisition"
    # Acquisition precedence beats other signals in the same headline.
    assert classify_event_type(
        "Pharma to acquire biotech, will initiate a Phase 2 trial") == "acquisition"


def test_acquisition_applies_without_trial_touchpoint():
    item = {
        "title": "Acme Pharma to acquire NovaBio to expand its CNS pipeline",
        "body_snippet": "Acme Pharma agreed to acquire NovaBio to bolster its "
                        "depression pipeline.",
        "event_type": "acquisition",
        "url": "http://example.com/deal",
    }
    a = news_nlp.analyze(item, use_llm=False)   # force rules path
    assert a["applies_to_aicure"] is True
    assert a["aicure_category"] == news_nlp.ACQUISITION_CATEGORY
    assert a["not_yet_started"] is True
    assert a.get("acquirer") and a.get("target")
    assert "NovaBio" in (a.get("target") or "")


def test_acquisition_skips_llm_backend(monkeypatch):
    """Even with an LLM backend configured (the deploy default), acquisitions take
    the deterministic rules path — the LLM schema doesn't model acquirer/target,
    so routing M&A through it would render the digest's M&A cards blank."""
    monkeypatch.setenv("AICURE_NLP_BACKEND", "api")
    called = {"llm": False}

    def fake_llm(item, full_text=None):
        called["llm"] = True
        return {"aicure_category": "Off-focus", "method": "llm",
                "applies_to_aicure": False}
    monkeypatch.setattr(news_nlp, "_analyze_llm", fake_llm)

    item = {
        "title": "Acme Pharma to acquire NovaBio",
        "body_snippet": "Acme Pharma agreed to acquire NovaBio to bolster its "
                        "depression pipeline.",
        "event_type": "acquisition",
        "url": "http://example.com/deal2",
    }
    a = news_nlp.analyze(item)                 # no use_llm → would hit the LLM without the hoist
    assert called["llm"] is False              # acquisition never reaches the LLM
    assert a["aicure_category"] == news_nlp.ACQUISITION_CATEGORY
    assert a.get("acquirer") and a.get("target")
