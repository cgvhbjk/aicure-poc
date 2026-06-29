"""Known AiCure customers + CRO partners (§6).

Seeded from the historical won-deal book. A *new* trial or grant from a sponsor
AiCure has already sold to is the single highest-probability lead (the book is
dominated by repeat/expansion business), so the scorer boosts these. Matching is
substring-based on distinctive, lowercased name fragments after normalizing the
sponsor through org_extractor.resolve_alias — so "Neumora Therapeutics, Inc."
and "NEUMORA" both match "neumora".

CROs (Syneos / ICON / PPD / IQVIA / Premier / …) run many sponsors' trials; a
CRO-run trial must NOT be penalized — surface the CRO instead. This list is
analyst-editable: add a fragment to grow coverage.
"""

from org_extractor import resolve_alias

# Distinctive lowercase name fragments of won accounts. Kept specific enough to
# avoid false positives. Big-pharma names that resolve_alias already canonicalizes
# are included as their canonical fragment.
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
    "bayer", "astellas", "glaxosmithkline", "gsk", "bristol", "abbvie", "allergan",
    "jazz", "eisai", "chugai", "taisho", "pfizer",
    # academic / government won accounts
    "department of defense", "neurovance",
]

# Distinctive CRO name fragments (mirror org_extractor.KNOWN_ALIASES CRO entries).
CRO_FRAGMENTS = [
    "iqvia", "icon", "parexel", "syneos", "ppd", "medpace", "premier research",
    "worldwide clinical", "fortrea", "labcorp", "covance", "lotus clinical",
    "precision for medicine", "clinilabs", "charles river", "rho ", "thermo fisher",
]


def _norm(name: str) -> str:
    if not name:
        return ""
    return resolve_alias(name).lower().strip()


def matched_customer(sponsor: str):
    """Return the matching customer fragment if `sponsor` is a known AiCure
    customer, else None."""
    n = _norm(sponsor)
    if not n:
        return None
    for frag in KNOWN_CUSTOMER_FRAGMENTS:
        if frag in n:
            return frag
    return None


def is_known_customer(sponsor: str) -> bool:
    return matched_customer(sponsor) is not None


def is_cro(name: str) -> bool:
    n = _norm(name)
    if not n:
        return False
    return any(frag in n for frag in CRO_FRAGMENTS)
