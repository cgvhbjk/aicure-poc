import html
import json
import re
import os
from datetime import datetime
from dateutil import parser as dateutil_parser
from db import get_connection
# Shared keyword list / flag helper (was a divergent local copy here).
from text_match import DRUG_KEYWORDS, flag as _flag  # noqa: F401 (DRUG_KEYWORDS re-exported)

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "snapshots")
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

_GN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="

RSS_FEEDS = [
    # General pharma — good for sponsor/drug awareness
    {"source": "Fierce Pharma",  "url": "https://www.fiercepharma.com/rss/xml"},
    {"source": "Endpoints News", "url": "https://endpts.com/feed/"},
    {"source": "PharmaVoice",    "url": "https://www.pharmavoice.com/feed/"},
    # Trial-focused / clinical development sources
    {"source": "TrialSite News", "url": "https://trialsitenews.com/feed/"},
    {"source": "BioPharma Dive", "url": "https://www.biopharmadive.com/feeds/news/"},
    {"source": "STAT News",      "url": "https://www.statnews.com/feed/"},
    {"source": "BioSpace",       "url": "https://www.biospace.com/rss/news/", "require_relevance": True},
    # Google News keyword searches — aggregates dozens of pharma sources, free, no
    # API key. Led by CNS / psychiatry & neurology (AiCure's real focus), then a
    # secondary cardiometabolic net, plus stage-signal and M&A queries. Indication-
    # driven and general (no brand names) so the net isn't narrowed to GLP-1.
    # ── CNS / psychiatry (primary) ──
    {"source": "Google News — Schizophrenia",  "url": _GN + "schizophrenia+clinical+trial+phase"},
    {"source": "Google News — Depression",     "url": _GN + "major+depressive+disorder+clinical+trial+phase"},
    {"source": "Google News — PTSD",           "url": _GN + "PTSD+clinical+trial+phase"},
    {"source": "Google News — Bipolar",        "url": _GN + "bipolar+disorder+clinical+trial+phase"},
    {"source": "Google News — ADHD",           "url": _GN + "ADHD+clinical+trial+phase"},
    {"source": "Google News — Addiction",      "url": _GN + "substance+use+OR+addiction+clinical+trial+phase"},
    # ── Neurology (primary) ──
    {"source": "Google News — Parkinson",      "url": _GN + "Parkinson+disease+clinical+trial+phase"},
    {"source": "Google News — Alzheimer",      "url": _GN + "Alzheimer+disease+clinical+trial+phase"},
    {"source": "Google News — ALS",            "url": _GN + "amyotrophic+lateral+sclerosis+clinical+trial"},
    {"source": "Google News — Epilepsy",       "url": _GN + "epilepsy+clinical+trial+phase"},
    {"source": "Google News — CNS initiated",  "url": _GN + "CNS+OR+psychiatric+trial+initiated+OR+planned+OR+launches"},
    # ── Cross-cutting stage / signal queries ──
    {"source": "Google News — First patient",  "url": _GN + "first+patient+enrolled+OR+dosed+pharma+trial"},
    {"source": "Google News — IND filing",     "url": _GN + "IND+filed+OR+IND+cleared+clinical+trial"},
    {"source": "Google News — Adherence trial","url": _GN + "medication+adherence+clinical+trial"},
    {"source": "Google News — DCT",            "url": _GN + "decentralized+OR+remote+clinical+trial+initiated"},
    # ── M&A / buyouts (incl. private companies — see §4 acquisition stream) ──
    {"source": "Google News — Biotech M&A",    "url": _GN + "biotech+OR+pharma+acquires+OR+acquisition+OR+buyout"},
    # ── Cardiometabolic (secondary) ──
    {"source": "Google News — Obesity trial",  "url": _GN + "obesity+OR+GLP-1+trial+phase+2+OR+phase+3"},
    {"source": "Google News — T2D trial",      "url": _GN + "type+2+diabetes+clinical+trial+phase"},
    {"source": "Google News — Heart failure",  "url": _GN + "heart+failure+clinical+trial+phase"},
]

NCT_PATTERN = re.compile(r"NCT\d{8}")
PHASE_PATTERN = re.compile(r"\bphase\s*(1|2|3|4|I|II|III|IV)\b", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"<[^>]+>")

SPONSOR_DOMAINS = [
    "novo nordisk", "eli lilly", "astrazeneca", "pfizer", "merck",
    "sanofi", "roche", "johnson & johnson", "abbvie", "amgen",
    "boehringer", "bms", "bristol myers",
]

