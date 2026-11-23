-- Source data: what the APIs return, one table per record type.
-- Replaces the MongoDB collections in docs/mongodb_schema.md (kept as history).
-- Every API row keeps its untouched payload in `raw` (jsonb) and the file it came
-- from in `raw_source` (path relative to RAW_ROOT).

BEGIN;

CREATE TABLE schema_migrations (
    version    text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- run log

CREATE TABLE runs (
    id           bigserial PRIMARY KEY,
    run_id       text NOT NULL,                 -- e.g. usaspending-2026-11-29
    pipeline     text NOT NULL,                 -- legiscan | usaspending | federal_register | regulations_gov | weekly | migration
    run_tag      text,
    parent_id    bigint REFERENCES runs (id),
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL DEFAULT 'running'
                 CHECK (status IN ('running', 'ok', 'partial', 'skipped', 'failed')),
    details      jsonb NOT NULL DEFAULT '{}',   -- per-step counts, windows, errors
    errors       text[] NOT NULL DEFAULT '{}'
);
CREATE INDEX ON runs (pipeline, started_at DESC);

-- Write-once raw files (was manifest.jsonl).
CREATE TABLE raw_files (
    path         text PRIMARY KEY,              -- relative to RAW_ROOT
    sha256       text NOT NULL,
    source       text NOT NULL,
    endpoint     text NOT NULL,
    fetched_at   timestamptz NOT NULL,
    record_count integer,
    run_id       text,
    extra        jsonb NOT NULL DEFAULT '{}'    -- e.g. LegiScan dataset_date, dataset_hash
);

-- ---------------------------------------------------------------- legislation (LegiScan)

-- Latest state of each bill.
CREATE TABLE bills (
    bill_id        text PRIMARY KEY,            -- LegiScan bill_id
    state          text NOT NULL,
    session_id     integer,
    identifier     text NOT NULL,               -- e.g. HB7525
    title          text NOT NULL,
    description    text,
    status         integer,                     -- LegiScan status code
    status_date    date,
    url            text,                        -- LegiScan page
    state_link     text,                        -- congress.gov page
    change_hash    text NOT NULL,
    raw            jsonb NOT NULL,
    raw_source     text,
    first_seen     date NOT NULL,
    last_seen      date NOT NULL,
    last_changed   date NOT NULL
);
CREATE INDEX ON bills (last_changed DESC);

-- One row per bill per weekly dataset (was legislation_history). Append-only.
CREATE TABLE bill_snapshots (
    bill_id            text NOT NULL,
    snapshot_date      date NOT NULL,           -- run date
    dataset_date       date,                    -- LegiScan dataset it came from
    change_hash        text NOT NULL,
    changed_since_prev boolean NOT NULL,
    raw                jsonb NOT NULL,
    raw_source         text,
    PRIMARY KEY (bill_id, snapshot_date)
);
CREATE INDEX ON bill_snapshots (snapshot_date DESC) WHERE changed_since_prev;

-- ---------------------------------------------------------------- rulemakings

-- Federal Register (fr:<document_number>) and Regulations.gov (rg:<documentId>)
-- documents. A rule in both sources is two rows, joined on docket_id.
CREATE TABLE regulatory_documents (
    id                 text PRIMARY KEY,        -- fr:2026-20531 | rg:FAA-2026-9077-0003
    source             text NOT NULL CHECK (source IN ('federal_register', 'regulations_gov')),
    document_number    text,                    -- FR document_number
    document_id        text,                    -- Regulations.gov documentId
    object_id          text,                    -- Regulations.gov objectId (comments filter on it)
    fr_document_number text,                    -- rg rows: the FR document that led here
    title              text,
    abstract           text,
    document_type      text,                    -- Rule | Proposed Rule | Notice | ...
    agency_names       text[] NOT NULL DEFAULT '{}',
    docket_id          text,
    docket_ids         text[] NOT NULL DEFAULT '{}',
    rin                text,
    cfr_references     jsonb NOT NULL DEFAULT '[]',
    significant        boolean,
    publication_date   date,
    posted_at          timestamptz,             -- rg: postedDate (UTC)
    effective_date     date,
    comment_start_at   timestamptz,
    comments_close_at  timestamptz,             -- FR dates are stored as midnight US/Eastern
    open_for_comment   boolean,
    html_url           text,
    pdf_url            text,
    matched_keywords   text[] NOT NULL DEFAULT '{}',
    raw                jsonb NOT NULL,
    raw_source         text,
    first_seen         date NOT NULL,
    last_seen          date NOT NULL
);
CREATE INDEX ON regulatory_documents (docket_id);
CREATE INDEX ON regulatory_documents (publication_date DESC);
CREATE INDEX ON regulatory_documents (source, last_seen DESC);

CREATE TABLE regulatory_comments (
    id                text PRIMARY KEY,         -- rgc:<commentId>
    comment_id        text NOT NULL,
    comment_on_id     text NOT NULL,            -- objectId of the commented-on document
    docket_id         text,
    agency_id         text,
    title             text,
    posted_at         timestamptz,
    organization      text,
    submitter_name    text,
    comment_text      text,
    raw               jsonb NOT NULL,
    raw_detail        jsonb,
    raw_source        text,
    detail_fetched_at date,                     -- null = details still to fetch
    first_seen        date NOT NULL,
    last_seen         date NOT NULL
);
CREATE INDEX ON regulatory_comments (docket_id);
CREATE INDEX ON regulatory_comments (comment_on_id);
CREATE INDEX ON regulatory_comments (posted_at DESC) WHERE detail_fetched_at IS NULL;

-- ---------------------------------------------------------------- contracts (USASpending, PSC 1550)

-- Awards from the live API. Transactions are the primary record; an award a
-- transaction points to but the API hasn't resolved yet is a 'pending' stub.
CREATE TABLE contract_awards (
    award_key          text PRIMARY KEY,        -- generated_internal_id, CONT_AWD_...
    award_id           text,                    -- PIID
    recipient          text,
    awarding_agency    text,
    funding_agency     text,
    naics_code         text,
    psc_code           text,
    description        text,
    amount_usd         double precision,
    award_base_date    date,
    last_modified_at   timestamptz,
    source             text NOT NULL CHECK (source IN ('award_search', 'backfill', 'bulk')),
    lookup_status      text NOT NULL DEFAULT 'ok' CHECK (lookup_status IN ('ok', 'pending')),
    backfill           jsonb,                   -- method, attempts, first/last attempt
    raw                jsonb,
    raw_source         text,
    first_seen         date NOT NULL,
    last_seen          date NOT NULL
);
CREATE INDEX ON contract_awards (last_modified_at DESC);
CREATE INDEX ON contract_awards (award_key) WHERE lookup_status = 'pending';

CREATE TABLE contract_transactions (
    transaction_id            text PRIMARY KEY, -- CONT_TX_...
    txn_key                   text NOT NULL UNIQUE,  -- transaction_id without CONT_TX_
    award_key                 text NOT NULL,
    type                      text,
    action_type               text,
    action_date               date,
    description               text,
    modification_number       text,
    federal_action_obligation double precision,
    discovered_via            text NOT NULL,    -- weekly_feed | award_modified | backfill | bulk
    raw                       jsonb,
    raw_source                text,
    first_seen                date NOT NULL,
    last_seen                 date NOT NULL
);
CREATE INDEX ON contract_transactions (award_key, action_date);
CREATE INDEX ON contract_transactions (action_date DESC);

-- spending_by_transaction feed: which awards had activity in the past 14 days.
CREATE TABLE contract_activity_feed (
    award_key               text NOT NULL,
    mod                     text NOT NULL,
    award_id                text,
    action_date             date,
    recipient_name          text,
    recipient_uei           text,
    awarding_agency         text,
    award_type              text,
    action_type             text,
    transaction_description text,
    psc_code                text,
    transaction_amount      double precision,
    late_posting            boolean NOT NULL DEFAULT false,
    raw                     jsonb,
    raw_source              text,
    first_seen              date NOT NULL,
    last_seen               date NOT NULL,
    PRIMARY KEY (award_key, mod)
);
CREATE INDEX ON contract_activity_feed (action_date DESC);

-- FY2020-2025 bulk downloads (usa_spending_old_data/). Frozen history.
-- Columns follow scripts/load_contracts_past.py; everything else stays in `row`.
CREATE TABLE past_awards (
    award_key                text PRIMARY KEY,  -- contract_award_unique_key
    award_kind               text,              -- AWD | IDV
    recipient_name           text,
    recipient_uei            text,
    awarding_agency_name     text,
    awarding_sub_agency_name text,
    total_obligated_amount   double precision,
    award_base_action_date   date,
    award_latest_action_date date,
    description              text,
    row                      jsonb NOT NULL,
    source_file              text NOT NULL
);

CREATE TABLE past_transactions (
    txn_key                   text PRIMARY KEY, -- contract_transaction_unique_key
    award_key                 text NOT NULL,
    action_date               date,
    action_date_fiscal_year   integer,
    federal_action_obligation double precision,
    recipient_name            text,
    awarding_agency_name      text,
    extent_competed           text,
    type_of_set_aside         text,
    country_of_product_or_service_origin text,
    row                       jsonb NOT NULL,
    source_file               text NOT NULL
);
CREATE INDEX ON past_transactions (action_date_fiscal_year, action_date);
CREATE INDEX ON past_transactions (award_key);

INSERT INTO schema_migrations (version) VALUES ('001_core');

COMMIT;
