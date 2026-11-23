-- Counter-UAS awards found by keyword outside PSC 1550 (docs/DECISIONS.md P23).
BEGIN;

ALTER TABLE contract_awards DROP CONSTRAINT contract_awards_source_check;
ALTER TABLE contract_awards ADD CONSTRAINT contract_awards_source_check
    CHECK (source IN ('award_search', 'keyword_search', 'backfill', 'bulk'));

INSERT INTO schema_migrations (version) VALUES ('007_counter_uas_awards');

COMMIT;
