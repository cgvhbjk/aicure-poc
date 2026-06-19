from rss_parser import parse_all_feeds
from linker import run_linker

print("Parsing feeds...")
parse_all_feeds()
print("Linking to trials...")
run_linker()

# Daily cadence: also hand any high-fit, pre-start trials to the CRM (no-op
# unless CRM_PUSH_ENABLED + CRM_BASE_URL are set). Already-pushed rows are
# skipped, so this only catches ones that newly qualified.
try:
    import crm_push
    crm_push.run()
except Exception as e:
    print(f"CRM push ERROR: {e}")

print("Done.")
