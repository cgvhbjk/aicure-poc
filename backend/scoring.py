"""AiCure opportunity scoring — the single source of truth.

Both the email digests (emailer.py) and the API/grid (api.py, via the precomputed
aicure_fit column) score trials and grants with these formulas, so a lead ranks
identically wherever it surfaces. `_illustrative_trial_score` / `_illustrative_grant
_score` each return (score 0-100, why-string); `score_trial` / `score_grant` are the
convenience wrappers that return just the number.

TARGETING (from the won-deal book):
  * Focus = CNS / psychiatry & neurology first, cardiometabolic second; oncology is
    off-focus (those patients adhere, so there's no AiCure pill-adherence angle).
  * The product touchpoint that matters most is a SELF-ADMINISTERED PILL in an
    ADHERENCE-FRAGILE population (psychiatric / addiction / neuro). A transdermal
    PATCH is explicitly NOT a touchpoint and is gated out.
  * Timing: engage 6–12 months BEFORE a trial starts; a trial starting in <6 months
    (or already underway) is too late to win and is gated out. Phase 4 is out of
    scope. A recently-completed Phase 1 is treated as a "graduate" — a Phase 2 is
    expected in the target window.
  * A new trial/grant from a KNOWN AiCure customer is the strongest lead (repeat
    business dominates the book); CRO-run trials are recognized, not penalized.

Off-target leads are SUPPRESSED (scored 0 with an "excluded: …" reason) rather than
merely down-ranked, so the sorted grids/digests stay tight.
"""
import json
from datetime import datetime
from dateutil.parser import parse as dateparse

from text_match import ONCOLOGY_CUES, CNS_PSYCH_CUES, NEURO_CUES
from target_accounts import is_known_customer, is_cro


def _g(row, key, default=None):
    """Column accessor tolerant of both dict and sqlite3.Row, and of SELECT lists
    that omit a column (returns `default` instead of raising)."""
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return v if v is not None else default


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


# ── Therapeutic-area commercial fit ───────────────────────────────────────────
# CNS / psychiatry & neurology lead (AiCure's real book); cardiometabolic second.
_AREA_FIT = {
    "CNS / Psychiatry":     (22, "CNS/psych fit"),
    "Neurology":            (20, "neurology fit"),
    "Metabolic / GLP-1":    (12, "GLP-1/metabolic fit"),
    "Diabetes":             (10, "diabetes fit"),
    "Cardiovascular":       (10, "CV fit"),
    "Liver / NASH":         (8,  "NASH fit"),
    "Renal":                (8,  "renal fit"),
    "Adherence / Outcomes": (10, "adherence fit"),
    "Other":                (-14, "off-core area"),
}

# Trial statuses that mean the trial is already underway / closed — too late to win
# (handled as a hard gate unless the row is a Phase-1 graduate).
_UNDERWAY_STATUSES = {
    "RECRUITING", "ENROLLING_BY_INVITATION", "ACTIVE_NOT_RECRUITING", "COMPLETED",
    "TERMINATED", "SUSPENDED", "WITHDRAWN", "APPROVED_FOR_MARKETING",
    "NO_LONGER_AVAILABLE",
}

# ── AiCure-capability fit cues ────────────────────────────────────────────────
_ORAL_CUES = ["oral", "tablet", "capsule", "pill", "by mouth", "orally", "swallow",
              "rybelsus", "lozenge", "sublingual"]
_SELF_ADMIN_CUES = _ORAL_CUES + [
    "self-administ", "self administ", "subcutaneous", "self-inject", "self inject",
    "autoinjector", "auto-injector", "pen injector", "prefilled pen", "take-home",
    "outpatient", "once-daily", "once daily", "twice daily", "daily dosing",
]
# A transdermal patch is NOT a pill-ingestion touchpoint — gate it out.
_PATCH_NEG_CUES = ["transdermal", "patch", "skin patch", "topical patch",
                   "dermal patch", "adhesive patch"]
_WEIGHT_VITAL_CUES = [
    "weight", "body weight", "bmi", "obes", "blood pressure", "waist circumference",
    "vital signs", "weight loss", "weight management",
]


def _is_patch_only(text):
    """True if the text describes a transdermal patch with no oral/pill cue."""
    return any(k in text for k in _PATCH_NEG_CUES) and not any(k in text for k in _ORAL_CUES)


