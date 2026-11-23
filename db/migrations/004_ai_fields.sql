-- AI draft fields used by scripts/summarize.py (claude -p on the subscription).
-- ai_relevant is a suggestion only: you set status 'not_relevant' yourself.

BEGIN;

ALTER TABLE analyses
    ADD COLUMN ai_status_line    text,      -- one line: where it stands, what's next
    ADD COLUMN ai_relevant       boolean,
    ADD COLUMN ai_relevance_note text,
    ADD COLUMN ai_raw            jsonb;     -- full structured response

INSERT INTO schema_migrations (version) VALUES ('004_ai_fields');

COMMIT;
