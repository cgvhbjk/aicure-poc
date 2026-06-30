"""Human-subjects gate for grants (§3a) — exclude animal / preclinical work."""
from grant_utils import is_human_subjects


def test_animal_study_excluded():
    assert is_human_subjects("A study in transgenic mice of tumor metabolism") is False
    assert is_human_subjects("in vitro assays of a novel cell line") is False
    assert is_human_subjects("zebrafish model of neurodegeneration") is False


def test_human_study_kept():
    assert is_human_subjects("A clinical trial in patients with schizophrenia") is True
    assert is_human_subjects("randomized trial enrolling adults with depression") is True


def test_animal_with_human_cue_kept():
    # A grant that mentions mouse models but is ultimately a human clinical study.
    assert is_human_subjects(
        "mouse models informing a clinical trial in patients") is True


def test_empty_or_unknown_kept():
    assert is_human_subjects("") is True
    assert is_human_subjects(None) is True
