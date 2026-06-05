import os
import sys
import time
import signal
import threading
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Per-step watchdog: abandon any source that runs longer than this and move on,
# so one stuck puller can't freeze the whole pipeline (as ISRCTN once did).
# Slowest legit step seen is CTIS (~18 min); default 30 min leaves margin.
STEP_TIMEOUT = int(os.environ.get("AICURE_STEP_TIMEOUT", "1800"))


class _StepTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _StepTimeout()

from ct_puller import pull_all
from ctis_puller import pull_all_ctis
from ictrp_puller import pull_all_ictrp
from isrctn_puller import pull_all_isrctn
from cris_puller import pull_all_cris
from rss_parser import parse_all_feeds
from linker import run_linker
from org_extractor import extract_all_orgs
from merge_detector import run_merge_detection
from grants.nih_reporter import pull_nih_reporter
from grants.usaspending import pull_usaspending
from grants.cordis import pull_cordis
from grants.ukri import pull_ukri
from grants.pcori import pull_pcori
from grants.aha import pull_aha
from grants.ada import pull_ada
from grant_linker import run_grant_linker

STEPS = [
    ("ClinicalTrials.gov", pull_all),
    ("CTIS", pull_all_ctis),
    ("ICTRP (ANZCTR, DRKS, jRCT, NTR + others)", pull_all_ictrp),
    ("ISRCTN", pull_all_isrctn),
    ("CRIS", pull_all_cris),
    ("RSS feeds", parse_all_feeds),
    ("Linker", run_linker),
    ("Organizations", extract_all_orgs),
    ("Merge detection", run_merge_detection),
    ("NIH RePORTER", pull_nih_reporter),
    ("USASpending", pull_usaspending),
    ("CORDIS", pull_cordis),
    ("UKRI", pull_ukri),
    ("PCORI", pull_pcori),
    ("AHA", pull_aha),
    ("ADA", pull_ada),
    ("Grant linker", run_grant_linker),
]


def run():
    failures = []
    started = time.time()

    # SIGALRM only works on the main thread (Unix). Skip the watchdog otherwise.
    use_watchdog = (hasattr(signal, "SIGALRM")
                    and threading.current_thread() is threading.main_thread())
    if use_watchdog:
        signal.signal(signal.SIGALRM, _on_alarm)

    for i, (name, fn) in enumerate(STEPS, 1):
        print(f"Step {i}/{len(STEPS)} — {name}...")
        step_start = time.time()
        if use_watchdog:
            signal.alarm(STEP_TIMEOUT)
        try:
            fn()
            print(f"  done ({time.time() - step_start:.1f}s)")
        except _StepTimeout:
            failures.append((name, f"timed out after {STEP_TIMEOUT}s"))
            print(f"  TIMEOUT in {name} after {STEP_TIMEOUT}s — skipping to next step")
        except Exception as e:
            failures.append((name, str(e)))
            print(f"  ERROR in {name}: {e}")
            traceback.print_exc()
        finally:
            if use_watchdog:
                signal.alarm(0)

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
