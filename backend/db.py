import sqlite3
import os
import contextlib
import contextvars

DB_PATH = os.environ.get("AICURE_DB_PATH") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "aicure.db"
)
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# When the DB lives on a network filesystem (EFS/NFS in prod), WAL's shared-
# memory mmap and a large PRAGMA mmap_size are unsafe — both can hand back
# stale/torn pages even when the PRAGMA "succeeds". Setting AICURE_DB_NETWORK_FS=1
# forces the NFS-safe settings (rollback journal + mmap off) used below.
_NETWORK_FS = os.environ.get("AICURE_DB_NETWORK_FS") == "1"


# Per-request open-connection tracker, set by the API layer via
# request_connection_scope(). None outside a request, so scripts, the scheduler,
# and _init_db keep managing their own connections exactly as before.
_request_conns: contextvars.ContextVar = contextvars.ContextVar(
    "aicure_request_conns", default=None
)


@contextlib.contextmanager
def request_connection_scope():
    """Track every connection get_connection() hands out inside this block and
    close them all on exit — even if a handler raised before its own conn.close().
    close() is idempotent, so handlers that DO close on the happy path stay
    correct (the second close is a no-op). A leaked sqlite fd contributes to
    spurious 'database is locked', and per-request connections make that easy to
    hit on the error path; this closes the gap in one place instead of a
    try/finally in every endpoint."""
    token = _request_conns.set([])
    try:
        yield
    finally:
        for conn in _request_conns.get() or []:
            try:
                conn.close()
            except Exception:
                pass
        _request_conns.reset(token)