TRIAL_ANNOUNCEMENT_KEYWORDS = [
    # Initiation verbs + "phase" (avoids matching results headlines about phase X data)
    "initiates phase", "initiate phase", "initiating phase",
    "begins phase", "begin phase", "beginning phase",
    "starts phase", "start phase",
    "launches phase", "launch phase",
    "announces phase",
    # Enrollment language
    "enrollment opens", "enrollment begins", "enrollment open",
    "open enrollment", "begin enrollment", "begin enrolling",
    "enrolling patients", "enrolling participants", "enrolling now",
    "seeking patients", "seeking participants",
    "recruiting patients", "recruiting participants",
    "open for enrollment", "open to enrollment",
    # First patient milestones — strongest signal
    "first patient enrolled", "first patient dosed", "first patient treated",
    "first-in-human", "first in human", "first in-human",
    "doses first patient", "dosed first patient",
    # IND / regulatory filing — strongest signal
    "ind filed", "ind accepted", "ind cleared", "ind application",
    "ind clearance", "investigational new drug",
    # Explicit launch language
    "study initiation", "trial initiation", "trial launch",
    "announces trial", "announces clinical trial",
    # NCT registration language
    "clinicaltrials.gov",
]

RESULTS_KEYWORDS = [
    # Outcome reporting language
    "results show", "results showed", "results demonstrated", "results indicate",
    "study shows", "study showed", "study found", "study demonstrated",
    "trial shows", "trial showed", "trial found", "trial demonstrated",
    "data show", "data showed", "data demonstrate",
    "found that", "showed that", "demonstrated that",
    # Cause/association framing (appears in observational findings)
    "linked to greater", "associated with greater", "associated with weight",
    "leads to greater", "leads to weight",
    # Publication language
    "published in", "in the journal", "in nejm", "in the lancet", "in jama",
    "in the new england journal",
    # Results milestone language
    "interim results", "top-line results", "topline results", "final results",
    "primary results", "data readout", "readout",
    "met its primary endpoint", "missed primary endpoint", "failed to meet",
    "met primary endpoint",
    # Weight/outcome framing that signals findings, not enrollment
    "percent of their body weight", "percent of body weight",
    "percent weight loss", "% body weight",
    # Analysis types
    "post-hoc", "subgroup analysis", "meta-analysis", "retrospective",
    "real-world data", "real world data",
    # Phase results framing
    "phase 2 results", "phase 3 results", "phase 2 data", "phase 3 data",
    "phase ii results", "phase iii results", "phase ii data", "phase iii data",
]

# ─────────────────────────────────────────────────────────────────────────────
# Event-type signal keywords
# Early indicators of an upcoming/active trial often DON'T use standard
# clinical-trial phrasing (protocol awards, site activations, manufacturing
# scale-up, investigator announcements, budget notices, IRB activity, vendor
# RFPs, hiring). We classify each item into a single event type rather than a
# binary "mentions a trial" flag. Order in EVENT_TYPE_PRIORITY = precedence
# when more than one type matches (strongest / nearest-term signal wins).
# ─────────────────────────────────────────────────────────────────────────────

# Recruitment is actively opening — strongest near-term signal
RECRUITMENT_INITIATION_KEYWORDS = [
    "first patient enrolled", "first patient dosed", "first patient treated",
    "first-in-human", "first in human", "first in-human",
    "doses first patient", "dosed first patient",
    "enrollment opens", "enrollment begins", "enrollment open",
    "open enrollment", "begin enrollment", "begin enrolling",
    "enrolling patients", "enrolling participants", "enrolling now",
    "now enrolling", "seeking patients", "seeking participants",
    "recruiting patients", "recruiting participants",
    "open for enrollment", "open to enrollment", "actively recruiting",
]

# Study is spinning up operationally (after planning, before/at recruitment)
STUDY_STARTUP_KEYWORDS = [
    "initiates phase", "initiate phase", "initiating phase",
    "begins phase", "begin phase", "beginning phase",
    "starts phase", "start phase", "launches phase", "launch phase",
    "announces phase", "study initiation", "trial initiation", "trial launch",
    "announces trial", "announces clinical trial", "study go-live",
    "irb approval", "irb approved", "ethics approval", "ethics committee approval",
    "manufacturing scale-up", "scale up manufacturing", "scaling manufacturing",
    "manufacturing ramp", "clinical supply", "drug supply ready",
    "study staff", "clinical trial manager", "study coordinator hiring",
    "hiring clinical", "now hiring", "expanding clinical team",
]

# New investigative sites being activated / opened
SITE_OPENING_KEYWORDS = [
    "site activation", "sites activated", "activating sites", "site activated",
    "new clinical site", "new trial site", "opening sites", "site opening",
    "site initiation visit", "siv scheduled", "additional sites",
    "expanding to sites", "investigator site", "clinical site opened",
]

