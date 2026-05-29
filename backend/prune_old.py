import sys
from db import get_connection

_TRIAL_FK_TABLES = [
    ("registry_source_records", "trial_id"),
    ("trial_org_links", "trial_id"),
    ("trial_news_links", "trial_id"),
    ("grant_trial_links", "trial_id"),
    ("merge_candidates", "record_a_id"),
    ("merge_candidates", "record_b_id"),
]


def prune_old(dry_run=False):
    conn = get_connection()
    cutoff = "date('now', '-1 year')"

    old_trial_ids = [r[0] for r in conn.execute(
        f"SELECT id FROM trials WHERE primary_completion < {cutoff} AND primary_completion IS NOT NULL"
        f" AND status IN ('COMPLETED', 'TERMINATED', 'WITHDRAWN', 'SUSPENDED')"
    ).fetchall()]

    old_grant_ids = [r[0] for r in conn.execute(
        f"SELECT id FROM grants WHERE end_date < {cutoff} AND end_date IS NOT NULL"
    ).fetchall()]

    if dry_run:
        print(f"Would prune {len(old_trial_ids)} trials, {len(old_grant_ids)} grants")
        conn.close()
        return len(old_trial_ids), len(old_grant_ids)

    for tid in old_trial_ids:
        for table, col in _TRIAL_FK_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE {col} = ?", (tid,))

    if old_trial_ids:
        placeholders = ",".join("?" * len(old_trial_ids))
        conn.execute(f"DELETE FROM trials WHERE id IN ({placeholders})", old_trial_ids)
        conn.execute(
            "UPDATE trials SET has_news = 0 WHERE has_news = 1 AND id NOT IN "
            "(SELECT DISTINCT trial_id FROM trial_news_links WHERE trial_id IS NOT NULL)"
        )

    for gid in old_grant_ids:
        conn.execute("DELETE FROM grant_trial_links WHERE grant_id = ?", (gid,))

    if old_grant_ids:
        placeholders = ",".join("?" * len(old_grant_ids))
        conn.execute(f"DELETE FROM grants WHERE id IN ({placeholders})", old_grant_ids)

    conn.commit()
    conn.close()
    print(f"Pruned {len(old_trial_ids)} trials, {len(old_grant_ids)} grants")
    return len(old_trial_ids), len(old_grant_ids)


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    prune_old(dry_run=dry)
