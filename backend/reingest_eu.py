import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ctis_puller import pull_all_ctis
from eudract_puller import pull_all_eudract

print("Step 1/2 — Pulling CTIS (EU)...")
pull_all_ctis()
print("Step 2/2 — Pulling EU-CTR / EudraCT...")
pull_all_eudract()
print("Done.")
