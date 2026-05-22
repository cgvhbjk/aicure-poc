import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct_puller import pull_all
from ctis_puller import pull_all_ctis
from eudract_puller import pull_all_eudract
from rss_parser import parse_all_feeds
from linker import run_linker

print("Step 1/5 — Pulling ClinicalTrials.gov...")
pull_all()
print("Step 2/5 — Pulling CTIS (EU)...")
pull_all_ctis()
print("Step 3/5 — Pulling EU-CTR / EudraCT...")
pull_all_eudract()
print("Step 4/5 — Parsing RSS feeds...")
parse_all_feeds()
print("Step 5/5 — Linking news to trials...")
run_linker()
print("Done.")
