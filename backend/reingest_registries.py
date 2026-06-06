import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct_puller import pull_all
from ctis_puller import pull_all_ctis
from ictrp_puller import pull_all_ictrp
from isrctn_puller import pull_all_isrctn
from cris_puller import pull_all_cris

steps = [
    ("ClinicalTrials.gov", pull_all),
    ("CTIS", pull_all_ctis),
    ("ICTRP", pull_all_ictrp),
    ("ISRCTN", pull_all_isrctn),
    ("CRIS", pull_all_cris),
]

for i, (name, fn) in enumerate(steps, 1):
    print(f"Step {i}/{len(steps)} — {name}...")
    try:
        fn()
    except Exception as e:
        print(f"  ERROR in {name}: {e} — continuing")

from score_backfill import backfill
print("Scoring (aicure_fit)...")
backfill()
print("Done.")
