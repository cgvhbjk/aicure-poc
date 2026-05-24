import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ct_puller import pull_all
from ctis_puller import pull_all_ctis
from eudract_puller import pull_all_eudract
from isrctn_puller import pull_all_isrctn
from cris_puller import pull_all_cris
from rss_parser import parse_all_feeds
from linker import run_linker
from org_extractor import extract_all_orgs
from merge_detector import run_merge_detection

STEPS = [
    ("ClinicalTrials.gov", pull_all),
    ("CTIS", pull_all_ctis),
    ("EU-CTR", pull_all_eudract),
    ("ISRCTN", pull_all_isrctn),
    ("CRIS", pull_all_cris),
    ("RSS feeds", parse_all_feeds),
    ("Linker", run_linker),
    ("Organizations", extract_all_orgs),
    ("Merge detection", run_merge_detection),
]


def run():
    failures = []
    started = time.time()

    for i, (name, fn) in enumerate(STEPS, 1):
        print(f"Step {i}/{len(STEPS)} — {name}...")
        step_start = time.time()
        try:
            fn()
            print(f"  done ({time.time() - step_start:.1f}s)")
        except Exception as e:
            failures.append((name, str(e)))
            print(f"  ERROR in {name}: {e}")
            traceback.print_exc()

    elapsed = time.time() - started
    print(f"\nFinished in {elapsed:.1f}s. "
          f"{len(STEPS) - len(failures)}/{len(STEPS)} steps OK.")

    if failures:
        print("Failed steps:")
        for name, err in failures:
            print(f"  - {name}: {err}")
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
