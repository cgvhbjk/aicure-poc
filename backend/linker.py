import json
from db import get_connection


def run_linker():
    conn = get_connection()

    # Step 1 — NCT match
    news_with_nct = conn.execute(
        "SELECT id, nct_ids_found FROM news_items WHERE nct_ids_found IS NOT NULL AND nct_ids_found != '[]'"
    ).fetchall()

    nct_matches = 0
    for item in news_with_nct:
        news_id = item["id"]
        try:
            nct_ids = json.loads(item["nct_ids_found"] or "[]")
        except Exception:
            continue
        for nct_id in nct_ids:
            exists = conn.execute("SELECT 1 FROM trials WHERE id = ?", (nct_id,)).fetchone()
            if not exists:
                continue
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO trial_news_links (trial_id, news_id, match_method) VALUES (?, ?, 'NCT_MATCH')",
                    (nct_id, news_id),
                )
                conn.execute(
                    "UPDATE news_items SET trial_id = ? WHERE id = ? AND trial_id IS NULL",
                    (nct_id, news_id),
                )
                conn.execute("UPDATE trials SET has_news = 1 WHERE id = ?", (nct_id,))
                nct_matches += 1
            except Exception as e:
                print(f"  [WARN] NCT link error: {e}")
    conn.commit()
    print(f"  NCT matches: {nct_matches}")

    # Step 2 — Fuzzy match (sponsor + drug)
    unlinked = conn.execute(
        """SELECT id, sponsor_mentioned, drug_mentioned FROM news_items
           WHERE trial_id IS NULL
             AND (sponsor_mentioned IS NOT NULL OR drug_mentioned IS NOT NULL)"""
    ).fetchall()

    fuzzy_matches = 0
    for item in unlinked:
        news_id = item["id"]
        sponsor = item["sponsor_mentioned"]
        drug = item["drug_mentioned"]

        query = "SELECT id FROM trials WHERE 1=1"
        params = []
        if sponsor:
            query += " AND LOWER(sponsor) LIKE ?"
            params.append(f"%{sponsor.lower()}%")
        if drug:
            query += " AND LOWER(interventions) LIKE ?"
            params.append(f"%{drug.lower()}%")

        if not params:
            continue

        trial = conn.execute(query + " LIMIT 1", params).fetchone()
        if not trial:
            continue

        trial_id = trial["id"]
        existing = conn.execute(
            "SELECT 1 FROM trial_news_links WHERE trial_id = ? AND news_id = ?",
            (trial_id, news_id),
        ).fetchone()
        if existing:
            continue

        try:
            conn.execute(
                "INSERT OR IGNORE INTO trial_news_links (trial_id, news_id, match_method) VALUES (?, ?, 'FUZZY')",
                (trial_id, news_id),
            )
            conn.execute(
                "UPDATE news_items SET trial_id = ? WHERE id = ? AND trial_id IS NULL",
                (trial_id, news_id),
            )
            conn.execute("UPDATE trials SET has_news = 1 WHERE id = ?", (trial_id,))
            fuzzy_matches += 1
        except Exception as e:
            print(f"  [WARN] Fuzzy link error: {e}")

    conn.commit()
    print(f"  Fuzzy matches: {fuzzy_matches}")
    conn.close()
