-- Draft tracks from docs/research/2026-11-landscape.md, their auto-sort rules,
-- and detected_on for events (the weekly dashboard shows what the pipeline
-- found this week; event_date is when it happened, which a catch-up run can
-- put weeks back).
--
-- Rules are case-insensitive Postgres regexes (~*). Use \y for a word
-- boundary: in Postgres \b means backspace. A match is a suggestion
-- (assigned_by 'rule', confirmed false); tracks stay loose.

BEGIN;

ALTER TABLE policy_events ADD COLUMN detected_on date NOT NULL DEFAULT current_date;
CREATE INDEX ON policy_events (detected_on DESC);

DROP VIEW weekly_changes;
CREATE VIEW weekly_changes AS
SELECT e.detected_on, e.event_date, e.event_type, e.summary,
       p.id AS policy_id, p.slug, p.kind, p.title, a.status AS analysis_status, a.substack_url,
       coalesce(array_agg(pt.track_slug ORDER BY pt.track_slug)
                FILTER (WHERE pt.track_slug IS NOT NULL), '{}') AS tracks
FROM policy_events e
JOIN policies p ON p.id = e.policy_id
LEFT JOIN analyses a ON a.policy_id = p.id
LEFT JOIN policy_tracks pt ON pt.policy_id = p.id
WHERE e.detected_on > current_date - 7
  AND coalesce(a.status, 'new') <> 'not_relevant'
GROUP BY e.id, p.id, a.policy_id;

INSERT INTO tracks (slug, name, summary) VALUES
  ('bvlos-airspace', 'BVLOS and airspace integration',
   'FAA Part 108 (beyond visual line of sight), other FAA drone rules, and bills on airspace access, zoning and recreational flying.'),
  ('foreign-drone-restrictions', 'Foreign drone restrictions',
   'Import, marketing and procurement limits on foreign-made drones: the FCC Covered List, Section 232 tariffs, and federal purchasing bans.'),
  ('counter-uas-authority', 'Counter-UAS authority',
   'Who may detect, track and take down drones: the SAFER SKIES Act and its rules, and bills extending authority to states, agencies and sites.'),
  ('fy2027-ndaa', 'FY2027 NDAA',
   'Drone provisions in the FY2027 National Defense Authorization Act (H.R. 8800 / S. 4784), now in conference.'),
  ('industrial-base-exports', 'Industrial base and exports',
   'Domestic drone manufacturing, DoD drone programs and strategy, and export policy for U.S. and allied drones.'),
  ('public-safety', 'Public safety use',
   'Drones used by police, fire, emergency and land-management agencies.');

INSERT INTO track_rules (track_slug, field, pattern, note) VALUES
  ('bvlos-airspace', 'title', 'beyond visual line of sight|\yBVLOS\y|part 108|integration and zoning|local innovation for flight|\yLIFT Act|recreational drone|drone safety|remote identification|\yWILD Act|limited-use drones|training corridor|command and control integration', NULL),
  ('bvlos-airspace', 'agency', '^Federal Aviation Administration$|^FAA$', 'FAA drone rules default here'),
  ('foreign-drone-restrictions', 'title', 'american security robotics|drones for america|drone espionage|covered list|foreign|iranian|transatlantic technology|countries of concern|\yDJI\y|section 232|\yDEFEND Act', NULL),
  ('foreign-drone-restrictions', 'agency', '^Federal Communications Commission$|^FCC$', 'FCC drone items are mostly Covered List'),
  ('counter-uas-authority', 'title', 'counter|\yC-UAS\y|anti-killer|drone (defense|threat|defender)|defense against drones|airspace defense|neutraliz|interdiction|guard the skies|safer skies|nefarious|\yUAS detection|defeat unmanned|kinetic capability|secure our skies|shield u|seaport security|\yUSCP\y|domestic protection mission', NULL),
  ('fy2027-ndaa', 'title', 'national defense authorization act for fiscal year 2027', NULL),
  ('industrial-base-exports', 'title', 'manufacturing|skyfoundry|\yLEAD Act|leading exports|industrial base|\ySUSTAIN Act|strategic unmanned systems partnership|blue skies for taiwan|autonomous systems strategy|drone posture review|center of excellence', NULL),
  ('public-safety', 'title', 'first responders|public safety|wildfire|emergencies|deescalation|stewardship operations', NULL);

INSERT INTO schema_migrations (version) VALUES ('003_tracks_seed');

COMMIT;
