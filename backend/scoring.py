"""AiCure opportunity scoring — the single source of truth.

Both the email digests (emailer.py) and the API/grid (api.py) score trials and
grants with these formulas, so a lead ranks identically wherever it surfaces.
Each `_illustrative_*_score` returns (score 0-100, why-string); `score_trial` /
`score_grant` are the convenience wrappers that return just the number.
"""
import json
from datetime import datetime
from dateutil.parser import parse as dateparse


# ── illustrative scoring (placeholder until scorer.py / B is built) ───────────
# NOTE: Transparent stand-in so previews rank sensibly. Mirrors the 5 axes from
# the real scorer: immediacy, commercial fit, source strength, confidence,
# uniqueness. CORE PRINCIPLE: AiCure must engage BEFORE a trial starts — so
# immediacy rewards pre-start / near-future and PENALIZES already-underway.

def _days_from_now(date_str):
    """Signed days until date_str (positive = future). None if unparseable."""
    if not date_str:
        return None
    try:
        d = dateparse(str(date_str))
        if d.tzinfo:
            d = d.replace(tzinfo=None)
        return (d - datetime.utcnow()).days
    except Exception:
        return None


# Trial status → immediacy points. Pre-start is best; already recruiting is
# worse (window closing); completed/dead is worthless.
_STATUS_IMMEDIACY = {
    "NOT_YET_RECRUITING": (34, "not yet recruiting"),
    "APPROVED_FOR_MARKETING": (10, "approved"),
    "ENROLLING_BY_INVITATION": (16, "enrolling by invite"),
    "RECRUITING": (12, "already recruiting"),
    "ACTIVE_NOT_RECRUITING": (4, "active, closed to enroll"),
    "UNKNOWN": (6, "status unknown"),
    "COMPLETED": (0, "completed"),
    "TERMINATED": (0, "terminated"),
    "SUSPENDED": (0, "suspended"),
    "WITHDRAWN": (0, "withdrawn"),
}

# Therapeutic-area commercial fit (AiCure's core focus = highest).
_AREA_FIT = {
    "Metabolic / GLP-1": (20, "GLP-1/metabolic fit"),
    "Diabetes": (15, "diabetes fit"),
    "Cardiovascular": (15, "CV fit"),
    "Liver / NASH": (12, "NASH fit"),
    "Renal": (10, "renal fit"),
    "Adherence / Outcomes": (10, "adherence fit"),
    "Other": (-14, "off-core area"),
}

# ── AiCure-capability fit ─────────────────────────────────────────────────────
# AiCure's value is OPERATIONAL, not therapeutic: its product can only help a
# trial that has a touchpoint — a self-administered drug (dose confirmation /
# adherence), a weight/vitals endpoint (remote verification), or ePRO / digital
# biomarker / DCT elements. A right-disease trial with none of these is a poor
# fit and is penalized hard (but kept visible).
_SELF_ADMIN_CUES = [
    "oral", "tablet", "capsule", "pill", "by mouth", "orally", "self-administ",
    "self administ", "subcutaneous", "self-inject", "self inject", "autoinjector",
    "auto-injector", "pen injector", "prefilled pen", "take-home", "outpatient",
    "once-daily", "once daily", "twice daily", "daily dosing",
]
_WEIGHT_VITAL_CUES = [
    "weight", "body weight", "bmi", "obes", "blood pressure", "waist circumference",
    "vital signs", "weight loss", "weight management",
]
_GRANT_FIT_CUES = [
    "adher", "decentrali", "remote monitor", "telehealth", "telemedicine", "epro",
    "ecoa", "patient-reported", "patient reported", "wearable", "digital biomarker",
    "digital health", "mobile health", "mhealth", "self-administ", "smartphone",
    "app-based", "medication adherence", "remote", "home-based",
]


