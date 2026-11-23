-- defense-drones also covers export and trade controls on drones (docs/DECISIONS.md P23):
-- BIS (Entity List, export rules), USTR (Section 301), OFAC sanctions, and DoD arms sales.
BEGIN;

INSERT INTO track_rules (track_slug, field, pattern, note) VALUES
  ('defense-drones', 'agency', '^(Industry and Security Bureau|Trade Representative, Office of United States|Foreign Assets Control Office)$',
   'trade-control agencies'),
  ('defense-drones', 'title', 'arms sales|entity list|section 301|section 232|export administration regulations',
   'trade-control titles');

INSERT INTO schema_migrations (version) VALUES ('008_defense_trade_rules');

COMMIT;
