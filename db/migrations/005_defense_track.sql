-- Narrow the published focus to one main track (docs/DECISIONS.md P22).
-- defense-drones merges fy2027-ndaa, foreign-drone-restrictions and industrial-base-exports,
-- adds defense-term rules, and becomes the only 'active' track. The other three stay as a
-- 'draft' watch list; the merged three are archived (kept, rules off).
BEGIN;

INSERT INTO tracks (slug, name, summary, status) VALUES
  ('defense-drones', 'Defense drones: policy and procurement',
   'How Congress and DoD shape the military drone market: the NDAA and appropriations, limits on '
   'foreign-made drones and components (Covered List, Section 232, procurement bans), DoD drone '
   'programs and the domestic industrial base, with federal contract data as the market evidence.',
   'active');

INSERT INTO track_rules (track_slug, field, pattern, note)
SELECT 'defense-drones', field, pattern, 'from ' || track_slug
FROM track_rules
WHERE track_slug IN ('fy2027-ndaa', 'foreign-drone-restrictions', 'industrial-base-exports') AND active;

INSERT INTO track_rules (track_slug, field, pattern, note) VALUES
  ('defense-drones', 'title', 'department of defense|\yDoD\y|pentagon|military|armed forces|\yarmy\y|\ynavy\y|air force|marine corps|national guard|warfighter|replicator|drone dominance|defense production|defense industrial|appropriations act|national defense', 'defense terms'),
  ('defense-drones', 'agency', '^(Defense Department|Department of Defense|Defense Acquisition Regulations System|Army Department|Navy Department|Air Force Department)$', 'DoD agencies');

UPDATE track_rules SET active = false
WHERE track_slug IN ('fy2027-ndaa', 'foreign-drone-restrictions', 'industrial-base-exports');

-- Carry over assignments: rule and human ones, and AI suggestions made with confidence >= 0.7.
INSERT INTO policy_tracks (policy_id, track_slug, assigned_by, confidence, confirmed)
SELECT DISTINCT ON (policy_id) policy_id, 'defense-drones', assigned_by, confidence, confirmed
FROM policy_tracks
WHERE track_slug IN ('fy2027-ndaa', 'foreign-drone-restrictions', 'industrial-base-exports')
  AND (assigned_by <> 'ai' OR confidence >= 0.7)
ORDER BY policy_id, (assigned_by = 'ai'), confirmed DESC
ON CONFLICT DO NOTHING;

-- Apply the new defense-term rules once to every existing policy (sync only fills untracked ones).
INSERT INTO policy_tracks (policy_id, track_slug, assigned_by)
SELECT DISTINCT p.id, 'defense-drones', 'rule'
FROM policies p
WHERE p.title ~* (SELECT pattern FROM track_rules WHERE track_slug = 'defense-drones' AND note = 'defense terms')
   OR EXISTS (SELECT 1 FROM policy_sources s JOIN bills b ON s.source_table = 'bills' AND b.bill_id = s.source_id
              WHERE s.policy_id = p.id
                AND b.title ~* (SELECT pattern FROM track_rules WHERE track_slug = 'defense-drones' AND note = 'defense terms'))
   OR EXISTS (SELECT 1 FROM policy_sources s JOIN regulatory_documents d
                ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
              WHERE s.policy_id = p.id
                AND (d.title ~* (SELECT pattern FROM track_rules WHERE track_slug = 'defense-drones' AND note = 'defense terms')
                     OR EXISTS (SELECT 1 FROM unnest(d.agency_names) a
                                WHERE a ~* (SELECT pattern FROM track_rules WHERE track_slug = 'defense-drones' AND note = 'DoD agencies'))))
ON CONFLICT DO NOTHING;

UPDATE tracks SET status = 'archived'
WHERE slug IN ('fy2027-ndaa', 'foreign-drone-restrictions', 'industrial-base-exports');

INSERT INTO schema_migrations (version) VALUES ('005_defense_track');

COMMIT;
