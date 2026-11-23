-- Analysis layer: tracks -> policies -> source rows (docs/format.md).
--
-- A policy is one thing you analyze: a bill (plus its companion bills), a
-- rulemaking (its FR and Regulations.gov documents), an executive action, etc.
-- Each source row belongs to at most one policy, which is how the pipeline knows
-- whether something is already known. Tracks are loose groupings; a policy can
-- sit in several, and assignments say who made them (rule, ai or human).

BEGIN;

CREATE TABLE tracks (
    slug         text PRIMARY KEY,              -- e.g. counter-uas-authority
    name         text NOT NULL,
    summary      text,
    status       text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'active', 'archived')),
    substack_url text,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE policies (
    id           bigserial PRIMARY KEY,
    slug         text NOT NULL UNIQUE,
    kind         text NOT NULL CHECK (kind IN ('bill', 'rulemaking', 'executive_action', 'litigation', 'other')),
    title        text NOT NULL,
    created_by   text NOT NULL DEFAULT 'pipeline' CHECK (created_by IN ('pipeline', 'human')),
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Which source rows make up a policy. source_table names a table in 001_core.
CREATE TABLE policy_sources (
    source_table text NOT NULL CHECK (source_table IN ('bills', 'regulatory_documents')),
    source_id    text NOT NULL,
    policy_id    bigint NOT NULL REFERENCES policies (id) ON DELETE CASCADE,
    role         text,                          -- e.g. companion, proposed_rule, final_rule
    PRIMARY KEY (source_table, source_id)
);
CREATE INDEX ON policy_sources (policy_id);

-- Auto-sorting rules: a match puts a new policy in the track as a suggestion.
CREATE TABLE track_rules (
    id           bigserial PRIMARY KEY,
    track_slug   text NOT NULL REFERENCES tracks (slug) ON UPDATE CASCADE ON DELETE CASCADE,
    field        text NOT NULL CHECK (field IN ('title', 'docket_id', 'agency', 'rin', 'cfr')),
    pattern      text NOT NULL,                 -- case-insensitive regex
    active       boolean NOT NULL DEFAULT true,
    note         text
);

CREATE TABLE policy_tracks (
    policy_id    bigint NOT NULL REFERENCES policies (id) ON DELETE CASCADE,
    track_slug   text NOT NULL REFERENCES tracks (slug) ON UPDATE CASCADE ON DELETE CASCADE,
    assigned_by  text NOT NULL CHECK (assigned_by IN ('rule', 'ai', 'human')),
    confidence   real,                          -- ai only
    confirmed    boolean NOT NULL DEFAULT false, -- true once you've accepted it
    assigned_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_id, track_slug)
);

-- One analysis per policy: the AI draft, then your work on top of it.
CREATE TABLE analyses (
    policy_id       bigint PRIMARY KEY REFERENCES policies (id) ON DELETE CASCADE,
    status          text NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'ai_drafted', 'in_review', 'published', 'not_relevant')),
    ai_summary      text,
    ai_stakeholders jsonb,                      -- [{name, type, position, evidence}]
    ai_firm_notes   text,                       -- draft implications for affected firms
    ai_model        text,
    ai_generated_at timestamptz,
    human_notes     text,
    substack_url    text,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- What changed, per policy, per run. The weekly dashboard reads this.
CREATE TABLE policy_events (
    id           bigserial PRIMARY KEY,
    policy_id    bigint NOT NULL REFERENCES policies (id) ON DELETE CASCADE,
    event_date   date NOT NULL,
    event_type   text NOT NULL CHECK (event_type IN (
                     'new', 'status_change', 'text_change', 'new_document',
                     'comment_period', 'new_comments', 'other')),
    summary      text NOT NULL,                 -- one line for the dashboard
    source_table text,
    source_id    text,
    run_id       bigint REFERENCES runs (id),
    UNIQUE NULLS NOT DISTINCT (policy_id, event_date, event_type, source_id)
);
CREATE INDEX ON policy_events (event_date DESC);

-- Last 7 days of changes with each policy's tracks and links.
CREATE VIEW weekly_changes AS
SELECT e.event_date, e.event_type, e.summary,
       p.id AS policy_id, p.kind, p.title, a.status AS analysis_status, a.substack_url,
       coalesce(array_agg(pt.track_slug ORDER BY pt.track_slug)
                FILTER (WHERE pt.track_slug IS NOT NULL), '{}') AS tracks
FROM policy_events e
JOIN policies p ON p.id = e.policy_id
LEFT JOIN analyses a ON a.policy_id = p.id
LEFT JOIN policy_tracks pt ON pt.policy_id = p.id
WHERE e.event_date > current_date - 7
GROUP BY e.id, p.id, a.policy_id;

-- Your review queue: new or AI-drafted policies, oldest first.
CREATE VIEW review_queue AS
SELECT p.id, p.kind, p.title, a.status, a.ai_generated_at, p.created_at
FROM policies p
JOIN analyses a ON a.policy_id = p.id
WHERE a.status IN ('new', 'ai_drafted', 'in_review')
ORDER BY p.created_at;

INSERT INTO schema_migrations (version) VALUES ('002_analysis');

COMMIT;