# Protocol is being designed / planned; investigator engagement
PROTOCOL_PLANNING_KEYWORDS = [
    "protocol design", "protocol finalized", "protocol amendment",
    "study protocol", "protocol development", "protocol awarded",
    "protocol award", "investigator meeting", "principal investigator named",
    "appoints principal investigator", "lead investigator", "study design",
    "trial design unveiled", "plans phase", "planned phase", "planning a phase",
    "intends to initiate", "expects to begin", "to begin a phase",
]

# Registry / regulatory filing activity
REGISTRY_CHANGE_KEYWORDS = [
    "clinicaltrials.gov", "nct registration", "registered on clinicaltrials",
    "ind filed", "ind accepted", "ind cleared", "ind application",
    "ind clearance", "investigational new drug", "cta submitted",
    "cta approved", "clinical trial application", "euct", "eudract",
    "registered the trial", "trial registered",
]

# Funding / budget signals (often precede everything above)
FUNDING_AWARDED_KEYWORDS = [
    "grant awarded", "awarded a grant", "receives grant", "grant funding",
    "funding awarded", "secures funding", "raises", "series a", "series b",
    "series c", "budget notice", "budget allocation", "nih award",
    "awarded contract", "milestone payment", "funding round",
    "research funding", "to fund the trial", "funds the study",
    "nih grant", "research grant", "grant to study", "grant to fund",
    "awarded funding", "grant recipient",
]

# Vendor / outsourcing demand signals (CRO/eClinical/DCT vendor selection)
VENDOR_SIGNAL_KEYWORDS = [
    "request for proposal", "rfp issued", "issues rfp", "vendor selection",
    "selects cro", "cro selected", "contract research organization",
    "ecoa vendor", "epro vendor", "selects vendor", "technology partner",
    "partners with", "partnership to support", "selected to provide",
    "decentralized trial platform", "dct platform", "evaluating vendors",
]

# Mergers & acquisitions, incl. private-company buyouts. Surfaced as their own
# stream ("this got bought — why?") regardless of any trial touchpoint (§4). Highest
# event-type precedence so a buyout headline isn't mislabeled as a trial signal.
ACQUISITION_KEYWORDS = [
    "acquires", "to acquire", "acquisition of", "acquired by", "completes acquisition",
    "definitive agreement to acquire", "agreement to acquire", "buys", "buyout",
    "takeover", "to be acquired", "merger", "merges with", "to merge",
    "agreed to acquire", "snaps up", "purchases", "to buy",
]

# Broad pharma terms used to filter out off-topic noise (applied per-source via require_relevance)
_RELEVANCE_TERMS = [
    "acquire", "acquisition", "buyout", "merger", "takeover",
    "clinical trial", "phase 1", "phase 2", "phase 3", "phase i", "phase ii", "phase iii",
    "fda", "ema", "nda", "bla", "ind ", "anda", "approval",
    "drug", "therapy", "therapeutics", "pharmaceutical", "pharma",
    "biotech", "biologic", "antibody", "vaccine", "oncology",
    "disease", "patient", "treatment", "efficacy", "safety",
    "cancer", "tumor", "diabetes", "obesity", "cardiovascular",
    "clinical", "medical", "medicine", "hospital", "health",
] + DRUG_KEYWORDS + SPONSOR_DOMAINS


def _clean(text):
    if not text:
        return ""
    text = _HTML_TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def _is_relevant(text):
    t = text.lower()
    return any(kw in t for kw in _RELEVANCE_TERMS)


# Precedence: when an item matches multiple types, the earliest wins.
# Ordered strongest/nearest-term first; results & noise handled separately.
EVENT_TYPE_PRIORITY = [
    ("acquisition",            ACQUISITION_KEYWORDS),
    ("recruitment_initiation", RECRUITMENT_INITIATION_KEYWORDS),
    ("study_startup",          STUDY_STARTUP_KEYWORDS),
    ("site_opening",           SITE_OPENING_KEYWORDS),
    ("registry_change",        REGISTRY_CHANGE_KEYWORDS),
    ("protocol_planning",      PROTOCOL_PLANNING_KEYWORDS),
    ("vendor_signal",          VENDOR_SIGNAL_KEYWORDS),
    ("funding_awarded",        FUNDING_AWARDED_KEYWORDS),
]


def classify_event_type(text, has_nct=False):
    """Classify a news item into a single event type.

    Returns one of: recruitment_initiation, study_startup, site_opening,
    registry_change, protocol_planning, vendor_signal, funding_awarded,
    trial_results, non_relevant.
    Results reporting is checked last so an item describing readouts isn't
    mislabeled as an upcoming-trial signal.
    """
    for event_type, keywords in EVENT_TYPE_PRIORITY:
        if _flag(text, keywords):
            return event_type
    if has_nct:
        return "registry_change"
    if _flag(text, RESULTS_KEYWORDS):
        return "trial_results"
    return "non_relevant"


