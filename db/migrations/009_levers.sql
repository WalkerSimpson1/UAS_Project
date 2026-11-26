-- Policy levers: one lever per policy inside a track (docs/DECISIONS.md P24).
-- Rules are tried in priority order (lowest first); the first match wins, else 'other'.
-- lever_set_by = 'human' pins a lever so assign_levers() leaves it alone.
BEGIN;

CREATE TABLE levers (
    slug        text PRIMARY KEY,
    track_slug  text NOT NULL REFERENCES tracks (slug) ON UPDATE CASCADE ON DELETE CASCADE,
    name        text NOT NULL,
    summary     text,
    sort_order  int NOT NULL DEFAULT 100
);

CREATE TABLE lever_rules (
    id          bigserial PRIMARY KEY,
    lever_slug  text NOT NULL REFERENCES levers (slug) ON UPDATE CASCADE ON DELETE CASCADE,
    field       text NOT NULL CHECK (field IN ('title', 'agency', 'kind')),
    pattern     text NOT NULL,                 -- case-insensitive regex
    priority    int NOT NULL DEFAULT 100,
    active      boolean NOT NULL DEFAULT true,
    note        text
);

ALTER TABLE policies
    ADD COLUMN lever text REFERENCES levers (slug) ON UPDATE CASCADE ON DELETE SET NULL,
    ADD COLUMN lever_set_by text CHECK (lever_set_by IN ('rule', 'human'));

INSERT INTO levers (slug, track_slug, name, summary, sort_order) VALUES
  ('funding', 'defense-drones', 'Funding and mandates',
   'NDAA, defense appropriations, and bills that direct DoD drone programs, reviews or strategy.', 10),
  ('procurement', 'defense-drones', 'Buying rules',
   'How the government buys drones: FAR/DFARS, domestic sourcing, the drone dominance orders.', 20),
  ('trade', 'defense-drones', 'Trade and export controls',
   'Export rules, the Entity List, Section 232/301 tariffs, sanctions and arms sales.', 30),
  ('foreign-bans', 'defense-drones', 'Foreign drone bans',
   'The FCC Covered List, DJI and other bans on Chinese or foreign drones and parts.', 40),
  ('other', 'defense-drones', 'Other',
   'Defense-relevant items that fit none of the four levers.', 90);

INSERT INTO lever_rules (lever_slug, field, pattern, priority, note) VALUES
  -- The big money bills first: their companion bills' titles mention bans and procurement.
  ('funding', 'title', 'national defense authorization act|appropriations act', 5, 'NDAA and appropriations'),
  -- Then bans: several are BIS or FAR documents that would otherwise read as trade or buying rules.
  ('foreign-bans', 'title', 'covered (UAS|equipment|list)|national security threats to the communications supply chain|\yDJI\y|communications technology and services supply chain|espionage|foreign exploitation|security robotics|prohibition on certain|foreign-produced|countries of concern|\ychin(a|ese)\y', 10, 'ban titles'),
  ('foreign-bans', 'agency', '^Federal Communications Commission$', 15, 'FCC (Covered List)'),
  ('trade', 'title', 'export|entity list|section 301|section 232|tariff|arms sales|\yimports?\y|sanction|iranian|taiwan|polysilicon', 20, 'trade titles'),
  ('trade', 'agency', '^(Industry and Security Bureau|Trade Representative, Office of United States|Foreign Assets Control Office|International Trade Administration)$', 25, 'trade agencies'),
  ('procurement', 'title', 'acquisition regulation|\yDFARS\y|procurement|manufacturing|dominance|drones for america|skyfoundry|domestic|buy american|blue uas|partnership', 30, 'buying-rule titles'),
  ('funding', 'title', 'posture review|drone center|strategy act|\ysustain\y', 40, 'mandate titles');

CREATE FUNCTION assign_levers() RETURNS int LANGUAGE sql AS $$
    WITH pick AS (
        SELECT DISTINCT ON (p.id) p.id, coalesce(m.lever_slug, o.slug) AS lever
        FROM policies p
        JOIN policy_tracks pt ON pt.policy_id = p.id
        JOIN levers o ON o.track_slug = pt.track_slug AND o.slug = 'other'
        LEFT JOIN LATERAL (
            SELECT r.lever_slug FROM lever_rules r JOIN levers l ON l.slug = r.lever_slug
            WHERE r.active AND l.track_slug = pt.track_slug
              AND ((r.field = 'kind' AND p.kind ~* r.pattern)
                OR (r.field = 'title' AND (p.title ~* r.pattern
                     OR EXISTS (SELECT 1 FROM policy_sources s JOIN bills b ON s.source_table = 'bills'
                                AND b.bill_id = s.source_id WHERE s.policy_id = p.id AND b.title ~* r.pattern)
                     OR EXISTS (SELECT 1 FROM policy_sources s JOIN regulatory_documents d
                                ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
                                WHERE s.policy_id = p.id AND d.title ~* r.pattern)))
                OR (r.field = 'agency' AND EXISTS (SELECT 1 FROM policy_sources s JOIN regulatory_documents d
                                ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
                                WHERE s.policy_id = p.id AND EXISTS
                                  (SELECT 1 FROM unnest(d.agency_names) a WHERE a ~* r.pattern))))
            ORDER BY r.priority, r.id LIMIT 1) m ON true
        WHERE p.lever_set_by IS DISTINCT FROM 'human'
        ORDER BY p.id
    ), upd AS (
        UPDATE policies p SET lever = pick.lever, lever_set_by = 'rule'
        FROM pick WHERE p.id = pick.id AND p.lever IS DISTINCT FROM pick.lever
        RETURNING 1
    )
    SELECT count(*)::int FROM upd;
$$;

SELECT assign_levers();

INSERT INTO schema_migrations (version) VALUES ('009_levers');

COMMIT;
