import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ictrp_puller import pull_all_ictrp

pull_all_ictrp()
print("Done.")
