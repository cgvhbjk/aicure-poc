"""NLP / extraction layer for news items.

Two jobs:
  1. Decide whether an article is about a trial that has NOT yet started AND
     whether that trial is relevant to AiCure's focus.
  2. Extract the fields the lead card needs (indication, est. size, geography,
     sponsor/org) that aren't stored as columns.

Design: a Claude API path (when ANTHROPIC_API_KEY is set) plus a deterministic
rules-based fallback so the pipeline runs — and the previews fill out — without
a key. The card consumes `analyze()` regardless of which path ran.
"""

import os
import re
import json
import shutil

from grant_utils import classify_area

# Local Claude Code CLI — lets us run the LLM path through the user's Claude
# subscription (no billed API key) until the app is pushed to production.
_CLAUDE_BIN = shutil.which("claude")
_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "nlp_cache.json")
_CACHE = None


def _cache():
    global _CACHE
    if _CACHE is None:
        try:
            with open(_CACHE_PATH) as f:
                _CACHE = json.load(f)
        except FileNotFoundError:
            _CACHE = {}  # first run — expected, stay quiet
        except Exception as e:
            # File present but unreadable (corrupt JSON / permissions): warn so a
            # silently-reset cache that re-runs every LLM call is traceable.
            print(f"[news_nlp] cache unreadable ({_CACHE_PATH}): {e} — starting empty")
            _CACHE = {}
    return _CACHE


def _cache_put(key, value):
    c = _cache()
    c[key] = value
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        with open(_CACHE_PATH, "w") as f:
            json.dump(c, f)
    except Exception as e:
        # Disk-full / permission: the result is silently lost and will re-run
        # (cost/latency). Log it instead of swallowing.
        print(f"[news_nlp] cache write failed ({_CACHE_PATH}): {e}")

# ── AiCure-relevant trial categories ─────────────────────────────────────────
# What "applies to AiCure" means: a CNS / psychiatry / neurology indication (the
# real won-deal focus) or a cardiometabolic one, AND/OR a trial design AiCure's
# platform serves (decentralized, ePRO/eCOA, digital biomarkers, medication
# adherence). The NLP classifies into one of these and flags off-focus items so
# they can be dropped or de-ranked.
AICURE_CATEGORIES = [
    "CNS / Psychiatry",
    "Neurology",
    "Obesity / Weight Management",
    "Type 2 Diabetes",
    "Cardiovascular",
    "Liver / NASH",
    "Renal / Kidney",
    "Medication Adherence",
    "Other Cardiometabolic",
]
# Map of our coarse area buckets → display category
_AREA_TO_CATEGORY = {
    "CNS / Psychiatry": "CNS / Psychiatry",
    "Neurology": "Neurology",
    "Metabolic / GLP-1": "Obesity / Weight Management",
    "Diabetes": "Type 2 Diabetes",
    "Cardiovascular": "Cardiovascular",
    "Liver / NASH": "Liver / NASH",
    "Renal": "Renal / Kidney",
    "Adherence / Outcomes": "Medication Adherence",
}
_CORE_AREAS = set(_AREA_TO_CATEGORY) - {"Adherence / Outcomes"}

# ── geography detection ───────────────────────────────────────────────────────
_GEO_TERMS = [
    ("United States", "US"), ("U.S.", "US"), (" US ", "US"), ("USA", "US"),
    ("United Kingdom", "UK"), (" UK ", "UK"), ("Britain", "UK"), ("England", "UK"),
    ("Europe", "Europe"), ("European", "Europe"), ("Germany", "Germany"),
    ("France", "France"), ("Spain", "Spain"), ("Italy", "Italy"),
    ("China", "China"), ("Japan", "Japan"), ("Korea", "South Korea"),
    ("Canada", "Canada"), ("Australia", "Australia"), ("India", "India"),
    ("global", "Global / multinational"), ("multinational", "Global / multinational"),
    ("multi-country", "Global / multinational"), ("worldwide", "Global / multinational"),
]

# ── size / enrollment extraction ─────────────────────────────────────────────
_SIZE_PATTERNS = [
    re.compile(r'(\d[\d,]{1,6})\s*(?:patients|participants|subjects|adults|volunteers|people)', re.I),
    re.compile(r'enroll(?:ing|ed|ment of)?\s*(?:up to\s*|approximately\s*|about\s*|~\s*)?(\d[\d,]{1,6})', re.I),
    re.compile(r'(\d[\d,]{1,6})[- ](?:patient|participant|subject)\b', re.I),
    re.compile(r'(?:n\s*=\s*)(\d[\d,]{1,6})', re.I),
]

