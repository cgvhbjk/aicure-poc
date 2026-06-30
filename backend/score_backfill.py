"""(Re)compute the stored AiCure fit score for every grant and trial.

The score (scoring.py) is deterministic, so we precompute it into the
`aicure_fit` column once instead of scoring on every API request. This lets the
Funding/Trials grids sort and paginate on the score server-side.

Run standalone (`python score_backfill.py`) or call `backfill()` at the end of
an ingest so freshly pulled rows get scored.
"""
import sys

from db import get_connection
from scoring import score_grant, score_trial

# The exact columns each scorer reads (see scoring.py). Selecting only these
# instead of `SELECT *` keeps the unused wide TEXT columns (inclusion/exclusion
# criteria, raw_snapshot_path, abstracts of unrelated fields, endpoints, …) out
# of memory on the full-table rescore. Hardcoded allowlists — not user input.
# NOTE: this narrows the row width, NOT which rows are scored: every row is still
# rescored on each pass, because the score is time-dependent (immediacy decays as
# start/award dates pass), so a "skip unchanged rows" optimization would freeze
# stale scores. Keep these in sync with scoring.py if the scorer reads new fields.
_GRANT_SCORE_COLS = (
    "id", "title", "abstract", "conditions", "therapeutic_area", "amount_usd",
    "country", "award_date", "start_date", "end_date", "organization",
    "pi_name", "linked_trial_id", "phase_mentioned", "sponsor_funder",
    "human_subjects",
)
_TRIAL_SCORE_COLS = (
    "id", "title_brief", "brief_summary", "conditions", "interventions",
    "therapeutic_area", "phase", "status", "enrollment", "num_sites",
    "lead_country", "sponsor", "cro_named", "pi_email", "registry_sources",
    "epro_ecoa", "digital_biomarkers", "dct_elements", "start_date",
    "primary_completion", "study_completion",
)


def _score_one(scorer, row):
    """Score a single row, isolating failures so one malformed row can't abort
    the whole pass. A row we can't score gets 0 (sorts low) rather than NULL,
    so the grid never strands a stale/un-backfilled score."""
    try:
        return scorer(dict(row))
    except Exception as e:
        print(f"[score_backfill] could not score row {row['id']}: {e}")
        return 0


def _backfill_table(conn, table, scorer, columns):
    collist = ", ".join(columns)
    rows = conn.execute(f"SELECT {collist} FROM {table}").fetchall()
    updates = [(_score_one(scorer, r), r["id"]) for r in rows]
    if updates:
        conn.executemany(
            f"UPDATE {table} SET aicure_fit = ? WHERE id = ?", updates
        )
    conn.commit()
    return len(updates)


def backfill(conn=None):
    """Score every grant + trial. Reuses an open connection if given."""
    own = conn is None
    conn = conn or get_connection()
    try:
        n_grants = _backfill_table(conn, "grants", score_grant, _GRANT_SCORE_COLS)
        n_trials = _backfill_table(conn, "trials", score_trial, _TRIAL_SCORE_COLS)
        print(f"[score_backfill] scored {n_grants} grants, {n_trials} trials")
        # Refresh planner statistics here because every ingest path (ingest.py,
        # both reingest scripts, the daily rescore job) ends in backfill() —
        # one call site keeps sqlite_stat1 current after every bulk write.
        # Without stats the planner guesses index selectivity blind.
        conn.execute("ANALYZE")
        conn.commit()
        return n_grants, n_trials
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    backfill()
    sys.exit(0)
