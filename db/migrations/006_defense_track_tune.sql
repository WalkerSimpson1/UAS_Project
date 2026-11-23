-- Tune the defense-drones rules from 005 (docs/DECISIONS.md P22): the FCC agency rule
-- pulled in every FCC item (broadband data, spectrum, meeting notices), and "manufacturing"
-- matched a grant notice. Covered List items are now matched by title.
BEGIN;

UPDATE track_rules SET active = false
WHERE track_slug = 'defense-drones' AND field = 'agency' AND note = 'from foreign-drone-restrictions';

UPDATE track_rules
SET pattern = regexp_replace(pattern, '^manufacturing\|', 'drone manufacturing|manufacturing dominance|')
WHERE track_slug = 'defense-drones' AND note = 'from industrial-base-exports';

INSERT INTO track_rules (track_slug, field, pattern, note) VALUES
  ('defense-drones', 'title', 'covered (UAS|equipment)|national security threats to the communications supply chain|\yDJI\y', 'Covered List by title');

-- Drop unconfirmed rule assignments that no active defense rule matches any more.
DELETE FROM policy_tracks pt
WHERE pt.track_slug = 'defense-drones' AND pt.assigned_by = 'rule' AND NOT pt.confirmed
  AND NOT EXISTS (
    SELECT 1 FROM policies p JOIN track_rules r ON r.track_slug = 'defense-drones' AND r.active
    WHERE p.id = pt.policy_id
      AND ((r.field = 'title' AND (p.title ~* r.pattern
             OR EXISTS (SELECT 1 FROM policy_sources s JOIN bills b ON s.source_table = 'bills'
                        AND b.bill_id = s.source_id WHERE s.policy_id = p.id AND b.title ~* r.pattern)
             OR EXISTS (SELECT 1 FROM policy_sources s JOIN regulatory_documents d
                        ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
                        WHERE s.policy_id = p.id AND d.title ~* r.pattern)))
        OR (r.field = 'agency' AND EXISTS (SELECT 1 FROM policy_sources s JOIN regulatory_documents d
                        ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
                        WHERE s.policy_id = p.id AND EXISTS (SELECT 1 FROM unnest(d.agency_names) a
                                                             WHERE a ~* r.pattern)))));

INSERT INTO schema_migrations (version) VALUES ('006_defense_track_tune');

COMMIT;
