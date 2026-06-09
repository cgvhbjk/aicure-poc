import sys
import os
import traceback
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grants.nih_reporter import pull_nih_reporter
from grants.usaspending import pull_usaspending
from grants.cordis import pull_cordis
from grants.ukri import pull_ukri
from grants.pcori import pull_pcori
from grants.aha import pull_aha
from grants.ada import pull_ada
from grant_linker import run_grant_linker
from db import get_connection

STALE_DAYS = 7  # re-run a connector if its data is older than this


def _last_ingested(source: str):
    """Return the most recent ingested_at timestamp for this source, or None."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT MAX(ingested_at) FROM grants WHERE source = ?", (source,)
        ).fetchone()
        conn.close()
        val = row[0] if row else None
        return datetime.fromisoformat(val) if val else None
    except Exception:
        return None


steps = [
    ("NIH RePORTER",  pull_nih_reporter,  "NIH_REPORTER"),
    ("USASpending",   pull_usaspending,   "USASPENDING"),
    ("CORDIS",        pull_cordis,        "CORDIS"),
    ("UKRI",          pull_ukri,          "UKRI"),
    ("PCORI",         pull_pcori,         "PCORI"),
    ("AHA",           pull_aha,           "AHA"),
    ("ADA",           pull_ada,           "ADA"),
    ("Linker",        run_grant_linker,   None),
]

def main():
    force = "--force" in sys.argv
    threshold = datetime.utcnow() - timedelta(days=STALE_DAYS)

    for name, fn, source_key in steps:
        if not force and source_key:
            last = _last_ingested(source_key)
            if last and last > threshold:
                age = (datetime.utcnow() - last).days
                print(f"{name}... skipped (last run {age}d ago, use --force to re-run)")
                continue
        print(f"{name}...")
        try:
            fn()
        except Exception as e:
            print(f"  ERROR: {e}")
            traceback.print_exc()

    from score_backfill import backfill
    print("Scoring (aicure_fit)...")
    backfill()
    print("Done.")


if __name__ == "__main__":
    main()
