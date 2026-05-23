import json
import re
from datetime import datetime

from db import get_connection

KNOWN_ALIASES = {
    "novo nordisk": ["novo nordisk a/s", "novo nordisk inc", "novo nordisk as", "nn"],
    "eli lilly": ["eli lilly and company", "lilly", "lilly usa", "lilly research"],
    "astrazeneca": ["astrazeneca plc", "astrazeneca ab", "astrazeneca us"],
    "pfizer": ["pfizer inc", "pfizer inc.", "pfizer ltd"],
    "merck": ["merck & co", "merck sharp & dohme", "msd", "merck kgaa"],
    "sanofi": ["sanofi sa", "sanofi-aventis", "sanofi us"],
    "roche": ["f. hoffmann-la roche", "hoffmann-la roche", "genentech"],
    "johnson & johnson": ["j&j", "janssen", "janssen research"],
    "abbvie": ["abbvie inc", "abbvie inc."],
    "amgen": ["amgen inc", "amgen inc."],
    "boehringer ingelheim": ["boehringer ingelheim gmbh", "boehringer ingelheim pharma"],
    "bristol myers squibb": ["bms", "bristol-myers squibb", "bristol myers squibb company"],
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


def _classify_org_type(sponsor_type: str) -> str:
    if not sponsor_type:
        return "OTHER"
    st = sponsor_type.upper()
    if "INDUSTRY" in st:
        return "PHARMA"
    if st in ("NIH", "FED") or "FEDERAL" in st or "GOVERNMENT" in st:
        return "GOVERNMENT"
    if "NETWORK" in st:
        return "OTHER"
    if "UNIVERSITY" in st or "ACADEMIC" in st or "COLLEGE" in st:
        return "ACADEMIC"
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
        org_type = _classify_org_type(info["sponsor_type"])
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
        # Always refresh therapeutic_focus from current data (analyst-editable fields are untouched)
        conn.execute(
            "UPDATE organizations SET therapeutic_focus = ?, aliases = ? WHERE id = ?",
            (therapeutic_focus, json.dumps(aliases_list), slug),
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
