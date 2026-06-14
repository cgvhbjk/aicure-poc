"""Characterization tests for the SQL WHERE/ORDER BY builders shared by the list
and CSV-export endpoints (api.py).

These are the riskiest pure functions in the backend: a placeholder/param count
mismatch throws at execute time, and an un-whitelisted ORDER BY column would be a
SQL-injection vector via ?sort=. The deep-review flagged them as untested (P2-7);
this pins their contract so a future refactor can't silently break it.
"""
import pytest
from fastapi import HTTPException

import api


# ── helpers ───────────────────────────────────────────────────────────────────

def _parity(where_sql, params):
    """Every ? must bind exactly one param or sqlite raises 'Incorrect number of
    bindings' at execute time — the one invariant a WHERE-builder must never break."""
    assert where_sql.count("?") == len(params)


def trials_where(**kw):
    base = dict(q=None, status=None, phase=None, therapeutic_area=None, country=None,
                has_news=None, has_euct_id=None, registry=None, sponsor=None,
                sponsor_not=None, min_enrollment=None, max_enrollment=None,
                start_date_from=None, start_date_to=None, completion_date_from=None,
                completion_date_to=None)
    base.update(kw)
    return api._trials_where(**base)


def news_where(**kw):
    base = dict(q=None, source=None, linked_only=None, is_trial_announcement=None,
                is_trial_results=None, published_at_from=None, published_at_to=None,
                drug_mentioned=None, drug_mentioned_not=None, phase_mentioned=None,
                phase_mentioned_not=None, sponsor_mentioned=None,
                sponsor_mentioned_not=None)
    base.update(kw)
    return api._news_where(**base)


def grants_where(**kw):
    base = dict(q=None, source=None, therapeutic_area=None, status=None, country=None,
                country_q=None, country_q_not=None, has_trial_link=None, min_amount=None,
                max_amount=None, activity_code=None, org_type=None, research_type=None,
                agency_division=None, fiscal_year_min=None, fiscal_year_max=None,
                award_date_from=None, award_date_to=None)
    base.update(kw)
    return api._grants_where(**base)


# ── _order_by_clause: the ?sort= injection guard ──────────────────────────────

def test_order_by_falls_back_on_unknown_column():
    sql = api._order_by_clause("evil; DROP TABLE trials", "desc",
                               {"aicure_fit", "id"}, "aicure_fit", "id DESC")
    assert sql == "ORDER BY aicure_fit DESC, id DESC"
    assert "DROP" not in sql


def test_order_by_uses_whitelisted_column():
    sql = api._order_by_clause("id", "desc", {"aicure_fit", "id"}, "aicure_fit", "id DESC")
    assert sql == "ORDER BY id DESC, id DESC"


def test_order_by_desc_has_no_null_prefix_asc_does():
    desc = api._order_by_clause("id", "desc", {"id"}, "id", "id DESC")
    asc = api._order_by_clause("id", "asc", {"id"}, "id", "id DESC")
    assert "IS NULL" not in desc            # index-friendly hot path
    assert asc == "ORDER BY (id IS NULL), id ASC, id DESC"


def test_order_by_applies_table_prefix():
    sql = api._order_by_clause("name", "desc", {"name"}, "name", "id DESC", prefix="t.")
    assert sql == "ORDER BY t.name DESC, id DESC"


def test_order_by_defaults_direction_to_desc():
    assert api._order_by_clause("id", None, {"id"}, "id", "id DESC") == \
        "ORDER BY id DESC, id DESC"


# ── _trials_where ─────────────────────────────────────────────────────────────

def test_trials_where_empty():
    assert trials_where() == ("", [])


def test_trials_where_q_is_lowercased_four_columns():
    sql, params = trials_where(q="Foo")
    assert params == ["%foo%"] * 4
    assert sql.count("LIKE") == 4


def test_trials_where_status_in_list():
    sql, params = trials_where(status=["RECRUITING", "COMPLETED"])
    assert "status IN (?,?)" in sql
    assert params == ["RECRUITING", "COMPLETED"]


def test_trials_where_country_matches_lead_or_json_array():
    sql, params = trials_where(country=["United States"])
    assert "lead_country = ?" in sql and "countries LIKE ?" in sql
    assert params == ["United States", '%"United States"%']


def test_trials_where_has_news_toggles_in_vs_not_in():
    assert "id IN (SELECT" in trials_where(has_news=True)[0]
    assert "id NOT IN (SELECT" in trials_where(has_news=False)[0]


def test_trials_where_sponsor_escapes_like_wildcards():
    sql, params = trials_where(sponsor="50%_co")
    assert "ESCAPE" in sql
    assert params == [api._like_pattern("50%_co")]


