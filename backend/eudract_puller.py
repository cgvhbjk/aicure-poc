"""
EU-CTR / EudraCT puller.

EudraCT's legacy REST API (/ctr-search/rest/search) was retired when the system
migrated to CTIS in 2023. Trials that were registered in EudraCT and transitioned
to CTIS are captured by ctis_puller.py via the `eudraCt.isTransitioned` field.

This module is a no-op placeholder in case a working replacement API is found.
"""


def pull_all_eudract():
    print("  [EU-CTR] EudraCT REST API retired (data now in CTIS) — skipping.")
