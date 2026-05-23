import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct_puller import pull_all
from ctis_puller import pull_all_ctis
from eudract_puller import pull_all_eudract
from isrctn_puller import pull_all_isrctn
from ntr_puller import pull_all_ntr
from anzctr_puller import pull_all_anzctr
from drks_puller import pull_all_drks
from jrct_puller import pull_all_jrct
from cris_puller import pull_all_cris

steps = [
    ("ClinicalTrials.gov", pull_all),
    ("CTIS", pull_all_ctis),
    ("EU-CTR", pull_all_eudract),
    ("ISRCTN", pull_all_isrctn),
    ("NTR", pull_all_ntr),
    ("ANZCTR", pull_all_anzctr),
    ("DRKS", pull_all_drks),
    ("jRCT", pull_all_jrct),
    ("CRIS", pull_all_cris),
]

for i, (name, fn) in enumerate(steps, 1):
    print(f"Step {i}/{len(steps)} — {name}...")
    try:
        fn()
    except Exception as e:
        print(f"  ERROR in {name}: {e} — continuing")

print("Done.")
