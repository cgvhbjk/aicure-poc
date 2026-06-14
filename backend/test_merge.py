"""End-to-end tests for the destructive merge path: confirm deletes the loser and
reassigns its FK rows to the survivor; undo restores the pre-merge world from the
snapshot. The deep-review flagged this as the only data-destroying code with no
tests (P2-7), and P2-2 specifically: grant_trial_links must follow the survivor on
a trial merge (and come back on undo) instead of orphaning a now-deleted trial_id.

These run against the throwaway DB conftest.py points AICURE_DB_PATH at, so they
insert and delete freely. Each test uses a disjoint id prefix (MT_/MO_) so the
shared session DB stays collision-free.
"""
import api  # noqa: F401  (ensures the app module — and its DB schema — is imported)
from api import MergeConfirm, confirm_merge, undo_merge
from db import get_connection


def _orphans(conn):
    """grant_trial_links rows whose trial_id no longer names a real trial."""
    return conn.execute(
        "SELECT COUNT(*) FROM grant_trial_links gl "
        "LEFT JOIN trials t ON t.id = gl.trial_id WHERE t.id IS NULL"
    ).fetchone()[0]


# ── trial merge (P2-2: grant_trial_links) ─────────────────────────────────────

def _seed_trial_merge():
    conn = get_connection()
    try:
        conn.execute("INSERT INTO trials (id, title_brief) VALUES ('MT_SURV', 'survivor')")
        conn.execute("INSERT INTO trials (id, title_brief) VALUES ('MT_LOSE', 'loser')")
        conn.execute("INSERT INTO grants (id, title) VALUES ('MT_G', 'grant')")
        conn.execute(
            "INSERT INTO grant_trial_links (grant_id, trial_id, match_method) "
            "VALUES ('MT_G', 'MT_LOSE', 'seed')"
        )
        cur = conn.execute(
            "INSERT INTO merge_candidates (entity_type, record_a_id, record_b_id, status) "
            "VALUES ('trials', 'MT_SURV', 'MT_LOSE', 'PENDING')"
        )
        mid = cur.lastrowid
        conn.commit()
        return mid
    finally:
        conn.close()


def test_trial_merge_moves_grant_link_and_undo_restores_it():
    mid = _seed_trial_merge()

    confirm_merge(mid, MergeConfirm(surviving_id="MT_SURV", reviewed_by="test"))

    conn = get_connection()
    try:
        assert conn.execute("SELECT 1 FROM trials WHERE id='MT_LOSE'").fetchone() is None
        # P2-2: the link followed the survivor instead of dangling on a deleted trial
        assert conn.execute(
            "SELECT trial_id FROM grant_trial_links WHERE grant_id='MT_G'"
        ).fetchone()[0] == "MT_SURV"
        assert _orphans(conn) == 0
    finally:
        conn.close()

    undo_merge(mid)

    conn = get_connection()
    try:
        assert conn.execute("SELECT 1 FROM trials WHERE id='MT_LOSE'").fetchone() is not None
        # the link came back on the loser (snapshot restore)
        assert conn.execute(
            "SELECT trial_id FROM grant_trial_links WHERE grant_id='MT_G'"
        ).fetchone()[0] == "MT_LOSE"
        assert conn.execute(
            "SELECT status FROM merge_candidates WHERE id=?", (mid,)
        ).fetchone()[0] == "PENDING"
        assert _orphans(conn) == 0
    finally:
        conn.close()


# ── organization merge (the other destructive branch) ─────────────────────────

def _seed_org_merge():
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO organizations (id, canonical_name, trial_count) "
            "VALUES ('MO_SURV', 'Survivor Inc', 0)"
        )
        conn.execute(
            "INSERT INTO organizations (id, canonical_name, trial_count) "
            "VALUES ('MO_LOSE', 'Loser LLC', 1)"
        )
        conn.execute("INSERT INTO trials (id, title_brief) VALUES ('MO_T', 'trial')")
        conn.execute(
            "INSERT INTO trial_org_links (trial_id, org_id, role) "
            "VALUES ('MO_T', 'MO_LOSE', 'sponsor')"
        )
        cur = conn.execute(
            "INSERT INTO merge_candidates (entity_type, record_a_id, record_b_id, status) "
            "VALUES ('organizations', 'MO_SURV', 'MO_LOSE', 'PENDING')"
        )
        mid = cur.lastrowid
        conn.commit()
        return mid
    finally:
        conn.close()


def test_org_merge_moves_link_recomputes_count_and_undo_restores():
    mid = _seed_org_merge()

    confirm_merge(mid, MergeConfirm(surviving_id="MO_SURV", reviewed_by="test"))

    conn = get_connection()
    try:
        assert conn.execute("SELECT 1 FROM organizations WHERE id='MO_LOSE'").fetchone() is None
        assert conn.execute(
            "SELECT org_id FROM trial_org_links WHERE trial_id='MO_T'"
        ).fetchone()[0] == "MO_SURV"
        # trial_count recomputed from the moved link
        assert conn.execute(
            "SELECT trial_count FROM organizations WHERE id='MO_SURV'"
        ).fetchone()[0] == 1
    finally:
        conn.close()

    undo_merge(mid)

    conn = get_connection()
    try:
        assert conn.execute("SELECT 1 FROM organizations WHERE id='MO_LOSE'").fetchone() is not None
        assert conn.execute(
            "SELECT org_id FROM trial_org_links WHERE trial_id='MO_T'"
        ).fetchone()[0] == "MO_LOSE"
        # survivor's pre-merge trial_count (0) restored from the snapshot
        assert conn.execute(
            "SELECT trial_count FROM organizations WHERE id='MO_SURV'"
        ).fetchone()[0] == 0
    finally:
        conn.close()
