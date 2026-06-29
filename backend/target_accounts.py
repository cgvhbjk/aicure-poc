"""Known AiCure customers + CRO partners (§6).

Seeded from the historical won-deal book. A *new* trial or grant from a sponsor
AiCure has already sold to is the single highest-probability lead (the book is
dominated by repeat/expansion business), so the scorer boosts these. Matching is
substring-based on distinctive, lowercased name fragments after normalizing the
sponsor through org_aliases.resolve_alias — so "Neumora Therapeutics, Inc."
and "NEUMORA" both match "neumora".

CROs (Syneos / ICON / PPD / IQVIA / Premier / …) run many sponsors' trials; a
CRO-run trial must NOT be penalized — surface the CRO instead. This list is
analyst-editable: add a fragment to grow coverage.
"""

import re

# Import from the dependency-free org_aliases (NOT org_extractor) so this module —
# and scoring, which imports it — stays free of any db dependency.
from org_aliases import resolve_alias

# Distinctive lowercase name fragments of won accounts. Matched on WORD
# BOUNDARIES (not bare substrings) so a fragment can't hide inside an unrelated
# name — e.g. "roche" must not match "University of Rochester", "icon" must not
# match "Silicon Therapeutics". Multi-token fragments where a single word would
# collide with a common place/word are spelled out (e.g. "bristol myers", not
# "bristol", which would match "University of Bristol").
KNOWN_CUSTOMER_FRAGMENTS = [
    # CNS / psych / neuro biotechs (the core of the book)
    "neumora", "karuna", "praxis precision", "neurocrine", "sage therapeutic",
    "xenon", "aptinyx", "relmada", "cerevel", "recognify", "supernus", "newron",
    "vistagen", "neurorx", "cavion", "avanir", "alkermes", "oryzon", "neurobo",
    "embera", "curasen", "marvelbiome", "bionomics", "intra-cellular", "axsome",
    # cardiometabolic / other won biotechs
    "akero", "mineralys", "fulcrum therapeutic", "rezolute", "oculis", "kallyope",
    "rivus", "kailera", "corxel", "iterum", "homology medicine", "blueprint medicine",
    "kymera", "alladapt", "enteris", "eliem", "climbbio", "ancora",
    # big pharma / established sponsors with won studies
    "boehringer", "roche", "merck", "takeda", "otsuka", "janssen", "biogen",
    "bayer", "astellas", "glaxosmithkline", "gsk", "bristol myers", "abbvie",
    "allergan", "jazz pharma", "eisai", "chugai", "taisho", "pfizer",
    # academic / government won accounts
    "department of defense", "neurovance",
]

# Distinctive CRO name fragments (a curated subset of org_aliases.KNOWN_ALIASES CRO entries).
CRO_FRAGMENTS = [
    "iqvia", "icon", "parexel", "syneos", "ppd", "medpace", "premier research",
    "worldwide clinical", "fortrea", "labcorp", "covance", "lotus clinical",
    "precision for medicine", "clinilabs", "charles river", "rho", "thermo fisher",
]


def _norm(name: str) -> str:
    if not name:
        return ""
    return resolve_alias(name).lower().strip()


def _frag_match(fragments, name: str):
    """Return the first fragment that occurs as a whole word/phrase in `name`
    (word-boundary match), else None. Avoids the substring false positives a bare
    `frag in name` produced (roche↔Rochester, icon↔Silicon, bristol↔Bristol)."""
    for frag in fragments:
        if re.search(r"\b" + re.escape(frag) + r"\b", name):
            return frag
    return None


def matched_customer(sponsor: str):
    """Return the matching customer fragment if `sponsor` is a known AiCure
    customer, else None."""
    n = _norm(sponsor)
    if not n:
        return None
    return _frag_match(KNOWN_CUSTOMER_FRAGMENTS, n)


def is_known_customer(sponsor: str) -> bool:
    return matched_customer(sponsor) is not None


def is_cro(name: str) -> bool:
    n = _norm(name)
    if not n:
        return False
    return _frag_match(CRO_FRAGMENTS, n) is not None