# Maps a detected fit signal → the concrete AiCure product that serves it.
_FIT_PRODUCT = {
    "self-administered (adherence)":
        "AiCure's pill-ingestion / dose verification can confirm adherence on the "
        "self-administered regimen (and self-injection technique)",
    "weight/vitals endpoint":
        "AiCure's remote weight & vitals verification can capture the weight/vitals "
        "endpoint without clinic visits",
    "DCT":
        "AiCure supports the decentralized visit design",
    "digital biomarkers":
        "AiCure's smartphone digital-biomarker capture applies",
    "ePRO":
        "AiCure's ePRO/eCOA module can collect the patient-reported outcomes",
    "AiCure-relevant design":
        "the funded work involves adherence / remote / digital-measurement methods "
        "AiCure's platform delivers",
}


def _fit_blurb(fit_labels):
    """Human-readable 'why this works for AiCure', naming the actual product(s)."""
    parts = [_FIT_PRODUCT[l] for l in fit_labels if l in _FIT_PRODUCT]
    if not parts:
        return ("No clear AiCure touchpoint detected — included for human review; "
                "verify whether the drug is self-administered or has remote/PRO endpoints.")
    blurb = parts[0]
    for p in parts[1:]:
        blurb += "; also " + p[0].lower() + p[1:]
    return blurb + "."


def _trial_aicure_fit(t):
    """Whether AiCure's PRODUCT can touch this trial (disease-independent).
    Returns (points, labels, has_signal)."""
    text = " ".join(filter(None, [t["interventions"], t["brief_summary"],
                                  t["conditions"], t["title_brief"]])).lower()
    # Capability value hierarchy: PILL adherence (mature product) > WEIGHT
    # verification (in testing) > any other biodata/adherence signal.
    pts, why, has = 0, [], False
    if any(k in text for k in _SELF_ADMIN_CUES):
        pts += 26; why.append("self-administered (adherence)"); has = True
    if any(k in text for k in _WEIGHT_VITAL_CUES):
        pts += 15; why.append("weight/vitals endpoint"); has = True
    if t["digital_biomarkers"]: pts += 9; why.append("digital biomarkers"); has = True
    if t["dct_elements"]: pts += 8; why.append("DCT"); has = True
    if t["epro_ecoa"]: pts += 7; why.append("ePRO"); has = True
    if not has:
        pts -= 25; why.append("no AiCure touchpoint")
    return pts, why, has


# ── geography fit ─────────────────────────────────────────────────────────────
# AiCure runs US and European operations, so US/EU opportunities are worth more
# and trials outside that footprint are de-prioritized.
_US_TERMS = {"united states", "usa", "u.s.", "us", "u.s.a."}
_EU_COUNTRIES = {
    "germany", "france", "spain", "italy", "netherlands", "belgium", "sweden",
    "denmark", "austria", "poland", "portugal", "finland", "norway", "switzerland",
    "ireland", "czech republic", "czechia", "greece", "hungary", "romania",
    "bulgaria", "slovakia", "slovenia", "croatia", "lithuania", "latvia", "estonia",
    "luxembourg", "iceland", "united kingdom", "uk", "england", "scotland", "wales",
}
# Registry-of-origin hint when an explicit country is missing.
_REG_GEO = {
    "CTIS": ("EU", 14), "EudraCT": ("EU", 14), "ISRCTN": ("EU", 14),
    "DRKS": ("EU", 14), "NTR": ("EU", 14),
    "ChiCTR": ("non-US/EU", -12), "CRIS": ("non-US/EU", -12),
    "WHO-JPRN": ("non-US/EU", -12), "jRCT": ("non-US/EU", -12),
    "CTRI": ("non-US/EU", -12), "ANZCTR": ("non-US/EU", -12),
}


def _geo_fit(country, registry_sources=None):
    """(points, label) for US/European operational footprint."""
    c = (country or "").strip().lower()
    if c in _US_TERMS:
        return 14, "US operations"
    if c in _EU_COUNTRIES:
        return 14, "EU operations"
    if c in ("canada", "australia", "new zealand"):
        return 0, None  # adjacent markets — neutral
    if c:
        return -12, "outside US/EU"
    # No explicit country → fall back to the registry of origin.
    try:
        regs = json.loads(registry_sources or "[]")
    except Exception:
        regs = [registry_sources] if registry_sources else []
    for r in regs:
        if r in _REG_GEO:
            label, pts = _REG_GEO[r]
            return pts, ("EU operations" if label == "EU" else "outside US/EU")
    return 0, None