def _adherence_risk(text, area):
    """Whether the indication is one where non-adherence is common (the populations
    AiCure actually wins). CNS/neuro areas qualify, as do explicit psych/addiction
    cues. Oncology / acute contexts do NOT."""
    if area in ("CNS / Psychiatry", "Neurology"):
        return True
    return any(k in text for k in CNS_PSYCH_CUES)


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
    Returns (points, labels, has_signal). A patch-only intervention earns no
    self-administered credit; a self-administered pill in an adherence-fragile
    population earns the most."""
    text = " ".join(filter(None, [t["interventions"], t["brief_summary"],
                                  t["conditions"], t["title_brief"]])).lower()
    area = t["therapeutic_area"]
    pts, why, has = 0, [], False

    patch_only = _is_patch_only(text)
    if any(k in text for k in _SELF_ADMIN_CUES) and not patch_only:
        base = 26
        if _adherence_risk(text, area):
            base += 8          # adherence-fragile population — AiCure's sweet spot
            why.append("self-administered (adherence)")
        else:
            why.append("self-administered (adherence)")
        pts += base; has = True
    elif patch_only:
        pts -= 6; why.append("transdermal patch — no pill touchpoint")

    if any(k in text for k in _WEIGHT_VITAL_CUES):
        pts += 15; why.append("weight/vitals endpoint"); has = True
    if t["digital_biomarkers"]: pts += 9; why.append("digital biomarkers"); has = True
    if t["dct_elements"]: pts += 8; why.append("DCT"); has = True
    if t["epro_ecoa"]: pts += 7; why.append("ePRO"); has = True
    # Oncology dampener — even a self-administered oncology oral is a weak AiCure
    # fit (those patients adhere); pull the points back.
    if any(k in text for k in ONCOLOGY_CUES) and area not in ("CNS / Psychiatry", "Neurology"):
        pts -= 10
    if not has:
        pts -= 25; why.append("no AiCure touchpoint")
    # de-dupe labels while preserving order
    why = list(dict.fromkeys(why))
    return pts, why, has


# ── geography fit ─────────────────────────────────────────────────────────────
_US_TERMS = {"united states", "usa", "u.s.", "us", "u.s.a."}
_EU_COUNTRIES = {
    "germany", "france", "spain", "italy", "netherlands", "belgium", "sweden",
    "denmark", "austria", "poland", "portugal", "finland", "norway", "switzerland",
    "ireland", "czech republic", "czechia", "greece", "hungary", "romania",
    "bulgaria", "slovakia", "slovenia", "croatia", "lithuania", "latvia", "estonia",
    "luxembourg", "iceland", "united kingdom", "uk", "england", "scotland", "wales",
}
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
        return 0, None
    if c:
        return -12, "outside US/EU"
    try:
        regs = json.loads(registry_sources or "[]")
    except Exception:
        regs = [registry_sources] if registry_sources else []
    for r in regs:
        if r in _REG_GEO:
            label, pts = _REG_GEO[r]
            return pts, ("EU operations" if label == "EU" else "outside US/EU")
    return 0, None


# ── grant capability cues ─────────────────────────────────────────────────────
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
    """AiCure-capability fit for a grant, using the same product hierarchy as
    trials: PILL adherence (mature product) > WEIGHT verification > other biodata.
    Returns (points, fit_labels, has_signal)."""
    text = " ".join(filter(None, [g["title"], g["abstract"], g["conditions"]])).lower()
    area = g["therapeutic_area"]
    pts, labels = 0, []
    patch_only = _is_patch_only(text)
    if any(k in text for k in _GRANT_PILL_CUES) and not patch_only:
        base = 30
        if _adherence_risk(text, area):
            base += 6
        pts += base; labels.append("self-administered (adherence)")
    if any(k in text for k in _WEIGHT_VITAL_CUES):
        pts += 18; labels.append("weight/vitals endpoint")
    if any(k in text for k in _GRANT_DIGITAL_CUES):
        pts += 10; labels.append("digital biomarkers")
    if any(k in text for k in _GRANT_EPRO_CUES):
        pts += 8; labels.append("ePRO")
    if not labels:
        return -12, [], False
    return pts, labels, True


# ── Phase-1 graduate detection ────────────────────────────────────────────────
def _trial_phase1_graduate(t):
    """A recently-COMPLETED Phase 1 trial signals a Phase 2 is likely in the 6–12mo
    target window. Returns (is_graduate, why)."""
    phase = (t["phase"] or "").lower().replace(" ", "").replace("_", "")
    status = (t["status"] or "").upper()
    if "phase1" not in phase or status != "COMPLETED":
        return False, None
    # "recent" completion — within ~15 months of either completion field.
    for fld in ("primary_completion", "study_completion"):
        d = _days_from_now(_g(t, fld))
        if d is not None and -460 <= d <= 0:
            return True, "P1 complete → P2 expected"
    return False, None


# ── trial scoring ─────────────────────────────────────────────────────────────
def _illustrative_trial_score(t):
    """Opportunity score (0-100) with hard gates that suppress off-target leads."""
    why = []

    # ── HARD GATES ────────────────────────────────────────────────────────────
    ph = (t["phase"] or "").lower().replace(" ", "").replace("_", "")
    if "phase4" in ph:
        return 0, "excluded: phase 4 (post-marketing) — out of scope"

    text = " ".join(filter(None, [t["interventions"], t["brief_summary"],
                                  t["conditions"], t["title_brief"]])).lower()
    if _is_patch_only(text):
        return 0, "excluded: transdermal patch — no pill touchpoint"

    grad, grad_why = _trial_phase1_graduate(t)
    status = (t["status"] or "").upper()
    d_start = _days_from_now(t["start_date"])
    if not grad:
        if status in _UNDERWAY_STATUSES:
            return 0, "excluded: already underway / closed — too late to win"
        if d_start is not None and d_start < 180:
            return 0, "excluded: starts in <6mo — too late to win"

    fp, fwhy, has_fit = _trial_aicure_fit(t)
    if not has_fit:
        return 0, "excluded: no AiCure product touchpoint"

    # ── SCORING AXES ──────────────────────────────────────────────────────────
    s = 0

    # 1. Timing — reward the 6–12mo pre-start window; graduates get the P2 bonus.
    if grad:
        s += 24; why.append(grad_why)
    elif d_start is not None:
        if 180 <= d_start <= 365:
            s += 28; why.append("starts in 6–12mo (ideal)")
        elif d_start <= 540:
            s += 18; why.append("starts in 12–18mo")
        elif d_start <= 730:
            s += 12; why.append("starts in 18–24mo")
        else:
            s += 6; why.append("start >24mo (speculative)")
    else:
        # Pre-start status with no date — still a candidate, modest credit.
        s += 10; why.append("not yet recruiting")

    # 2. Indication fit (uses the CNS/neuro-led ladder).
    apts, albl = _AREA_FIT.get(t["therapeutic_area"], (-14, "off-core area"))
    s += apts; why.append(albl)

    # 3. AiCure touchpoint × adherence-risk.
    s += fp; why += fwhy

    # 3b. Phase nudge — Phase 2/3 are the sweet spot for getting in.
    phl = (t["phase"] or "").lower()
    if "3" in phl or "iii" in phl: s += 6
    elif "2" in phl or "ii" in phl: s += 8

    # 4. Known sponsor / CRO.
    if is_known_customer(t["sponsor"]):
        s += 18; why.append("existing AiCure customer")
    elif is_cro(t["sponsor"]) or is_cro(_g(t, "cro_named")):
        why.append("CRO-run")  # recognized, not penalized

    # 5. Scale & geography.
    enr = t["enrollment"]
    if enr is None: s -= 6; why.append("size unknown")
    elif enr >= 2000: s += 18; why.append("very large (2k+)")
    elif enr >= 1000: s += 12; why.append("large (1k+)")
    elif enr >= 500: s += 7; why.append("500+ enroll")
    elif enr >= 100: s += 2
    elif enr > 0: s -= 10; why.append("small")
    ns = t["num_sites"] or 0
    if ns >= 50: s += 8; why.append("many sites")
    elif ns >= 20: s += 4
    elif ns == 1: s -= 4; why.append("single-site")
    gp, glbl = _geo_fit(t["lead_country"], t["registry_sources"])
    s += gp
    if glbl: why.append(glbl)

    # 6. Source strength / confidence / contactability.
    try:
        n_reg = len(json.loads(t["registry_sources"] or "[]"))
    except Exception:
        n_reg = 1
    if n_reg >= 2: s += 4; why.append(f"{n_reg} registries")
    s += sum(1 for f in (t["sponsor"], t["start_date"], t["enrollment"],
                         t["num_sites"], t["brief_summary"]) if f)
    if t["pi_email"]: s += 5; why.append("contactable")

    return max(0, min(s, 100)), ", ".join(dict.fromkeys(why))[:90] or "baseline"


# ── grant scoring (full overhaul) ─────────────────────────────────────────────
def _illustrative_grant_score(g):
    """Opportunity score (0-100) for a grant, on an explicit axis rubric.

    Hard-gates non-human / preclinical work and pure basic science; otherwise
    rewards CNS/neuro, adherence-relevant, clinically-staged, recently-funded
    projects.
    """
    why = []
    text = " ".join(filter(None, [g["title"], g["abstract"], g["conditions"]])).lower()

    # ── HARD GATES ────────────────────────────────────────────────────────────
    if _g(g, "human_subjects", 1) == 0:
        return 0, "excluded: non-human / preclinical study"

    clinical = bool(_g(g, "phase_mentioned") or g["linked_trial_id"]
                    or any(k in text for k in ("clinical trial", "ind ", "in humans",
                                               "patients", "participants", "randomized")))
    fp, fwhy, has_fit = _grant_aicure_fit(g)
    area = g["therapeutic_area"]
    if not clinical and not has_fit and area in (None, "", "Other"):
        return 0, "excluded: basic-science grant — no clinical/AiCure angle"

    # ── SCORING AXES ──────────────────────────────────────────────────────────
    s = 0

    # 1. Indication fit (≈25).
    apts, albl = _AREA_FIT.get(area, (-14, "off-core area"))
    s += apts; why.append(albl)

    # 2. AiCure touchpoint × adherence (≈25).
    s += fp; why += fwhy

    # 3. Clinical readiness (≈15).
    if g["linked_trial_id"]: s += 12; why.append("linked to trial")
    elif _g(g, "phase_mentioned"): s += 9; why.append("clinical phase")
    elif clinical: s += 6; why.append("clinical/human")
    else: s -= 6; why.append("pre-clinical stage")

    # 4. Timing / recency (≈15) — recent award & near-future start good; old /
    #    long-running penalized (AiCure already missed those).
    da = _days_from_now(g["award_date"])
    if da is not None and da >= -180: s += 14; why.append("just awarded")
    elif da is not None and da >= -540: s += 7; why.append("awarded recently")
    elif da is not None and da < -1095: s -= 8; why.append("old award")
    ds = _days_from_now(g["start_date"])
    if ds is not None and ds < -1095: s -= 12; why.append("long-running project")
    de = _days_from_now(g["end_date"])
    if de is not None and de > 365: s += 5; why.append("long runway")

    # 5. Geography (≈10).
    gp, glbl = _geo_fit(g["country"])
    s += gp
    if glbl: why.append(glbl)

    # 6. Confidence / corroboration (≈10).
    if g["abstract"] and len(g["abstract"]) > 200: s += 5
    if g["organization"]: s += 3
    if g["pi_name"]: s += 4; why.append("PI named")

    # 6b. Known sponsor / funder bonus.
    if is_known_customer(g["organization"]) or is_known_customer(_g(g, "sponsor_funder")):
        s += 12; why.append("existing AiCure customer")

    # 7. Award size (≈10, bonus only).
    amt = g["amount_usd"] or 0
    if amt >= 10_000_000: s += 10; why.append("very large award")
    elif amt >= 5_000_000: s += 7; why.append("large award")
    elif amt >= 1_000_000: s += 4; why.append("$1M+")

    return max(0, min(s, 100)), ", ".join(dict.fromkeys(why))[:90] or "baseline"


# ── public API ───────────────────────────────────────────────────────────────
def score_trial(trial):
    """0-100 AiCure opportunity score for a trial (number only)."""
    return _illustrative_trial_score(trial)[0]


def score_grant(grant):
    """0-100 AiCure opportunity score for a grant (number only)."""
    return _illustrative_grant_score(grant)[0]