def _parse_date(entry):
    raw = getattr(entry, "published", None) or getattr(entry, "updated", None)
    if not raw:
        return None
    try:
        return dateutil_parser.parse(raw).isoformat()
    except Exception:
        return raw


def parse_feed(feed_info):
    source = feed_info["source"]
    url = feed_info["url"]
    require_relevance = feed_info.get("require_relevance", False)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    safe_source = re.sub(r"[^a-zA-Z0-9]", "_", source)

    import feedparser  # lazy import — keeps the module importable (and testable)
    try:                # even where feedparser/sgmllib3k isn't installed.
        feed = feedparser.parse(url)
    except Exception as e:
        print(f"  [ERROR] feedparser failed for '{source}': {e}")
        return []

    snapshot_path = os.path.join(SNAPSHOT_DIR, f"rss_{safe_source}_{timestamp}.json")
    try:
        with open(snapshot_path, "w", encoding="utf-8") as f:
            json.dump(
                {"feed_title": getattr(feed.feed, "title", ""), "entries": [dict(e) for e in feed.entries]},
                f,
                default=str,
            )
    except Exception as e:
        print(f"  [WARN] Snapshot write failed for '{source}': {e}")

    items = []
    skipped = 0
    now = datetime.utcnow().isoformat()
    for entry in feed.entries:
        title = _clean(entry.get("title", "") or "")
        link = entry.get("link", "") or ""
        summary = _clean(entry.get("summary", "") or "")
        body_snippet = summary[:1000]
        combined = title + " " + body_snippet

        if require_relevance and not _is_relevant(combined):
            skipped += 1
            continue

        text = combined.lower()
        nct_ids = list(dict.fromkeys(NCT_PATTERN.findall(combined)))
        drug_mentioned = next((d for d in DRUG_KEYWORDS if d in text), None)
        phase_match = PHASE_PATTERN.search(combined)
        phase_mentioned = phase_match.group(0) if phase_match else None
        sponsor_mentioned = next((s for s in SPONSOR_DOMAINS if s in text), None)
        _is_initiation = _flag(combined, TRIAL_ANNOUNCEMENT_KEYWORDS) or bool(nct_ids)
        _is_results = _flag(combined, RESULTS_KEYWORDS)
        is_trial_announcement = 1 if (_is_initiation and not _is_results) else 0
        is_trial_results = 1 if _is_results else 0

        # Granular event-type classification (supersedes the binary flags above
        # for downstream scoring; flags retained for backward compatibility).
        event_type = classify_event_type(combined, has_nct=bool(nct_ids))

        items.append({
            "source": source,
            "title": title,
            "url": link,
            "published_at": _parse_date(entry),
            "body_snippet": body_snippet,
            "sponsor_mentioned": sponsor_mentioned,
            "drug_mentioned": drug_mentioned,
            "phase_mentioned": phase_mentioned,
            "nct_ids_found": json.dumps(nct_ids),
            "trial_id": None,
            "is_trial_announcement": is_trial_announcement,
            "is_trial_results": is_trial_results,
            "event_type": event_type,
            "ingested_at": now,
        })

    if skipped:
        print(f"    (filtered {skipped} off-topic items)")
    return items


def parse_all_feeds():
    conn = get_connection()
    for feed_info in RSS_FEEDS:
        source = feed_info["source"]
        print(f"  Parsing '{source}'...")
        try:
            items = parse_feed(feed_info)
            inserted = 0
            for item in items:
                if not item["url"]:
                    continue
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO news_items
                          (source, title, url, published_at, body_snippet,
                           sponsor_mentioned, drug_mentioned, phase_mentioned,
                           nct_ids_found, trial_id, is_trial_announcement, is_trial_results, event_type, ingested_at)
                        VALUES
                          (:source, :title, :url, :published_at, :body_snippet,
                           :sponsor_mentioned, :drug_mentioned, :phase_mentioned,
                           :nct_ids_found, :trial_id, :is_trial_announcement, :is_trial_results, :event_type, :ingested_at)
                        """,
                        item,
                    )
                    if conn.execute("SELECT changes()").fetchone()[0]:
                        inserted += 1
                except Exception as e:
                    print(f"  [WARN] Insert failed: {e}")
            conn.commit()
            print(f"    → {inserted} new items")
        except Exception as e:
            print(f"  [ERROR] Feed '{source}' failed: {e}")
    conn.close()
