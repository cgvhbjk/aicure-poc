import json
import re
from datetime import datetime

from db import get_connection

# Pure name-resolution / classification helpers live in the dependency-free
# org_aliases module (so scoring/target_accounts don't pull in db). Re-exported
# here for backward compatibility with existing `from org_extractor import ...`.
from org_aliases import (  # noqa: F401 (re-exported)
    KNOWN_ALIASES, _ALIAS_LOOKUP, slugify, resolve_alias,
    _BIG_PHARMA, _BIOTECH_CUES, _classify_org_type,
)


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
        # Always refresh therapeutic_focus + aliases from current data.
        conn.execute(
            "UPDATE organizations SET therapeutic_focus = ?, aliases = ? WHERE id = ?",
            (therapeutic_focus, json.dumps(aliases_list), slug),
        )
        # Re-derive org_type ONLY when an analyst hasn't pinned it. A manual
        # org_type edit sets org_type_locked=1 (see api.patch_org), so a re-pull
        # can still backfill the auto classification (e.g. the BIOTECH split) for
        # untouched orgs without clobbering a human reclassification
        # (CRO / DCT_VENDOR / DIGITAL_HEALTH / …), which used to be silently
        # reverted on every ingest.
        conn.execute(
            "UPDATE organizations SET org_type = ? "
            "WHERE id = ? AND IFNULL(org_type_locked, 0) = 0",
            (org_type, slug),
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