# ── sponsor / org extraction (beyond the 13 big-pharma names) ─────────────────
_ORG_PATTERN = re.compile(
    r'\b([A-Z][A-Za-z0-9&.\-]+(?:\s+[A-Z][A-Za-z0-9&.\-]+){0,3}\s+'
    r'(?:Pharmaceuticals?|Pharma|Therapeutics|Biosciences?|Biotech|Bio|Sciences|'
    r'Labs?|Laboratories|Inc\.?|Corp\.?|Ltd\.?|LLC|University|Hospital|Institute|'
    r'Health|Medical|Medicines?|Oncology|Sciences))\b'
)


def _extract_size(text):
    for pat in _SIZE_PATTERNS:
        m = pat.search(text)
        if m:
            n = m.group(1).replace(",", "")
            try:
                v = int(n)
                if 10 <= v <= 500000:
                    return f"{v:,}"
            except ValueError:
                continue
    return None


def _extract_geo(text):
    found = []
    for needle, label in _GEO_TERMS:
        if needle.lower() in text.lower() and label not in found:
            found.append(label)
    return ", ".join(found[:3]) if found else None


def _extract_org(text, known_sponsor=None):
    if known_sponsor:
        return known_sponsor.title()
    m = _ORG_PATTERN.search(text)
    return m.group(1) if m else None


_FETCH_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}
_article_cache = {}


def fetch_article_text(url, timeout=8, max_chars=6000):
    """Fetch and strip an article to plain text. Cached; best-effort (returns
    '' on any failure). RSS snippets are too thin for extraction, so we read the
    real page for the handful of relevant candidates."""
    if not url:
        return ""
    if url in _article_cache:
        return _article_cache[url]
    text = ""
    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(url, headers=_FETCH_HEADERS, timeout=timeout)
        if resp.status_code >= 400:
            # A 404/403/500 returns an HTML error page; parsing it as "article
            # text" silently feeds a "Page Not Found" body to the NLP relevance/
            # extraction step (and caches it). Treat as no text.
            print(f"[news_nlp] fetch {resp.status_code} for {url[:60]} — skipping")
            _article_cache[url] = ""
            return ""
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        paras = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
        text = " ".join(paras)[:max_chars]
    except Exception as e:
        print(f"[news_nlp] fetch failed for {url[:60]}: {e}")
    _article_cache[url] = text
    return text


def _resolve_backend(use_llm):
    """Pick the analysis backend.

    AICURE_NLP_BACKEND = api | cli | rules | auto (default auto):
      auto → billed API if ANTHROPIC_API_KEY is set, else the local `claude`
      CLI (your Claude subscription), else rules. `use_llm=False` forces rules;
      `use_llm=True` forces an LLM path if one is available.
    """
    backend = os.environ.get("AICURE_NLP_BACKEND", "auto")
    if use_llm is False:
        return "rules"
    if backend in ("api", "cli", "rules"):
        return backend
    # auto
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if _CLAUDE_BIN:
        return "cli"
    return "rules"


def analyze(item, use_llm=None, full_text=None):
    """Return enrichment for a news item.

    item: dict-like with title, body_snippet, sponsor_mentioned, drug_mentioned,
          phase_mentioned, event_type, nct_ids_found.
    Returns dict: applies_to_aicure (bool), aicure_category, indication,
                  est_size, geography, sponsor_org, not_yet_started (bool),
                  fit_reason, fit_signals, signal_phrase, method.
    """
    backend = _resolve_backend(use_llm)
    url = item.get("url")
    cache_key = f"{backend}:{url}" if (backend != "rules" and url) else None
    if cache_key and cache_key in _cache():
        return _cache()[cache_key]

    try:
        if backend == "api":
            res = _analyze_llm(item, full_text)
        elif backend == "cli":
            res = _analyze_cli(item, full_text)
        else:
            res = _analyze_rules(item, full_text)
    except Exception as e:
        print(f"[news_nlp] {backend} path failed ({e}); falling back to rules")
        import traceback
        traceback.print_exc()
        res = _analyze_rules(item, full_text)
        # Tag the degraded path so a silently-downgraded LLM run is visible in the
        # `method` field downstream (e.g. the digest), not indistinguishable from
        # a deliberate rules run.
        res["method"] = f"rules ({backend}-fallback)"

    # Don't cache rules output (deliberate OR degraded fallback): a cached
    # fallback would pin the item to rules and never retry the LLM path.
    if cache_key and not res.get("method", "").startswith("rules"):
        _cache_put(cache_key, res)
    return res


