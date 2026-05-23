import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from merge_detector import run_merge_detection

run_merge_detection()
print("Done.")