def get_connection(check_same_thread=True):
    # check_same_thread=False is needed for the streaming CSV export, whose
    # generator is iterated across anyio worker threads by Starlette; only one
    # thread touches the connection at a time, so this stays safe.
    conn = sqlite3.connect(DB_PATH, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    # mmap covers the whole DB file (331MB and growing) so reads hit the OS
    # page cache directly instead of going through a read() per page — this
    # matters because connections are opened per-request, so SQLite's own page
    # cache starts cold every time. Disabled (mmap_size=0) on a network FS,
    # where mmap coherence over NFS/EFS is unreliable. temp_store keeps the temp
    # B-trees built for non-indexed ORDER BYs (e.g. secondary grant sorts) in RAM.
    conn.execute(f"PRAGMA mmap_size={0 if _NETWORK_FS else 400000000}")
    conn.execute("PRAGMA temp_store=MEMORY")
    # Don't fail instantly on a locked DB. The streaming CSV export holds its
    # read transaction for the whole client download; with WAL (set in _init_db)
    # readers no longer block the writer, but two writers still serialize — wait
    # up to 5s for the lock instead of erroring out with "database is locked".
    conn.execute("PRAGMA busy_timeout=5000")
    # Inside an API request (the middleware opened a tracking scope), register
    # this connection so it's force-closed at request end even if the handler
    # raises before its own close(). No-op outside a request. The streaming CSV
    # export is the only caller passing check_same_thread=False; it opens its
    # connection lazily as the response body streams — partly AFTER this scope
    # would close it — so it is deliberately NOT tracked and closes itself in a
    # finally. Tracking only the default (check_same_thread=True) connections
    # keeps the two cleanly separated.
    tracked = _request_conns.get()
    if tracked is not None and check_same_thread:
        tracked.append(conn)
    return conn


def _init_db():
    conn = get_connection()
    # WAL is a persistent property of the DB file (set once, sticks across
    # connections). It lets readers and a writer run concurrently, so a long
    # streaming CSV export no longer blocks the ingest/PATCH/ANALYZE writers the
    # way rollback-journal mode did.
    #
    # BUT WAL relies on an mmap'd -shm shared-memory file, which does not work
    # correctly over NFS/EFS — the PRAGMA can report "wal" yet still hand back
    # stale/torn pages. So when the DB is on a network mount (AICURE_DB_NETWORK_FS=1)
    # we deliberately use the rollback-journal DELETE mode instead — prod is a
    # single writer, so the concurrency WAL buys us isn't needed there. Still
    # wrapped: a read-only FS rejects the PRAGMA outright.
    target_mode = "DELETE" if _NETWORK_FS else "WAL"
    try:
        mode = conn.execute(f"PRAGMA journal_mode={target_mode}").fetchone()[0]
        if str(mode).lower() != target_mode.lower():
            print(f"[db] WARNING: journal_mode not {target_mode} (got {mode})")
    except sqlite3.Error as e:
        print(f"[db] WARNING: could not set journal_mode={target_mode}: {e}")
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

        -- Seamless.AI contact-enrichment cache (§7). Seamless bills a credit per
        -- lookup INCLUDING failed/no-result lookups, so we persist every response
        -- (results AND known-empty negatives) keyed by the normalized query, and
        -- only re-call the API on a cache miss or explicit force_refresh.
        CREATE TABLE IF NOT EXISTS seamless_cache (
            cache_key             TEXT PRIMARY KEY,
            org_id                TEXT REFERENCES organizations(id),
            response_json         TEXT,
            contact_count         INTEGER DEFAULT 0,
            credits_used          INTEGER DEFAULT 0,
            fetched_at            TEXT
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
        -- and CSV export; without a matching index each request is a full-table
        -- scan + filesort that degrades silently as data accumulates.
        --
        -- Each is a composite of (sort column, tiebreak) that mirrors the exact
        -- default ORDER BY built by api._order_by_clause, so the page reads
        -- straight from the index with no temp B-tree. The descending leading
        -- column also places NULLs last natively, which is why the order-by no
        -- longer needs a `(col IS NULL)` prefix term — that prefix is an
        -- expression no plain index can satisfy and forced a full scan + sort
        -- (verified ~65x slower on trials). The drops below retire the older
        -- single-column / expression indexes these supersede.
        DROP INDEX IF EXISTS idx_grants_fit_rank;
        DROP INDEX IF EXISTS idx_trials_last_updated;
        DROP INDEX IF EXISTS idx_news_items_published_at;
        -- Grants default: ORDER BY aicure_fit DESC, ingested_at DESC.
        CREATE INDEX IF NOT EXISTS idx_grants_fit_ingested
            ON grants(aicure_fit DESC, ingested_at DESC);
        -- ingested_at as a standalone recency key (digest windows, tiebreaks).
        CREATE INDEX IF NOT EXISTS idx_grants_ingested_at
            ON grants(ingested_at DESC);
        -- Trials default: ORDER BY last_updated DESC, id (id is a TEXT pk, not
        -- the rowid, so it must be in the index to avoid a tiebreak sort).
        CREATE INDEX IF NOT EXISTS idx_trials_last_updated_id
            ON trials(last_updated DESC, id);
        -- News default: ORDER BY published_at DESC, id DESC.
        CREATE INDEX IF NOT EXISTS idx_news_items_published_at_id
            ON news_items(published_at DESC, id DESC);
        -- Correlated "latest news per trial" subqueries join on news_id; the
        -- composite PK only indexes (trial_id, news_id), so reverse lookups by
        -- news_id were unindexed.
        CREATE INDEX IF NOT EXISTS idx_trial_news_links_news_id
            ON trial_news_links(news_id);
        -- Hot trials filters/sorts that were full-scanning: status (IN filters,
        -- GROUP BY in get_stats + prune_old), aicure_fit (ORDER BY + range), and
        -- the therapeutic_area/phase GROUP BYs. Grants got tuned composites;
        -- trials only had the default-sort one.
        CREATE INDEX IF NOT EXISTS idx_trials_status ON trials(status);
        CREATE INDEX IF NOT EXISTS idx_trials_fit_id ON trials(aicure_fit DESC, id);
        CREATE INDEX IF NOT EXISTS idx_trials_therapeutic_area ON trials(therapeutic_area);
        CREATE INDEX IF NOT EXISTS idx_trials_phase ON trials(phase);
        -- (the vestigial crm_* partial index is created after the ALTER loop
        --  below, since crm_pushed_at is an ALTER-added column not present here)
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
        # Vestigial CRM hand-off columns. The push integration (crm_push.py) was
        # removed when this app was split out of the aicure monorepo; the columns
        # are kept so the persistent DB doesn't need a destructive migration. They
        # recorded the Lead id returned by the CRM and when/how we pushed it.
        "ALTER TABLE trials ADD COLUMN crm_lead_id TEXT",
        "ALTER TABLE trials ADD COLUMN crm_pushed_at TEXT",
        "ALTER TABLE trials ADD COLUMN crm_push_action TEXT",
        # Human-subjects flag for grants (§3a). Default 1 so pre-existing rows
        # aren't retroactively excluded; new ingests set it from the abstract.
        "ALTER TABLE grants ADD COLUMN human_subjects INTEGER DEFAULT 1",
        # Set to 1 by patch_org when an analyst manually edits org_type, so the
        # ingest's auto-reclassification (org_extractor.extract_from_trials) skips
        # it and the manual value (CRO / DCT_VENDOR / …) isn't reverted each run.
        "ALTER TABLE organizations ADD COLUMN org_type_locked INTEGER DEFAULT 0",
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
    # Vestigial partial index from the removed CRM hand-off (see the crm_* columns
    # above). Harmless to keep; created here (not in the CREATE block above)
    # because crm_pushed_at is an ALTER-added column that doesn't exist until the
    # loop above has run.
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trials_crm_candidates "
            "ON trials(aicure_fit DESC, id) "
            "WHERE status = 'NOT_YET_RECRUITING' AND crm_pushed_at IS NULL"
        )
        conn.commit()
    except sqlite3.Error:
        print("[db] WARNING: idx_trials_crm_candidates creation failed:")
        import traceback
        traceback.print_exc()
    conn.close()


_init_db()