# ── acquisition ("this got bought — why?") extraction (§4) ────────────────────
_ACQ_PATTERNS = [
    re.compile(r'([A-Z][\w.&\-]*(?:\s+[A-Z][\w.&\-]*){0,4})\s+(?:to\s+)?acquires?\s+'
               r'([A-Z][\w.&\-]*(?:\s+[A-Z][\w.&\-]*){0,4})', re.I),
    re.compile(r'([A-Z][\w.&\-]*(?:\s+[A-Z][\w.&\-]*){0,4})\s+(?:to\s+)?(?:buys|snaps up|'
               r'agreed to acquire|completes acquisition of)\s+'
               r'([A-Z][\w.&\-]*(?:\s+[A-Z][\w.&\-]*){0,4})', re.I),
]
# "<target> acquired by <acquirer>" — operands reversed.
_ACQ_BY_PATTERN = re.compile(
    r'([A-Z][\w.&\-]*(?:\s+[A-Z][\w.&\-]*){0,4})\s+acquired by\s+'
    r'([A-Z][\w.&\-]*(?:\s+[A-Z][\w.&\-]*){0,4})', re.I)
_RATIONALE_CUES = ("to expand", "to bolster", "to strengthen", "to gain", "to add",
                   "pipeline", "to acquire rights", "to broaden", "to enter",
                   "bolstering", "expanding", "strengthening")


def _extract_acquisition(text):
    """Best-effort (acquirer, target, rationale) for an acquisition headline."""
    acquirer = target = None
    m = _ACQ_BY_PATTERN.search(text)
    if m:
        target, acquirer = m.group(1).strip(), m.group(2).strip()
    else:
        for pat in _ACQ_PATTERNS:
            m = pat.search(text)
            if m:
                acquirer, target = m.group(1).strip(), m.group(2).strip()
                break
    rationale = None
    for sent in re.split(r'(?<=[.!?])\s+', text):
        if any(c in sent.lower() for c in _RATIONALE_CUES):
            rationale = sent.strip()[:240]
            break
    return acquirer, target, rationale


def _analyze_rules(item, full_text=None):
    title = item.get("title") or ""
    body = item.get("body_snippet") or ""
    # Prefer full article text when available — the RSS snippet is too thin to
    # extract size/geography/sponsor reliably.
    text = f"{title} {full_text or body}"

    et = item.get("event_type") or ""
    # Acquisitions are a lead in their own right ("why was this bought") — they
    # apply regardless of any trial touchpoint or pre-start timing.
    if et == "acquisition":
        acquirer, target, rationale = _extract_acquisition(text)
        return {
            "applies_to_aicure": True,
            "aicure_category": "Acquisition / M&A",
            "indication": None,
            "est_size": None,
            "geography": _extract_geo(text),
            "sponsor_org": acquirer or _extract_org(text, item.get("sponsor_mentioned")),
            "signal_phrase": _signal_phrase(text, et),
            "fit_signals": [],
            "not_yet_started": True,
            "fit_reason": "M&A — assess why the target was bought",
            "acquirer": acquirer,
            "target": target,
            "rationale": rationale,
            "method": "rules",
        }

    area = classify_area(text)
    category = _AREA_TO_CATEGORY.get(area)
    # adherence is in-focus even without a core indication
    applies = area in _CORE_AREAS or area == "Adherence / Outcomes"
    if category is None:
        category = "Off-focus"

    # pre-start heuristic: early event types and no "first patient/results" cues
    et = item.get("event_type") or ""
    late_cues = ("first patient", "results show", "data show", "topline",
                 "met its primary", "readout", "now enrolling", "actively recruiting")
    not_yet = et in ("protocol_planning", "funding_awarded", "vendor_signal",
                      "registry_change", "study_startup", "site_opening") \
        and not any(c in text.lower() for c in late_cues)

    fit_reason = (f"{category} indication" if applies else "outside cardiometabolic focus")

    fit_signals = []
    if any(k in text for k in ("oral", "tablet", "capsule", "pill", "self-inject",
                               "subcutaneous", "injection", "once-daily", "daily dos")):
        fit_signals.append("self-administered (adherence)")
    if any(k in text for k in ("weight", "bmi", "obes", "blood pressure")):
        fit_signals.append("weight/vitals endpoint")

    return {
        "applies_to_aicure": applies,
        "aicure_category": category,
        "indication": area if area != "Other" else None,
        "est_size": _extract_size(text),
        "geography": _extract_geo(text),
        "sponsor_org": _extract_org(text, item.get("sponsor_mentioned")),
        "signal_phrase": _signal_phrase(text, et),
        "fit_signals": fit_signals,
        "not_yet_started": not_yet,
        "fit_reason": fit_reason,
        "method": "rules",
    }