def test_trials_where_enrollment_bounds():
    _, params = trials_where(min_enrollment=10, max_enrollment=20)
    assert params == [10, 20]


def test_trials_where_param_parity_full_combo():
    sql, params = trials_where(
        q="x", status=["A", "B"], phase=["P1"], therapeutic_area=["Diabetes"],
        country=["US"], has_news=True, has_euct_id=True, registry=["CTIS", "CTgov"],
        sponsor="acme", sponsor_not="evil", min_enrollment=1, max_enrollment=9,
        start_date_from="2026-01-01", start_date_to="2026-12-31",
        completion_date_from="2026-01-01", completion_date_to="2026-12-31")
    _parity(sql, params)


# ── _news_where ───────────────────────────────────────────────────────────────

def test_news_where_empty():
    assert news_where() == ("", [])


def test_news_where_announcement_flag_is_int():
    assert news_where(is_trial_announcement=True)[1] == [1]
    assert news_where(is_trial_announcement=False)[1] == [0]


def test_news_where_linked_only_takes_no_param():
    sql, params = news_where(linked_only=True)
    assert sql == "WHERE ni.trial_id IS NOT NULL" and params == []
    assert news_where(linked_only=False)[0] == "WHERE ni.trial_id IS NULL"


def test_news_where_published_bounds_normalized_via_iso_day():
    _, params = news_where(published_at_from="2026-03-04", published_at_to="2026-03-04")
    # to-bound is the exclusive next day so same-day timestamps still match
    assert params == ["2026-03-04", "2026-03-05"]


def test_news_where_param_parity_full_combo():
    sql, params = news_where(
        q="x", source=["PRNewswire", "BusinessWire"], linked_only=True,
        is_trial_announcement=True, is_trial_results=False,
        published_at_from="2026-01-01", published_at_to="2026-12-31",
        drug_mentioned="glp", drug_mentioned_not="aspirin",
        phase_mentioned="3", phase_mentioned_not="1",
        sponsor_mentioned="acme", sponsor_mentioned_not="evil")
    _parity(sql, params)


# ── _grants_where ─────────────────────────────────────────────────────────────

def test_grants_where_empty():
    assert grants_where() == ("", [])


def test_grants_where_in_lists_keep_param_order():
    sql, params = grants_where(source=["NIH"], status=["ACTIVE"])
    assert params == ["NIH", "ACTIVE"]
    assert sql.count("IN (?)") == 2


def test_grants_where_has_trial_link_is_int():
    assert grants_where(has_trial_link=True)[1] == [1]
    assert grants_where(has_trial_link=False)[1] == [0]


def test_grants_where_amount_and_fiscal_bounds():
    _, params = grants_where(min_amount=1000, max_amount=2000,
                             fiscal_year_min=2024, fiscal_year_max=2026)
    assert params == [1000, 2000, 2024, 2026]


def test_grants_where_award_date_bounds_normalized():
    _, params = grants_where(award_date_from="2026-03-04", award_date_to="2026-03-04")
    assert params == ["2026-03-04", "2026-03-05"]


def test_grants_where_param_parity_full_combo():
    sql, params = grants_where(
        q="x", source=["NIH"], therapeutic_area=["Diabetes"], status=["ACTIVE"],
        country=["United States"], country_q="united", country_q_not="china",
        has_trial_link=True, min_amount=1, max_amount=9, activity_code=["R01"],
        org_type=["University"], research_type=["Clinical"], agency_division=["NIDDK"],
        fiscal_year_min=2024, fiscal_year_max=2026,
        award_date_from="2026-01-01", award_date_to="2026-12-31")
    _parity(sql, params)


# ── helpers: _like_pattern + _iso_day ─────────────────────────────────────────

def test_like_pattern_escapes_and_lowercases():
    assert api._like_pattern("AB") == "%ab%"
    # %, _ and \ are LIKE metacharacters and must be backslash-escaped
    assert api._like_pattern("100%") == "%100\\%%"
    assert api._like_pattern("a_b") == "%a\\_b%"
    assert api._like_pattern("c\\d") == "%c\\\\d%"


def test_iso_day_normalizes_and_shifts():
    assert api._iso_day("2026-06-14") == "2026-06-14"
    assert api._iso_day("2026-06-14T23:59:59") == "2026-06-14"   # trims timestamp
    assert api._iso_day("2026-06-14", plus_days=1) == "2026-06-15"


def test_iso_day_rejects_garbage():
    with pytest.raises(HTTPException) as ei:
        api._iso_day("not-a-date")
    assert ei.value.status_code == 422
