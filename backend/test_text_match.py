"""Characterization tests for the consolidated text_match helpers.

These pin the single-source-of-truth classifier/keyword behaviour that trials
(ct_puller), grants (grant_utils), and news (news_nlp) now all share, so a future
edit to one path can't silently re-introduce the drift this module removed.
"""
from text_match import classify_area, flag, DRUG_KEYWORDS


def test_metabolic_bucket():
    assert classify_area("obesity and weight loss") == "Metabolic / GLP-1"
    assert classify_area("metabolic syndrome cohort") == "Metabolic / GLP-1"
    assert classify_area("semaglutide trial") == "Metabolic / GLP-1"


def test_oncology_guard():
    # Cancer text with no cardiometabolic anchor must NOT leak into Metabolic.
    assert classify_area("pancreatic cancer tumor metabolism") == "Other"
    # ...but a strong CM anchor (e.g. semaglutide for cancer cachexia) keeps it.
    assert classify_area("tumor cachexia treated with semaglutide") == "Metabolic / GLP-1"


def test_other_buckets():
    assert classify_area("type 2 diabetes, insulin") == "Diabetes"
    assert classify_area("heart failure with reduced ejection fraction") == "Cardiovascular"
    assert classify_area("NASH / NAFLD fibrosis") == "Liver / NASH"
    assert classify_area("chronic kidney disease (CKD)") == "Renal"
    assert classify_area("medication adherence and compliance") == "Adherence / Outcomes"
    assert classify_area("a study of foot fungus") == "Other"


def test_classify_area_handles_none_and_empty():
    assert classify_area(None) == "Other"
    assert classify_area("") == "Other"


def test_flag_is_case_insensitive_and_none_safe():
    assert flag("Contains EPRO assessment", ["epro"]) is True
    assert flag("nothing here", ["epro", "ecoa"]) is False
    assert flag(None, ["epro"]) is False


def test_drug_keywords_include_sglt2_and_dpp4_family():
    # The news side previously lacked these; the union now covers them.
    for d in ("sglt2", "dpp-4", "empagliflozin", "glp-1", "semaglutide"):
        assert d in DRUG_KEYWORDS


# ── search-net ↔ classifier drift guards (canonical TARGET_CONDITIONS) ──────────

def test_target_conditions_classify_to_declared_area():
    """Every canonical search condition MUST classify into the area it's declared
    under — otherwise the CT.gov search net and the scorer/classifier have drifted
    (we'd search for a condition the scorer then files as off-focus and suppresses)."""
    from text_match import TARGET_CONDITIONS
    for area, term in TARGET_CONDITIONS:
        assert classify_area(term) == area, \
            f"search term {term!r} classifies as {classify_area(term)!r}, not {area!r}"


def test_ct_conditions_derived_from_taxonomy_and_in_focus():
    import ct_puller
    from text_match import TARGET_CONDITIONS
    assert ct_puller.CONDITIONS == [term for _area, term in TARGET_CONDITIONS]
    for term in ct_puller.CONDITIONS:
        assert classify_area(term) != "Other", f"searched term {term!r} is off-focus"
