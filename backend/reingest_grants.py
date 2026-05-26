import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from grants.nih_reporter import pull_nih_reporter
from grants.usaspending import pull_usaspending
from grants.cordis import pull_cordis
from grants.ukri import pull_ukri
from grants.pcori import pull_pcori
from grants.aha import pull_aha
from grants.ada import pull_ada
from grant_linker import run_grant_linker

steps = [
    ("NIH RePORTER", pull_nih_reporter),
    ("USASpending", pull_usaspending),
    ("CORDIS", pull_cordis),
    ("UKRI", pull_ukri),
    ("PCORI", pull_pcori),
    ("AHA", pull_aha),
    ("ADA", pull_ada),
    ("Linker", run_grant_linker),
]

for name, fn in steps:
    print(f"{name}...")
    try:
        fn()
    except Exception as e:
        print(f"  ERROR: {e}")

print("Done.")
