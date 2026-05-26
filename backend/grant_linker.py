import re
from datetime import datetime

from db import get_connection

NCT_RE = re.compile(r'NCT\d{8}')


def run_grant_linker():
    conn = get_connection()

    grants = conn.execute(
        "SELECT id, title, abstract, pi_name FROM grants"
    ).fetchall()

    linked_nct = 0
    linked_pi = 0

    for grant in grants:
        grant_id = grant["id"]
        combined = f"{grant['title'] or ''} {grant['abstract'] or ''}"

        # Step 1: NCT match
        m = NCT_RE.search(combined)
        if m:
            nct_id = m.group(0)
            trial = conn.execute(
                "SELECT id FROM trials WHERE id = ?", (nct_id,)
            ).fetchone()
            if trial:
                conn.execute(
                    "INSERT OR IGNORE INTO grant_trial_links (grant_id, trial_id, match_method) "
                    "VALUES (?, ?, ?)",
                    (grant_id, nct_id, "NCT_MATCH"),
                )
                conn.execute(
                    "UPDATE grants SET has_trial_link = 1, linked_trial_id = ? WHERE id = ?",
                    (nct_id, grant_id),
                )
                linked_nct += 1
                continue

        # Step 2: PI last-name match (soft signal — no has_trial_link flag)
        pi_name = (grant["pi_name"] or "").strip()
        if pi_name:
            last_name = pi_name.split()[-1].lower() if pi_name.split() else ""
            if last_name and len(last_name) >= 3:
                trial = conn.execute(
                    "SELECT id FROM trials WHERE LOWER(pi_name) LIKE ? LIMIT 1",
                    (f"%{last_name}%",),
                ).fetchone()
                if trial:
                    conn.execute(
                        "INSERT OR IGNORE INTO grant_trial_links (grant_id, trial_id, match_method) "
                        "VALUES (?, ?, ?)",
                        (grant_id, trial["id"], "PI_MATCH"),
                    )
                    linked_pi += 1

    conn.commit()
    conn.close()
    print(f"  Grant linker: {linked_nct} NCT matches, {linked_pi} PI matches")
