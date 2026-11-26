-- How far each policy has gotten (docs/DECISIONS.md P29). Higher stage_rank = further along.
-- A policy takes the furthest stage of any of its sources (companion bills, rule documents).
--   7 Law / Final rule / In effect (executive action)   4 Reported by committee
--   6 Passed both chambers                               3 Proposed rule
--   5 Passed one chamber                                 2 Introduced
--   1 Notice or request for comment                      0 Vetoed or failed
-- LegiScan bill status: 1 introduced, 2 engrossed, 3 enrolled, 4 passed, 5 vetoed, 6 failed.
BEGIN;

CREATE VIEW policy_stage AS
WITH src AS (
    SELECT s.policy_id,
           CASE b.status WHEN 4 THEN 7 WHEN 3 THEN 6 WHEN 2 THEN 5
                WHEN 5 THEN 0 WHEN 6 THEN 0
                ELSE CASE WHEN EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(b.raw->'history', '[]')) h
                                       WHERE h->>'action' ~* 'reported|ordered to be reported') THEN 4 ELSE 2 END
           END AS r
    FROM policy_sources s JOIN bills b ON s.source_table = 'bills' AND b.bill_id = s.source_id
    UNION ALL
    SELECT s.policy_id,
           CASE d.document_type WHEN 'Rule' THEN 7 WHEN 'Proposed Rule' THEN 3 WHEN 'Presidential Document' THEN 7 ELSE 1 END
    FROM policy_sources s JOIN regulatory_documents d ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
), best AS (
    SELECT p.id AS policy_id, p.kind,
           CASE WHEN p.kind = 'executive_action' THEN 7 ELSE coalesce(max(src.r), 1) END AS stage_rank
    FROM policies p LEFT JOIN src ON src.policy_id = p.id
    GROUP BY p.id
)
SELECT policy_id, stage_rank,
       CASE stage_rank
            WHEN 7 THEN CASE kind WHEN 'bill' THEN 'Law' WHEN 'executive_action' THEN 'In effect' ELSE 'Final rule' END
            WHEN 6 THEN 'Passed both chambers'
            WHEN 5 THEN 'Passed one chamber'
            WHEN 4 THEN 'Reported by committee'
            WHEN 3 THEN 'Proposed rule'
            WHEN 2 THEN 'Introduced'
            WHEN 1 THEN 'Notice or request for comment'
            ELSE 'Vetoed or failed' END AS stage
FROM best;

INSERT INTO schema_migrations (version) VALUES ('011_policy_stage');

COMMIT;
