import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "aicure.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
            ingested_at           TEXT
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
            ingested_at           TEXT
        );

        CREATE TABLE IF NOT EXISTS trial_news_links (
            trial_id              TEXT REFERENCES trials(id),
            news_id               INTEGER REFERENCES news_items(id),
            match_method          TEXT,
            PRIMARY KEY (trial_id, news_id)
        );
    """)
    conn.commit()
    conn.close()


_init_db()
