import json
import re
from datetime import datetime

from db import get_connection

KNOWN_ALIASES = {
    # === Big pharma — metabolic / cardiovascular / general ===
    "novo nordisk": ["novo nordisk a/s", "novo nordisk inc", "novo nordisk as", "novo nordisk pharmaceuticals", "nn"],
    "eli lilly": ["eli lilly and company", "lilly", "lilly usa", "lilly research", "eli lilly & co"],
    "astrazeneca": ["astrazeneca plc", "astrazeneca ab", "astrazeneca us", "astrazeneca pharmaceuticals", "az"],
    "pfizer": ["pfizer inc", "pfizer inc.", "pfizer ltd", "pfizer pharmaceuticals", "pfizer global research"],
    "merck": ["merck & co", "merck sharp & dohme", "msd", "merck kgaa", "merck research laboratories"],
    "sanofi": ["sanofi sa", "sanofi-aventis", "sanofi us", "sanofi pasteur", "sanofi r&d"],
    "roche": ["f. hoffmann-la roche", "hoffmann-la roche", "genentech", "roche pharmaceuticals", "roche products"],
    "johnson & johnson": ["j&j", "janssen", "janssen research", "janssen pharmaceuticals", "janssen-cilag", "janssen scientific affairs"],
    "abbvie": ["abbvie inc", "abbvie inc.", "abbvie ltd", "abbvie pharmaceuticals"],
    "amgen": ["amgen inc", "amgen inc.", "amgen research", "amgen ltd"],
    "boehringer ingelheim": ["boehringer ingelheim gmbh", "boehringer ingelheim pharma", "boehringer ingelheim international", "bi"],
    "bristol myers squibb": ["bms", "bristol-myers squibb", "bristol myers squibb company", "bristol-myers squibb company"],
    "novartis": ["novartis pharmaceuticals", "novartis ag", "novartis pharma", "novartis institutes for biomedical research", "novartis biomedical research"],
    "gsk": ["glaxosmithkline", "glaxosmithkline plc", "gsk plc", "smithkline beecham"],
    "bayer": ["bayer ag", "bayer healthcare", "bayer pharmaceuticals", "bayer schering pharma", "bayer hispania"],
    "takeda": ["takeda pharmaceutical company", "takeda development center", "takeda pharmaceuticals u.s.a.", "takeda oncology"],
    "daiichi sankyo": ["daiichi sankyo inc", "daiichi sankyo co", "daiichi sankyo europe", "daiichi-sankyo"],
    "servier": ["les laboratoires servier", "servier laboratories", "servier r&d"],
    "otsuka": ["otsuka pharmaceutical", "otsuka pharmaceutical co", "otsuka pharmaceutical development & commercialization", "otsuka america pharmaceutical"],
    "astellas": ["astellas pharma", "astellas pharma inc", "astellas pharma global development", "astellas pharma europe"],
    "eisai": ["eisai co", "eisai inc", "eisai limited"],
    "mitsubishi tanabe": ["mitsubishi tanabe pharma", "tanabe research laboratories"],
    "sumitomo pharma": ["sumitomo dainippon pharma", "dainippon sumitomo pharma", "sunovion"],
    "hanmi pharmaceutical": ["hanmi pharm", "hanmi pharm co"],
    "lg chem": ["lg life sciences", "lg chem life sciences"],
    "yuhan corporation": ["yuhan", "yuhan corp"],
    "innovent biologics": ["innovent biologics inc", "innovent"],
    "celltrion": ["celltrion inc", "celltrion healthcare"],
    "samsung bioepis": ["samsung bioepis co"],
    "teva": ["teva pharmaceutical industries", "teva pharmaceuticals usa", "teva branded pharmaceutical products"],
    "viatris": ["mylan", "mylan pharmaceuticals", "viatris inc"],
    "sun pharmaceutical": ["sun pharma", "sun pharma advanced research", "sun pharmaceutical industries"],
    "cipla": ["cipla limited", "cipla ltd"],
    "dr reddys": ["dr. reddy's laboratories", "drl", "dr reddys laboratories"],
    "aurobindo": ["aurobindo pharma", "aurobindo pharma usa"],
    "hikma": ["hikma pharmaceuticals", "hikma pharmaceuticals plc"],
    "lupin": ["lupin limited", "lupin pharmaceuticals"],

    # === Major biotech ===
    "vertex pharmaceuticals": ["vertex", "vertex pharmaceuticals incorporated"],
    "gilead sciences": ["gilead", "gilead sciences inc"],
    "regeneron": ["regeneron pharmaceuticals", "regeneron pharmaceuticals inc"],
    "biogen": ["biogen idec", "biogen inc", "biogen ma inc"],
    "moderna": ["moderna inc", "modernatx", "moderna therapeutics"],
    "biontech": ["biontech se", "biontech rna pharmaceuticals"],
    "alnylam": ["alnylam pharmaceuticals", "alnylam pharmaceuticals inc"],
    "incyte": ["incyte corporation", "incyte biosciences"],
    "alexion": ["alexion pharmaceuticals", "alexion pharma"],
    "ionis pharmaceuticals": ["ionis", "isis pharmaceuticals"],
    "exelixis": ["exelixis inc"],
    "bluebird bio": ["bluebird bio inc"],
    "neurocrine biosciences": ["neurocrine", "neurocrine biosciences inc"],
    "jazz pharmaceuticals": ["jazz pharmaceuticals plc", "jazz pharma"],
    "horizon therapeutics": ["horizon pharma", "horizon therapeutics plc"],

    # === Obesity / GLP-1 / metabolic specialists ===
    "madrigal pharmaceuticals": ["madrigal pharma", "madrigal"],
    "89bio": ["89bio inc"],
    "akero therapeutics": ["akero", "akero therapeutics inc"],
    "altimmune": ["altimmune inc"],
    "structure therapeutics": ["structure therapeutics inc"],
    "viking therapeutics": ["viking", "viking therapeutics inc"],
    "carmot therapeutics": ["carmot"],
    "terns pharmaceuticals": ["terns", "terns pharmaceuticals inc"],
    "rivus pharmaceuticals": ["rivus pharma", "rivus"],
    "eccogene": ["eccogene inc"],
    "skye bioscience": ["skye bioscience inc", "emerald health pharmaceuticals"],
    "inversago pharma": ["inversago"],
    "camurus": ["camurus ab"],
    "metsera": ["metsera inc"],
    "kallyope": ["kallyope inc"],
    "regor therapeutics": ["regor pharmaceuticals", "regor therapeutics group"],

    # === Cardiovascular specialists ===
    "cytokinetics": ["cytokinetics incorporated", "cytokinetics inc"],
    "bridgebio": ["bridgebio pharma", "bridgebio"],
    "tenaya therapeutics": ["tenaya", "tenaya therapeutics inc"],
    "tenax therapeutics": ["tenax"],
    "cardurion pharmaceuticals": ["cardurion"],
    "lexicon pharmaceuticals": ["lexicon", "lexicon pharmaceuticals inc"],
    "applied therapeutics": ["applied therapeutics inc"],
    "edwards lifesciences": ["edwards lifesciences corporation", "edwards"],
    "medtronic": ["medtronic plc", "medtronic vascular", "medtronic inc"],
    "abbott": ["abbott laboratories", "abbott vascular", "abbott medical"],
    "boston scientific": ["boston scientific corporation"],

    # === CRO / clinical operations ===
    "iqvia": ["iqvia inc", "iqvia rds", "quintiles ims", "iqvia biotech"],
    "icon plc": ["icon clinical research", "icon", "icon plc"],
    "parexel": ["parexel international", "parexel international corporation"],
    "syneos health": ["syneos", "inc research", "incresearch"],
    "labcorp drug development": ["covance", "labcorp", "lab corp drug development", "covance inc"],
    "wuxi apptec": ["wuxi apptec co", "wuxi pharmatech"],
    "wuxi biologics": ["wuxi biologics (hong kong)"],
    "ppd": ["pharmaceutical product development", "ppd inc", "thermo fisher scientific ppd"],
    "medpace": ["medpace holdings", "medpace inc"],
    "worldwide clinical trials": ["worldwide clinical trials inc"],
    "charles river": ["charles river laboratories", "charles river laboratories international"],
    "premier research": ["premier research international"],
    "clinipace": ["clinipace worldwide"],
    "fortrea": ["fortrea inc"],
    "alira health": ["alira health clinical"],

    # === Government / academic sponsors ===
    "national institutes of health": ["nih", "nih clinical center"],
    "national heart, lung, and blood institute": ["nhlbi"],
    "national institute of diabetes and digestive and kidney diseases": ["niddk"],
    "national cancer institute": ["nci", "nci/nih"],
    "national institute on aging": ["nia"],
    "national institute of mental health": ["nimh"],
    "department of veterans affairs": ["va", "us department of veterans affairs", "veterans health administration"],
    "mayo clinic": ["mayo foundation for medical education and research", "mayo clinic rochester"],
    "cleveland clinic": ["cleveland clinic foundation"],
    "massachusetts general hospital": ["mgh", "mass general"],
    "brigham and women's hospital": ["brigham and womens hospital", "brigham & women's hospital"],
    "stanford university": ["stanford", "stanford medical center", "stanford health care"],
    "duke university": ["duke", "duke clinical research institute", "dcri", "duke university medical center"],
    "johns hopkins university": ["johns hopkins", "johns hopkins school of medicine"],
    "university of california, san francisco": ["ucsf", "university of california san francisco"],
    "university of california, los angeles": ["ucla"],
    "university of pennsylvania": ["penn", "upenn", "perelman school of medicine"],
    "yale university": ["yale", "yale school of medicine"],
    "columbia university": ["columbia", "columbia university medical center"],
    "harvard university": ["harvard", "harvard medical school"],
    "university of oxford": ["oxford", "oxford university"],
    "university of cambridge": ["cambridge", "cambridge university"],
    "imperial college london": ["imperial college", "imperial"],
    "karolinska institutet": ["karolinska", "karolinska university hospital"],
    "leiden university medical center": ["lumc", "leiden umc"],
    "university of copenhagen": ["copenhagen university", "københavns universitet"],
    "seoul national university": ["seoul national university hospital", "snu"],
    "peking union medical college hospital": ["pumch"],

    # === Other notable sponsors ===
    "alkermes": ["alkermes plc", "alkermes inc"],
    "intercept pharmaceuticals": ["intercept pharma", "intercept"],
    "ironwood pharmaceuticals": ["ironwood", "ironwood pharmaceuticals inc"],
    "ardelyx": ["ardelyx inc"],
    "tricida": ["tricida inc"],
    "axsome therapeutics": ["axsome", "axsome therapeutics inc"],
    "sage therapeutics": ["sage", "sage therapeutics inc"],
    "agios pharmaceuticals": ["agios", "agios pharmaceuticals inc"],
    "blueprint medicines": ["blueprint medicines corporation"],
    "arcus biosciences": ["arcus biosciences inc"],
    "kymera therapeutics": ["kymera therapeutics inc"],
    "kura oncology": ["kura oncology inc"],
    "deciphera pharmaceuticals": ["deciphera"],
    "puma biotechnology": ["puma biotechnology inc"],
    "geron corporation": ["geron"],
    "kaleido biosciences": ["kaleido"],
    "rhythm pharmaceuticals": ["rhythm", "rhythm pharmaceuticals inc"],
    "zealand pharma": ["zealand pharma a/s"],
    "bachem": ["bachem holding ag", "bachem americas"],
}

