"""build_grant_record centralizes the derivation block the 7 grant pullers each
used to repeat (grant-record fan-out). These pin that the derived columns match
the underlying extractors and survive an upsert round-trip, so the refactor is
behavior-preserving."""
import grant_utils as gu
from grant_utils import (
    build_grant_record, GRANT_RECORD_FIELDS, _GRANT_COLUMNS,
    _DERIVED_GRANT_FIELDS, upsert_grant,
)
from db import get_connection


def test_canonical_field_set_is_single_source():
    assert _GRANT_COLUMNS == set(GRANT_RECORD_FIELDS)
    assert set(_DERIVED_GRANT_FIELDS) <= set(GRANT_RECORD_FIELDS)


def test_derived_fields_match_extractors():
    text = ("A randomized clinical trial in patients with schizophrenia of an oral "
            "antipsychotic; see NCT01234567.")
    r = build_grant_record(text, id="G1", source="TEST", title="t")
    assert r["therapeutic_area"] == gu.classify_area(text)
    assert r["conditions"] == gu.extract_conditions(text)
    assert r["interventions"] == gu.extract_interventions(text)
    assert r["phase_mentioned"] == gu.extract_phase(text)
    assert r["human_subjects"] == int(gu.is_human_subjects(text))
    assert r["linked_trial_id"] == "NCT01234567"
    assert r["has_trial_link"] == 1
    assert r["id"] == "G1" and r["source"] == "TEST"   # explicit fields preserved


def test_explicit_field_overrides_derived():
    text = "preclinical mouse study, no humans"        # is_human_subjects → False
    r = build_grant_record(text, id="G2", source="T", human_subjects=1, linked_trial_id=None)
    assert r["human_subjects"] == 1                    # explicit wins
    assert r["has_trial_link"] == 0                    # consistent with explicit linked_trial_id


def test_animal_grant_gated_human_subjects_zero():
    text = "in vitro assays in transgenic mice of tumor metabolism"
    assert build_grant_record(text, id="G3", source="T")["human_subjects"] == 0


def test_upsert_roundtrip():
    text = "clinical trial in adults with depression, oral therapy"
    rec = build_grant_record(text, id="TEST-GRANT-RT", source="TEST", title="RT",
                             abstract=text, amount_usd=100000, country="US")
    conn = get_connection()
    try:
        upsert_grant(rec, conn)
        conn.commit()
        row = conn.execute("SELECT * FROM grants WHERE id = 'TEST-GRANT-RT'").fetchone()
        assert row is not None
        assert row["therapeutic_area"] == rec["therapeutic_area"]
        assert row["human_subjects"] == rec["human_subjects"]
        assert row["conditions"] == rec["conditions"]
        conn.execute("DELETE FROM grants WHERE id = 'TEST-GRANT-RT'")
        conn.commit()
    finally:
        conn.close()


def test_grant_record_fields_match_db_schema():
    """Every canonical grant field must be a real column on the grants table, so a
    built record can't fail at INSERT time — caught here in CI, not at ingest."""
    conn = get_connection()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(grants)")}
    finally:
        conn.close()
    missing = [f for f in GRANT_RECORD_FIELDS if f not in cols]
    assert not missing, f"GRANT_RECORD_FIELDS not in grants schema: {missing}"
