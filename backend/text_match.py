"""Shared text-matching helpers and keyword lists.

Single source of truth for the substring keyword detection that the news parser
(rss_parser), the trial puller (ct_puller), the grant utils (grant_utils), and
the NLP layer (news_nlp) all need. These previously kept near-duplicate copies
of `_flag`, `DRUG_KEYWORDS`, and the therapeutic-area classifier that had quietly
drifted apart (e.g. the trial classifier lacked the oncology guard the grant one
had, and DRUG_KEYWORDS was missing the SGLT2/DPP-4 drugs on the news side).

TARGETING NOTE: AiCure's actual won-deal book is overwhelmingly CNS / psychiatry
/ neurology (schizophrenia, depression/MDD, PTSD, bipolar, ADHD, addiction,
Parkinson's, Alzheimer's, ALS, MS, tardive dyskinesia, …) — adherence-fragile,
self-administered populations. Cardiometabolic (obesity/GLP-1, diabetes, CV, NASH)
is a real but secondary slice. The classifier therefore leads with the CNS /
Psychiatry and Neurology buckets, then cardiometabolic, with oncology kept
off-focus (those patients adhere, so there's no AiCure pill-adherence angle).
"""

# Drug names / classes AiCure cares about. Substring-matched. Detection is kept
# broad on purpose (matching brand names only ever finds MORE rows); the pullers'
# *search queries*, by contrast, are deliberately general (indications / classes,
# not brand names) — see ct_puller / rss_parser / grants/*.
#
# Cardiometabolic drugs (the original list) + CNS/psychiatric drug classes that
# dominate the won book. General classes (antipsychotic / antidepressant / …) plus
# a few high-frequency molecules seen across won CNS trials.
DRUG_KEYWORDS = [
    # cardiometabolic
    "semaglutide", "tirzepatide", "liraglutide", "dulaglutide", "ozempic",
    "wegovy", "mounjaro", "victoza", "saxenda", "rybelsus", "jardiance",
    "farxiga", "trulicity", "metformin", "insulin", "glp-1", "sglt2",
    "dpp-4", "sitagliptin", "empagliflozin", "dapagliflozin",
    # CNS / psychiatric drug classes (general — the primary focus)
    "antipsychotic", "antidepressant", "ssri", "snri", "antiepileptic",
    "anticonvulsant", "benzodiazepine", "stimulant", "mood stabilizer",
    "muscarinic", "psychedelic", "ketamine", "esketamine", "brexpiprazole",
    "cariprazine", "lumateperone", "zuranolone", "vmat2",
]


def flag(text, keywords) -> bool:
    """True if any keyword appears as a case-insensitive substring of text.

    Tolerates None (returns False). Callers that persist into an INTEGER column
    should wrap with int() — see ct_puller.
    """
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords)


# ── Therapeutic-area classifier ───────────────────────────────────────────────
# One ladder used for trials, grants, and news so the same text always lands in
# the same bucket regardless of ingestion path. CNS / Psychiatry and Neurology
# are checked FIRST (primary focus), then cardiometabolic (secondary). Oncology is
# guarded out to "Other".
ONCOLOGY_CUES = ["cancer", "tumor", "tumour", "oncolog", "carcinoma", "neoplas",
                 "melanoma", "lymphoma", "leukemia", "leukaemia", "metasta", "glioma"]

# Psychiatric / behavioral-health cues (the single largest won-deal cluster).
CNS_PSYCH_CUES = [
    "schizophren", "psychosis", "psychotic", "depress", "mdd",
    "major depressive", "treatment-resistant", "treatment resistant",
    "ptsd", "post-traumatic", "post traumatic", "bipolar", "manic episode",
    "adhd", "attention deficit", "attention-deficit", "anxiety",
    "social anxiety", "ocd", "obsessive-compulsive", "addiction", "substance use",
    "substance-use", "alcohol use", "alcohol dependence", "opioid use",
    "smoking cessation", "nicotine", "borderline personality", "insomnia",
    "agitation", "tardive dyskinesia", "neuropsychiatr",
]
# Neurology cues (the second-largest won cluster — degenerative / movement / CNS).
NEURO_CUES = [
    "parkinson", "alzheimer", "dementia", "mild cognitive", "cognitive impairment",
    "huntington", "amyotrophic", "lou gehrig", "multiple sclerosis", "epilepsy",
    "seizure", "essential tremor", "neuropath", "migraine", "myasthenia",
    "spinal muscular", "ataxia",
]

# Strong cardiometabolic anchors — used by the oncology guard so a "cancer
# metabolism" record doesn't leak into the metabolic bucket.
_STRONG_CM = ["semaglutide", "tirzepatide", "liraglutide", "dulaglutide", "glp-1",
              "obesity", "obese", "diabet", "heart failure", "atrial fib"]
_METABOLIC_CUES = ["obes", "glp", "weight", "semaglutide", "tirzepatide",
                   "liraglutide", "dulaglutide", "bariatric", "metaboli"]


def classify_area(text: str) -> str:
    t = (text or "").lower()
    # Oncology guard: cancer-metabolism / tumor records were leaking into
    # "Metabolic / GLP-1" via loose substrings (e.g. "metabolic"). If the text is
    # clearly oncology and has no strong cardiometabolic anchor, it's off-focus.
    if any(k in t for k in ONCOLOGY_CUES) and not any(k in t for k in _STRONG_CM):
        return "Other"
    # PRIMARY focus — CNS / psychiatry then neurology.
    if any(k in t for k in CNS_PSYCH_CUES):
        return "CNS / Psychiatry"
    if any(k in t for k in NEURO_CUES):
        return "Neurology"
    # SECONDARY focus — cardiometabolic ladder.
    if any(k in t for k in _METABOLIC_CUES):
        return "Metabolic / GLP-1"
    if "diabet" in t or "insulin" in t or "glycem" in t or "glycaem" in t:
        return "Diabetes"
    if any(k in t for k in ["cardiac", "heart", "coronary", "atrial", "cardiovascular",
                            "hypertens", "blood pressure", "stroke", "arrhythm", "vascular"]):
        return "Cardiovascular"
    if any(k in t for k in ["nash", "nafld", "fatty liver", "hepatic", "steatohep"]):
        return "Liver / NASH"
    if any(k in t for k in ["kidney", "renal", "nephro", "ckd"]):
        return "Renal"
    if "adher" in t or "compliance" in t:
        return "Adherence / Outcomes"
    return "Other"
