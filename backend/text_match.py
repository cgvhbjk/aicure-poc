"""Shared text-matching helpers and keyword lists.

Single source of truth for the substring keyword detection that the news parser
(rss_parser), the trial puller (ct_puller), the grant utils (grant_utils), and
the NLP layer (news_nlp) all need. These previously kept near-duplicate copies
of `_flag`, `DRUG_KEYWORDS`, and the therapeutic-area classifier that had quietly
drifted apart (e.g. the trial classifier lacked the oncology guard the grant one
had, and DRUG_KEYWORDS was missing the SGLT2/DPP-4 drugs on the news side).
"""

# Cardiometabolic drug names AiCure cares about. Substring-matched. This is the
# union of the two prior lists (grant_utils had the SGLT2/DPP-4 entries the news
# parser lacked). Detection is additive, so widening it only finds more drugs.
DRUG_KEYWORDS = [
    "semaglutide", "tirzepatide", "liraglutide", "dulaglutide", "ozempic",
    "wegovy", "mounjaro", "victoza", "saxenda", "rybelsus", "jardiance",
    "farxiga", "trulicity", "metformin", "insulin", "glp-1", "sglt2",
    "dpp-4", "sitagliptin", "empagliflozin", "dapagliflozin",
]


def flag(text, keywords) -> bool:
    """True if any keyword appears as a case-insensitive substring of text.

    Tolerates None (returns False). Callers that persist into an INTEGER column
    should wrap with int() — see ct_puller.
    """
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords)


# Therapeutic-area classifier. One ladder used for trials, grants, and news so
# the same text always lands in the same bucket regardless of ingestion path.
_ONCOLOGY_CUES = ["cancer", "tumor", "tumour", "oncolog", "carcinoma", "neoplas",
                  "melanoma", "lymphoma", "leukemia", "leukaemia", "metasta", "glioma"]
_STRONG_CM = ["semaglutide", "tirzepatide", "liraglutide", "dulaglutide", "glp-1",
              "obesity", "obese", "diabet", "heart failure", "atrial fib"]
# "metaboli" subsumes "metabolic syndrome"/"cardiometabolic"/"metabolism";
# "weight" subsumes "weight loss"/"weight management". This is the broader of the
# two prior cue sets, so no row that classified as Metabolic before stops doing so.
_METABOLIC_CUES = ["obes", "glp", "weight", "semaglutide", "tirzepatide",
                   "liraglutide", "dulaglutide", "bariatric", "metaboli"]


def classify_area(text: str) -> str:
    t = (text or "").lower()
    # Oncology guard: cancer-metabolism / tumor records were leaking into
    # "Metabolic / GLP-1" via loose substrings (e.g. "metabolic"). If the text is
    # clearly oncology and has no strong cardiometabolic anchor, it's off-focus.
    if any(k in t for k in _ONCOLOGY_CUES) and not any(k in t for k in _STRONG_CM):
        return "Other"
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