_GRANT_PILL_CUES = [
    "medication adherence", "treatment adherence", "adher", "compliance", "oral",
    "tablet", "capsule", "pill", "by mouth", "self-administ", "self administ",
    "regimen", "polypharmacy", "dose timing",
]
_GRANT_DIGITAL_CUES = [
    "decentrali", "remote monitor", "telehealth", "telemedicine", "wearable",
    "digital biomarker", "digital health", "mobile health", "mhealth", "smartphone",
    "app-based", "remote", "home-based", "sensor",
]
_GRANT_EPRO_CUES = ["epro", "ecoa", "patient-reported", "patient reported"]


def _grant_aicure_fit(g):
    """AiCure-capability fit for a grant, using the SAME product hierarchy as
    trials: PILL adherence (mature product) > WEIGHT verification > other
    biodata. Returns (points, fit_labels, has_signal). fit_labels reuse the
    trial labels so the 'Why AiCure' blurb names the same products."""
    text = " ".join(filter(None, [g["title"], g["abstract"], g["conditions"]])).lower()
    pts, labels = 0, []
    if any(k in text for k in _GRANT_PILL_CUES):
        pts += 30; labels.append("self-administered (adherence)")   # pill — highest
    if any(k in text for k in _WEIGHT_VITAL_CUES):
        pts += 18; labels.append("weight/vitals endpoint")          # weight — middle
    if any(k in text for k in _GRANT_DIGITAL_CUES):
        pts += 10; labels.append("digital biomarkers")             # other biodata
    if any(k in text for k in _GRANT_EPRO_CUES):
        pts += 8; labels.append("ePRO")
    if not labels:
        return -12, [], False
    return pts, labels, True


def _illustrative_trial_score(t):
    """5-axis-style trial opportunity score (0-100)."""
    s, why = 0, []

    # 1. IMMEDIACY (cap ~30) — must reach the sponsor DURING PLANNING. A start
    # date that has already passed is disqualifying (way too late).
    st = (t["status"] or "").upper()
    pts, lbl = _STATUS_IMMEDIACY.get(st, (6, st.lower() or "n/a"))
    s += int(pts * 0.6); why.append(lbl)   # scaled so immediacy can't dominate
    d = _days_from_now(t["start_date"])
    if d is not None:
        if d >= 0: s += 12; why.append("not yet started")
        else: s -= 45; why.append("start date passed — too late")  # ranks far below pre-start

    # 2. AICURE-CAPABILITY FIT (dominant) — can the product actually touch it?
    fp, fwhy, _has_fit = _trial_aicure_fit(t)
    s += fp; why += fwhy

    # 2b. THERAPEUTIC-AREA GATE (secondary) — right disease, lightly weighted.
    apts, _ = _AREA_FIT.get(t["therapeutic_area"], (-14, "off-core area"))
    s += int(apts * 0.5)
    phase = (t["phase"] or "").lower()
    if "3" in phase or "iii" in phase: s += 8
    elif "2" in phase or "ii" in phase: s += 5

    # 3. SCALE — AiCure runs at large scale (quality over quantity). UNKNOWN
    # size (non-CTgov registries like CRIS/EudraCT give no enrollment) is
    # penalized: we can't confirm it's worth pursuing.
    enr = t["enrollment"]
    if enr is None: s -= 10; why.append("size unknown")
    elif enr >= 2000: s += 20; why.append("very large (2k+)")
    elif enr >= 1000: s += 14; why.append("large (1k+)")
    elif enr >= 500: s += 8; why.append("500+ enroll")
    elif enr >= 100: s += 2
    elif enr > 0: s -= 14; why.append("too small")
    ns = t["num_sites"] or 0
    if ns >= 50: s += 10; why.append("many sites")
    elif ns >= 20: s += 5
    elif ns == 1: s -= 6; why.append("single-site")

    # 3b. GEOGRAPHY — US / European footprint.
    gp, glbl = _geo_fit(t["lead_country"], t["registry_sources"])
    s += gp
    if glbl: why.append(glbl)

    # 3. SOURCE STRENGTH — corroborated across registries
    try:
        n_reg = len(json.loads(t["registry_sources"] or "[]"))
    except Exception:
        n_reg = 1
    if n_reg >= 2: s += 5; why.append(f"{n_reg} registries")

    # 4. CONFIDENCE — field completeness
    filled = sum(1 for f in (t["sponsor"], t["start_date"], t["enrollment"],
                             t["num_sites"], t["brief_summary"]) if f)
    s += filled  # 0-5

    # 5. CONTACTABILITY (feeds uniqueness/actionability)
    if t["pi_email"]: s += 5; why.append("contactable")

    return max(0, min(s, 100)), ", ".join(dict.fromkeys(why))[:90] or "baseline"