_ALIAS_LOOKUP = {alias: canonical for canonical, aliases in KNOWN_ALIASES.items() for alias in aliases}
_ALIAS_LOOKUP.update({canonical: canonical for canonical in KNOWN_ALIASES})


def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def resolve_alias(name: str) -> str:
    if not name:
        return name
    lower = name.strip().lower()
    canonical_key = _ALIAS_LOOKUP.get(lower)
    if canonical_key:
        return canonical_key.title()
    return name.strip()


# Large established pharma — used to split INDUSTRY sponsors into PHARMA vs BIOTECH
# (the ClinicalTrials.gov `class` field only says INDUSTRY). Lowercased canonical
# names (as produced by resolve_alias).
_BIG_PHARMA = {
    "novo nordisk", "eli lilly", "astrazeneca", "pfizer", "merck", "sanofi", "roche",
    "johnson & johnson", "abbvie", "amgen", "boehringer ingelheim",
    "bristol myers squibb", "novartis", "gsk", "bayer", "takeda", "daiichi sankyo",
    "servier", "otsuka", "astellas", "eisai", "teva", "viatris", "sun pharmaceutical",
    "gilead sciences", "biogen", "moderna", "regeneron", "vertex pharmaceuticals",
    "allergan", "alkermes", "supernus pharmaceuticals", "jazz pharmaceuticals",
}
# Name cues that indicate a (smaller) biotech.
_BIOTECH_CUES = ("therapeutic", "biosciences", "bioscience", "biopharma", "biologics",
                 "biotech", " bio ", "bio,", "genomics", "pharmaceuticals")


