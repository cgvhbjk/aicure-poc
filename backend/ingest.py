import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct_puller import pull_all
from rss_parser import parse_all_feeds
from linker import run_linker

print("Step 1/3 — Pulling ClinicalTrials.gov...")
pull_all()
print("Step 2/3 — Parsing RSS feeds...")
parse_all_feeds()
print("Step 3/3 — Linking news to trials...")
run_linker()
print("Done.")