def _illustrative_grant_score(g):
    """5-axis-style grant opportunity score (0-100).

    Grants are an EARLY signal, but an old award means the work likely already
    started — so immediacy rewards a recent award and a near/future project
    start, and penalizes projects that began long ago.
    """
    s, why = 0, []

    # 1. IMMEDIACY — for grants the timing signal is AWARD RECENCY (a freshly
    # funded project is just spinning up = early) plus PROJECT AGE. A grant's
    # start ≈ its award, so a past start is normal and NOT penalized; instead a
    # long-running project (old start = renewal of ongoing work) is penalized —
    # AiCure already missed that one.
    da = _days_from_now(g["award_date"])
    if da is not None and da >= -180: s += 22; why.append("just awarded")
    elif da is not None and da >= -540: s += 10; why.append("awarded recently")
    elif da is not None and da < -1095: s -= 8; why.append("old award")
    ds = _days_from_now(g["start_date"])
    if ds is not None and ds < -1095: s -= 14; why.append("long-running project")
    de = _days_from_now(g["end_date"])
    if de is not None and de > 365: s += 6; why.append("long runway")

    # 2. AICURE-CAPABILITY FIT (dominant) — operational angle in the abstract.
    fp, fwhy, _has = _grant_aicure_fit(g)
    s += fp; why += fwhy

    # 2b. THERAPEUTIC-AREA GATE + award size. amount_usd is only one slice of a
    # sponsor's spend, so it's a BONUS-only signal (large = good), never penalized.
    apts, _ = _AREA_FIT.get(g["therapeutic_area"], (-14, "off-core area"))
    s += int(apts * 0.5)
    amt = g["amount_usd"] or 0
    if amt >= 10_000_000: s += 20; why.append("very large award")
    elif amt >= 5_000_000: s += 14; why.append("large award")
    elif amt >= 1_000_000: s += 8; why.append("$1M+")

    # 2c. GEOGRAPHY — US / European footprint.
    gp, glbl = _geo_fit(g["country"])
    s += gp
    if glbl: why.append(glbl)

    # 3. SOURCE STRENGTH / CORROBORATION — tied to a real registered trial
    if g["linked_trial_id"]: s += 12; why.append("linked to trial")

    # 4. CONFIDENCE — has usable abstract + named org
    if g["abstract"] and len(g["abstract"]) > 200: s += 6
    if g["organization"]: s += 4
    if g["pi_name"]: s += 4; why.append("PI named")

    return max(0, min(s, 100)), ", ".join(dict.fromkeys(why))[:90] or "baseline"




# ── public API ───────────────────────────────────────────────────────────────

def score_trial(trial):
    """0-100 AiCure opportunity score for a trial (number only)."""
    return _illustrative_trial_score(trial)[0]


def score_grant(grant):
    """0-100 AiCure opportunity score for a grant (number only)."""
    return _illustrative_grant_score(grant)[0]
