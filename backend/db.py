import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "aicure.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_connection(check_same_thread=True):
    # check_same_thread=False is needed for the streaming CSV export, whose
    # generator is iterated across anyio worker threads by Starlette; only one
    # thread touches the connection at a time, so this stays safe.
    conn = sqlite3.connect(DB_PATH, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    # mmap covers the whole DB file (331MB and growing) so reads hit the OS
    # page cache directly instead of going through a read() per page — this
    # matters because connections are opened per-request, so SQLite's own page
    # cache starts cold every time. temp_store keeps the temp B-trees built for
    # non-indexed ORDER BYs (e.g. secondary grant sorts) in RAM.
    conn.execute("PRAGMA mmap_size=400000000")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def _init_db():
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trials (
            id                    TEXT PRIMARY KEY,
            -- Identification
            title_brief           TEXT,
            title_official        TEXT,
            registry_id           TEXT,
            source_url            TEXT,
            raw_snapshot_path     TEXT,
            -- Status & type
            status                TEXT,
            phase                 TEXT,
            study_type            TEXT,
            -- Sponsor & operational
            sponsor               TEXT,
            sponsor_type          TEXT,
            cro_named             TEXT,
            lead_country          TEXT,
            countries             TEXT,
            num_sites             INTEGER,
            -- Study design
            randomized            TEXT,
            masking               TEXT,
            num_arms              INTEGER,
            -- Intervention & disease
            conditions            TEXT,
            interventions         TEXT,
            therapeutic_area      TEXT,
            mesh_terms            TEXT,
            -- Patient population
            enrollment            INTEGER,
            min_age               TEXT,
            max_age               TEXT,
            sex_eligibility       TEXT,
            is_pediatric          INTEGER DEFAULT 0,
            inclusion_criteria    TEXT,
            exclusion_criteria    TEXT,
            -- PI / contact
            pi_name               TEXT,
            pi_email              TEXT,
            -- Timeline
            start_date            TEXT,
            primary_completion    TEXT,
            study_completion      TEXT,
            first_posted          TEXT,
            last_updated          TEXT,
            -- Endpoints & outcomes
            primary_endpoints     TEXT,
            secondary_endpoints   TEXT,
            epro_ecoa             INTEGER DEFAULT 0,
            digital_biomarkers    INTEGER DEFAULT 0,
            dct_elements          INTEGER DEFAULT 0,
            -- Summary
            brief_summary         TEXT,
            -- Meta
            has_news              INTEGER DEFAULT 0,
            ingested_at           TEXT,
            -- EU registry cross-reference
            euct_id               TEXT,
            eudract_number        TEXT,
            registry_sources      TEXT DEFAULT '["ClinicalTrials.gov"]',
            all_registry_ids      TEXT,
            eu_member_states      TEXT,
            aicure_fit            INTEGER
        );

        CREATE TABLE IF NOT EXISTS registry_source_records (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            trial_id              TEXT REFERENCES trials(id),
            registry              TEXT,
            registry_trial_id     TEXT,
            raw_data              TEXT,
            ingested_at           TEXT,
            UNIQUE(trial_id, registry)
        );

        CREATE TABLE IF NOT EXISTS news_items (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            source                TEXT,
            title                 TEXT,
            url                   TEXT UNIQUE,
            published_at          TEXT,
            body_snippet          TEXT,
            sponsor_mentioned     TEXT,
            drug_mentioned        TEXT,
            phase_mentioned       TEXT,
            nct_ids_found         TEXT,
            trial_id              TEXT REFERENCES trials(id),
            is_trial_announcement INTEGER DEFAULT 0,
            is_trial_results      INTEGER DEFAULT 0,
            ingested_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS trial_news_links (
            trial_id              TEXT REFERENCES trials(id),
            news_id               INTEGER REFERENCES news_items(id),
            match_method          TEXT,
            PRIMARY KEY (trial_id, news_id)
        );

        CREATE TABLE IF NOT EXISTS organizations (
            id                    TEXT PRIMARY KEY,
            canonical_name        TEXT NOT NULL,
            aliases               TEXT,
            org_type              TEXT,
            therapeutic_focus     TEXT,
            regions_served        TEXT,
            offerings             TEXT,
            existing_integrations TEXT,
            white_label_signal    TEXT,
            funding_stage         TEXT,
            website               TEXT,
            linkedin_url          TEXT,
            crunchbase_url        TEXT,
            source_urls           TEXT,
            trial_count           INTEGER DEFAULT 0,
            last_verified         TEXT,
            created_at            TEXT,
            notes                 TEXT
        );

        CREATE TABLE IF NOT EXISTS organization_aliases (
            alias                 TEXT PRIMARY KEY,
            org_id                TEXT REFERENCES organizations(id)
        );

        CREATE TABLE IF NOT EXISTS trial_org_links (
            trial_id              TEXT REFERENCES trials(id),
            org_id                TEXT REFERENCES organizations(id),
            role                  TEXT,
            PRIMARY KEY (trial_id, org_id, role)
        );

        CREATE TABLE IF NOT EXISTS org_contacts (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id                TEXT REFERENCES organizations(id),
            full_name             TEXT,
            title                 TEXT,
            department            TEXT,
            email                 TEXT,
            linkedin_url          TEXT,
            source_url            TEXT,
            is_decision_maker     INTEGER DEFAULT 0,
            notes                 TEXT,
            created_at            TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_trial_org_links_org_id
            ON trial_org_links(org_id);
        CREATE INDEX IF NOT EXISTS idx_trial_org_links_trial_id
            ON trial_org_links(trial_id);

        CREATE TABLE IF NOT EXISTS uploads (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            filename          TEXT,
            entity_type       TEXT,
            row_count         INTEGER,
            matched_count     INTEGER,
            new_count         INTEGER,
            skipped_count     INTEGER,
            uploaded_at       TEXT,
            uploaded_by       TEXT,
            notes             TEXT,
            file_path         TEXT
        );

        CREATE TABLE IF NOT EXISTS merge_candidates (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type       TEXT,
            record_a_id       TEXT,
            record_b_id       TEXT,
            confidence        REAL,
            match_fields      TEXT,
            match_scores      TEXT,
            status            TEXT DEFAULT 'PENDING',
            reviewed_by       TEXT,
            reviewed_at       TEXT,
            merged_into       TEXT,
            snooze_until      TEXT,
            created_at        TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_merge_candidates_status
            ON merge_candidates(status);
        CREATE INDEX IF NOT EXISTS idx_merge_candidates_entity
            ON merge_candidates(entity_type, status);
        -- merge_detector loads existing pairs by entity_type (and de-dupes by
        -- the (a,b) tuple); this covers that lookup.
        CREATE INDEX IF NOT EXISTS idx_merge_candidates_pair
            ON merge_candidates(entity_type, record_a_id, record_b_id);

        CREATE TABLE IF NOT EXISTS grants (
            id                    TEXT PRIMARY KEY,
            source                TEXT,
            award_id              TEXT,
            title                 TEXT,
            abstract              TEXT,
            pi_name               TEXT,
            pi_email              TEXT,
            organization          TEXT,
            org_type              TEXT,
            sponsor_funder        TEXT,
            amount_usd            INTEGER,
            currency              TEXT,
            amount_original       REAL,
            start_date            TEXT,
            end_date              TEXT,
            award_date            TEXT,
            status                TEXT,
            therapeutic_area      TEXT,
            conditions            TEXT,
            interventions         TEXT,
            phase_mentioned       TEXT,
            linked_trial_id       TEXT REFERENCES trials(id),
            country               TEXT,
            source_url            TEXT,
            raw_snapshot_path     TEXT,
            ingested_at           TEXT,
            has_trial_link        INTEGER DEFAULT 0,
            aicure_fit            INTEGER
        );

        CREATE TABLE IF NOT EXISTS grant_trial_links (
            grant_id              TEXT REFERENCES grants(id),
            trial_id              TEXT REFERENCES trials(id),
            match_method          TEXT,
            PRIMARY KEY (grant_id, trial_id)
        );

        CREATE INDEX IF NOT EXISTS idx_grants_source
            ON grants(source);
        CREATE INDEX IF NOT EXISTS idx_grants_therapeutic_area
            ON grants(therapeutic_area);
        CREATE INDEX IF NOT EXISTS idx_grants_has_trial_link
            ON grants(has_trial_link);

        -- Sort/pagination indexes. The grids ORDER BY these columns on every page
        -- and CSV export; without indexes each request is a full-table scan +
        -- filesort that degrades silently as data accumulates.
        -- Grants default ranking is ORDER BY (aicure_fit IS NULL), aicure_fit
        -- DESC, ingested_at DESC (see api._grants_order_by). A plain composite
        -- index can't satisfy it because of the leading (aicure_fit IS NULL)
        -- expression (verified: SQLite falls back to a temp B-tree sort), so we
        -- index that exact expression tuple. The default sort + every page and
        -- CSV export then read straight from the index instead of full-scanning
        -- and filesorting. (Expression indexes need SQLite >= 3.9; the deploy is
        -- far newer — see the NULLS-LAST note in _grants_order_by.)
        CREATE INDEX IF NOT EXISTS idx_grants_fit_rank
            ON grants((aicure_fit IS NULL), aicure_fit DESC, ingested_at DESC);
        -- ingested_at as a standalone recency key (digest windows, tiebreaks).
        CREATE INDEX IF NOT EXISTS idx_grants_ingested_at
            ON grants(ingested_at DESC);
        -- Trials default: ORDER BY last_updated DESC (see /trials, /orgs trials).
        CREATE INDEX IF NOT EXISTS idx_trials_last_updated
            ON trials(last_updated DESC);
        -- News default: ORDER BY published_at DESC (see /news, trial news).
        CREATE INDEX IF NOT EXISTS idx_news_items_published_at
            ON news_items(published_at DESC);
        -- Correlated "latest news per trial" subqueries join on news_id; the
        -- composite PK only indexes (trial_id, news_id), so reverse lookups by
        -- news_id were unindexed.
        CREATE INDEX IF NOT EXISTS idx_trial_news_links_news_id
            ON trial_news_links(news_id);
    """)
    conn.commit()
    for alter in [
        "ALTER TABLE news_items ADD COLUMN is_trial_results INTEGER DEFAULT 0",
        "ALTER TABLE news_items ADD COLUMN event_type TEXT DEFAULT 'non_relevant'",
        "ALTER TABLE trials ADD COLUMN euct_id TEXT",
        "ALTER TABLE trials ADD COLUMN eudract_number TEXT",
        "ALTER TABLE trials ADD COLUMN registry_sources TEXT DEFAULT '[\"ClinicalTrials.gov\"]'",
        "ALTER TABLE trials ADD COLUMN all_registry_ids TEXT",
        "ALTER TABLE trials ADD COLUMN eu_member_states TEXT",
        "ALTER TABLE trials ADD COLUMN isrctn_id TEXT",
        "ALTER TABLE trials ADD COLUMN ntr_id TEXT",
        "ALTER TABLE trials ADD COLUMN anzctr_id TEXT",
        "ALTER TABLE trials ADD COLUMN drks_id TEXT",
        "ALTER TABLE trials ADD COLUMN jrct_id TEXT",
        "ALTER TABLE trials ADD COLUMN cris_id TEXT",
        "ALTER TABLE trials ADD COLUMN chictr_id TEXT",
        "ALTER TABLE trials ADD COLUMN ctri_id TEXT",
        "ALTER TABLE trials ADD COLUMN irct_id TEXT",
        "ALTER TABLE trials ADD COLUMN rebec_id TEXT",
        "ALTER TABLE trials ADD COLUMN pactr_id TEXT",
        "ALTER TABLE merge_candidates ADD COLUMN loser_snapshot TEXT",
        "ALTER TABLE grants ADD COLUMN activity_code TEXT",
        "ALTER TABLE grants ADD COLUMN agency_division TEXT",
        "ALTER TABLE grants ADD COLUMN fiscal_year INTEGER",
        "ALTER TABLE grants ADD COLUMN project_acronym TEXT",
        "ALTER TABLE grants ADD COLUMN research_type TEXT",
        # Stable "first time we saw this grant" — set on insert, preserved on
        # re-pull (see grant_utils.upsert_grant). ingested_at is re-stamped on
        # every pull (INSERT OR REPLACE), so it can't mark "new this week";
        # first_seen can. The weekly grants digest windows on it.
        "ALTER TABLE grants ADD COLUMN first_seen TEXT",
        # Precomputed AiCure opportunity score (0-100, see scoring.py). Stored so
        # the grid can ORDER BY / paginate on it server-side; (re)populated by
        # score_backfill.py after each ingest.
        "ALTER TABLE grants ADD COLUMN aicure_fit INTEGER",
        "ALTER TABLE trials ADD COLUMN aicure_fit INTEGER",
    ]:
        try:
            conn.execute(alter)
            conn.commit()
        except sqlite3.OperationalError as e:
            # The ONLY expected error on a second run is re-adding a column that
            # already exists. Anything else (disk full, corruption, locked DB)
            # must surface, not leave a half-migrated schema that fails weirdly
            # downstream.
            if "duplicate column name" not in str(e).lower():
                raise
    # Backfill first_seen for pre-existing rows (no-op once populated).
    try:
        conn.execute(
            "UPDATE grants SET first_seen = ingested_at "
            "WHERE first_seen IS NULL OR first_seen = ''"
        )
        conn.commit()
    except sqlite3.Error:
        # Non-fatal to startup (only degrades "new this week" digest accuracy),
        # but log it rather than swallowing silently.
        print("[db] WARNING: first_seen backfill failed:")
        import traceback
        traceback.print_exc()
    conn.close()


_init_db()
