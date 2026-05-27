import sys
import os
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ictrp_puller import pull_all_ictrp
from db import get_connection

FORCE = "--force" in sys.argv
STALE_DAYS = 7


def _last_ictrp_run():
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT MAX(ingested_at) FROM trials "
            "WHERE chictr_id IS NOT NULL OR anzctr_id IS NOT NULL "
            "OR drks_id IS NOT NULL OR jrct_id IS NOT NULL"
        ).fetchone()
        conn.close()
        val = row[0] if row else None
        return datetime.fromisoformat(val) if val else None
    except Exception:
        return None


if not FORCE:
    last = _last_ictrp_run()
    if last and last > datetime.utcnow() - timedelta(days=STALE_DAYS):
        age = (datetime.utcnow() - last).days
        print(f"ICTRP skipped (last run {age}d ago, use --force to re-run)")
        sys.exit(0)

pull_all_ictrp()
print("Done.")