def _signal_phrase(text, event_type):
    """The keyword that triggered this event type — explains WHY it was flagged."""
    try:
        import rss_parser as rp
    except Exception:
        return None
    kw_map = {
        "acquisition": rp.ACQUISITION_KEYWORDS,
        "protocol_planning": rp.PROTOCOL_PLANNING_KEYWORDS,
        "funding_awarded": rp.FUNDING_AWARDED_KEYWORDS,
        "vendor_signal": rp.VENDOR_SIGNAL_KEYWORDS,
        "registry_change": rp.REGISTRY_CHANGE_KEYWORDS,
        "study_startup": rp.STUDY_STARTUP_KEYWORDS,
        "site_opening": rp.SITE_OPENING_KEYWORDS,
    }
    t = text.lower()
    for kw in kw_map.get(event_type, []):
        if kw in t:
            return f'"{kw}"'
    return None


# ── Claude API path (primary when ANTHROPIC_API_KEY is set) ───────────────────
# Model: Haiku 4.5 — the cheapest current Claude model and well-suited to short
# headline/snippet classification + extraction. No `effort`/`thinking` params:
# those are unsupported on Haiku and would 400.
_LLM_MODEL = "claude-haiku-4-5"

# Allowed enum values, kept in sync with the rules path so both produce the same
# downstream shape (the email card maps fit_signals → AiCure products).
_LLM_CATEGORIES = AICURE_CATEGORIES + ["Off-focus"]
_LLM_FIT_SIGNALS = [
    "self-administered (adherence)", "weight/vitals endpoint",
    "digital biomarkers", "DCT", "ePRO",
]

_LLM_SYSTEM = (
    "You analyze pharma / clinical-trial news for AiCure, which sells software for "
    "medication adherence (visual pill-ingestion / dose confirmation), remote weight "
    "& vitals verification, ePRO/eCOA, digital biomarkers, and decentralized trials. "
    "AiCure's PRIMARY focus is CNS / psychiatry & neurology trials (schizophrenia, "
    "depression/MDD, PTSD, bipolar, ADHD, addiction, Parkinson's, Alzheimer's, ALS, "
    "MS, epilepsy) — adherence-fragile, self-administered populations. It ALSO cares "
    "about cardiometabolic trials (obesity/GLP-1, type 2 diabetes, cardiovascular, "
    "NASH, renal) and medication adherence. Oncology is OFF-focus (those patients "
    "adhere reliably).\n\n"
    "CRITICAL TIMING: AiCure must engage with a trial BEFORE it starts enrolling — "
    "during planning, funding, vendor selection, or registration. An article about a "
    "trial that is already recruiting, has dosed its first patient, or is reporting "
    "results means the window has closed → not_yet_started = false.\n\n"
    "Set applies_to_aicure = true only if the trial is CNS/neuro or "
    "cardiometabolic/adherence AND AiCure's product could plausibly touch it "
    "(self-administered drug, weight/vitals endpoint, ePRO, digital biomarkers, or "
    "decentralized design). A transdermal patch is NOT a pill-ingestion touchpoint. "
    "fit_signals must be "
    "a subset of the allowed values; choose only those clearly supported by the text. "
    "Leave est_size / geography / sponsor_org null when the text does not state them — "
    "do not guess. aicure_category must be one of the allowed categories or 'Off-focus'."
)


