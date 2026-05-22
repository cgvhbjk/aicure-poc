from rss_parser import parse_all_feeds
from linker import run_linker

print("Parsing feeds...")
parse_all_feeds()
print("Linking to trials...")
run_linker()
print("Done.")
