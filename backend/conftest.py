"""Pytest bootstrap: bind the DB layer to a throwaway file before api/db import.

db._init_db() opens AICURE_DB_PATH at *import* time, so the override has to be in
os.environ before any test module does `import api`/`import db`. conftest.py is
imported by pytest ahead of collection — the one hook early enough. This keeps the
suite off the 331 MB seed DB and hands every run a clean, empty schema to fixture
into (the merge tests insert and delete rows, so they must not touch the real
seed). setdefault() lets CI pin its own path. Network-FS flag stays off → local
WAL, which is fine and fast for tests.
"""
import os
import tempfile

os.environ.setdefault(
    "AICURE_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="aicure-test-"), "test.db")
)