def _analyze_llm(item, full_text=None):
    # Lazily imported so the rules-based fallback has no hard dependency on the
    # Anthropic SDK / pydantic.
    from anthropic import Anthropic
    from pydantic import BaseModel
    from typing import List, Optional
    try:
        from typing import Literal
    except ImportError:  # py<3.8 safety; project is 3.10+
        Literal = None

    cat_type = Literal[tuple(_LLM_CATEGORIES)] if Literal else str
    sig_type = Literal[tuple(_LLM_FIT_SIGNALS)] if Literal else str

    class NewsAnalysis(BaseModel):
        applies_to_aicure: bool
        aicure_category: cat_type
        not_yet_started: bool
        fit_reason: str
        fit_signals: List[sig_type]
        indication: Optional[str] = None
        est_size: Optional[str] = None
        geography: Optional[str] = None
        sponsor_org: Optional[str] = None

    title = item.get("title") or ""
    body = full_text or item.get("body_snippet") or ""

    client = Anthropic()  # resolves ANTHROPIC_API_KEY from the environment
    resp = client.messages.parse(
        model=_LLM_MODEL,
        max_tokens=400,
        # cache_control on the (stable) system prompt — caches once the prefix is
        # large enough to hit Haiku's 4096-token minimum; harmless otherwise.
        system=[{"type": "text", "text": _LLM_SYSTEM,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": f"Title: {title}\n\nBody: {body}"}],
        output_format=NewsAnalysis,
    )
    data = resp.parsed_output.model_dump()
    # signal_phrase is a deterministic keyword lookup — compute it the same way
    # regardless of path so the email's "why flagged" field is consistent.
    et = item.get("event_type") or ""
    data["signal_phrase"] = _signal_phrase(f"{title} {body}".lower(), et)
    data["method"] = "llm"
    return data


# ── Local Claude CLI path (uses your Claude subscription; no billed API key) ──

def _analyze_cli(item, full_text=None):
    """Run the same classification through the local `claude` CLI in print mode.

    Output is constrained to the same enums as the API path so downstream code
    (the email card's product mapping) is identical regardless of backend.
    """
    import subprocess
    import tempfile

    if not _CLAUDE_BIN:
        raise RuntimeError("claude CLI not found on PATH")

    title = item.get("title") or ""
    body = full_text or item.get("body_snippet") or ""
    instr = (
        _LLM_SYSTEM
        + "\n\nAllowed aicure_category values (choose EXACTLY one): "
        + ", ".join(_LLM_CATEGORIES) + "."
        + "\nAllowed fit_signals values (a possibly-empty subset, EXACT strings only): "
        + ", ".join(_LLM_FIT_SIGNALS) + "."
        + "\n\nOutput ONLY a single JSON object (no markdown fences, no prose) with keys: "
        "applies_to_aicure (bool), aicure_category (string), not_yet_started (bool), "
        "indication (string|null), est_size (string|null), geography (string|null), "
        "sponsor_org (string|null), fit_signals (array), fit_reason (string)."
    )
    prompt = f"{instr}\n\nNEWS ITEM:\nTitle: {title}\nBody: {body}\n\nJSON:"

    r = subprocess.run(
        [_CLAUDE_BIN, "-p", prompt, "--output-format", "json", "--model", "haiku"],
        capture_output=True, text=True, timeout=120,
        cwd=tempfile.gettempdir(),  # neutral dir so it doesn't load repo context
    )
    if r.returncode != 0:
        raise RuntimeError(f"claude CLI rc={r.returncode}: {r.stderr[:200]}")

    envelope = json.loads(r.stdout)
    text = envelope.get("result", "") if isinstance(envelope, dict) else ""
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < 0:
        raise ValueError(f"no JSON in CLI result: {text[:120]}")
    data = json.loads(text[s:e + 1])

    # Coerce / validate against our enums so the shape matches the API path.
    if data.get("aicure_category") not in _LLM_CATEGORIES:
        area = classify_area(f"{title} {body}")
        data["aicure_category"] = _AREA_TO_CATEGORY.get(area, "Off-focus")
    data["fit_signals"] = [s for s in (data.get("fit_signals") or []) if s in _LLM_FIT_SIGNALS]
    data["applies_to_aicure"] = bool(data.get("applies_to_aicure"))
    data["not_yet_started"] = bool(data.get("not_yet_started"))
    for k in ("indication", "est_size", "geography", "sponsor_org"):
        data.setdefault(k, None)
    data.setdefault("fit_reason", "")
    data["signal_phrase"] = _signal_phrase(f"{title} {body}".lower(), item.get("event_type") or "")
    data["method"] = "cli"
    return data
