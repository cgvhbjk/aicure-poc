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


def _backfill_table(conn, table, scorer):
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    updates = [(scorer(dict(r)), r["id"]) for r in rows]
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
        n_grants = _backfill_table(conn, "grants", score_grant)
        n_trials = _backfill_table(conn, "trials", score_trial)
        print(f"[score_backfill] scored {n_grants} grants, {n_trials} trials")
        return n_grants, n_trials
    finally:
        if own:
            conn.close()


if __name__ == "__main__":
    backfill()
    sys.exit(0)