def _classify_org_type(sponsor_type: str, canonical_name: str = "") -> str:
    """Classify an org. INDUSTRY is split into PHARMA (big established pharma) vs
    BIOTECH (everyone else industry), which the UI already styles/filters but the
    feed's `class` field never distinguishes."""
    st = (sponsor_type or "").upper()
    name = (canonical_name or "").lower()
    if "INDUSTRY" in st:
        if name in _BIG_PHARMA:
            return "PHARMA"
        if any(cue in name for cue in _BIOTECH_CUES):
            return "BIOTECH"
        # Default: an industry sponsor that isn't big pharma is treated as biotech.
        return "BIOTECH"
    if st in ("NIH", "FED") or "FEDERAL" in st or "GOVERNMENT" in st:
        return "GOVERNMENT"
    if "NETWORK" in st:
        return "OTHER"
    if "UNIVERSITY" in st or "ACADEMIC" in st or "COLLEGE" in st:
        return "ACADEMIC"
    # No class from the feed — fall back to name cues so biotechs aren't all OTHER.
    if name in _BIG_PHARMA:
        return "PHARMA"
    if any(cue in name for cue in _BIOTECH_CUES):
        return "BIOTECH"
    return "OTHER"


def extract_from_trials():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, sponsor, sponsor_type, cro_named, therapeutic_area FROM trials WHERE sponsor IS NOT NULL"
    ).fetchall()

    sponsor_map = {}
    cro_map = {}

    for row in rows:
        sponsor_raw = row["sponsor"]
        if sponsor_raw:
            canonical = resolve_alias(sponsor_raw)
            slug = slugify(canonical)
            if not slug:
                continue
            if slug not in sponsor_map:
                sponsor_map[slug] = {
                    "canonical_name": canonical,
                    "sponsor_type": row["sponsor_type"] or "",
                    "aliases": set(),
                    "therapeutic_areas": set(),
                    "trial_ids": [],
                }
            sponsor_map[slug]["aliases"].add(sponsor_raw.strip())
            if row["therapeutic_area"]:
                sponsor_map[slug]["therapeutic_areas"].add(row["therapeutic_area"])
            sponsor_map[slug]["trial_ids"].append(row["id"])

        cro_raw = row["cro_named"]
        if cro_raw:
            cro_name = cro_raw.strip()
            cro_slug = slugify(cro_name)
            if not cro_slug:
                continue
            if cro_slug not in cro_map:
                cro_map[cro_slug] = {
                    "canonical_name": cro_name,
                    "therapeutic_areas": set(),
                    "trial_ids": [],
                }
            if row["therapeutic_area"]:
                cro_map[cro_slug]["therapeutic_areas"].add(row["therapeutic_area"])
            cro_map[cro_slug]["trial_ids"].append(row["id"])

    now = datetime.utcnow().isoformat()

    for slug, info in sponsor_map.items():
        org_type = _classify_org_type(info["sponsor_type"], info["canonical_name"])
        therapeutic_focus = json.dumps(sorted(info["therapeutic_areas"]))
        aliases_list = sorted(info["aliases"])

        conn.execute(
            """
            INSERT OR IGNORE INTO organizations
                (id, canonical_name, aliases, org_type, therapeutic_focus, trial_count, created_at)
            VALUES (?, ?, ?, ?, ?, 0, ?)
            """,
            (slug, info["canonical_name"], json.dumps(aliases_list), org_type, therapeutic_focus, now),
        )
        # Refresh therapeutic_focus, aliases AND org_type from current data so a
        # re-pull re-classifies (org_type used to be frozen on first insert, which
        # left BIOTECH unassigned). Analyst-editable fields are untouched.
        conn.execute(
            "UPDATE organizations SET therapeutic_focus = ?, aliases = ?, org_type = ? WHERE id = ?",
            (therapeutic_focus, json.dumps(aliases_list), org_type, slug),
        )

        for alias in info["aliases"]:
            conn.execute(
                "INSERT OR IGNORE INTO organization_aliases (alias, org_id) VALUES (?, ?)",
                (alias.lower().strip(), slug),
            )

        for trial_id in info["trial_ids"]:
            conn.execute(
                "INSERT OR IGNORE INTO trial_org_links (trial_id, org_id, role) VALUES (?, ?, 'SPONSOR')",
                (trial_id, slug),
            )

    for cro_slug, info in cro_map.items():
        therapeutic_focus = json.dumps(sorted(info["therapeutic_areas"]))

        conn.execute(
            """
            INSERT OR IGNORE INTO organizations
                (id, canonical_name, org_type, therapeutic_focus, white_label_signal, trial_count, created_at)
            VALUES (?, ?, 'CRO', ?, 'POSSIBLE', 0, ?)
            """,
            (cro_slug, info["canonical_name"], therapeutic_focus, now),
        )
        conn.execute(
            "UPDATE organizations SET therapeutic_focus = ? WHERE id = ?",
            (therapeutic_focus, cro_slug),
        )

        for trial_id in info["trial_ids"]:
            conn.execute(
                "INSERT OR IGNORE INTO trial_org_links (trial_id, org_id, role) VALUES (?, ?, 'CRO')",
                (trial_id, cro_slug),
            )

    conn.commit()
    conn.close()
    return len(sponsor_map), len(cro_map)


def recount_trial_counts():
    conn = get_connection()
    conn.execute(
        """
        UPDATE organizations SET trial_count = (
            SELECT COUNT(DISTINCT trial_id) FROM trial_org_links WHERE org_id = organizations.id
        )
        """
    )
    conn.commit()
    conn.close()


def extract_all_orgs():
    n_sponsors, n_cros = extract_from_trials()
    recount_trial_counts()
    print(f"  Organizations: {n_sponsors} sponsors, {n_cros} CROs extracted/updated")
