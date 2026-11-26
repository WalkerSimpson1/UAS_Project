-- Themes: story tags on policies, many per policy, alongside the one lever (docs/DECISIONS.md P27).
-- assign_themes() adds rule matches and drops rule tags that no longer match.
-- Hand edits: insert with assigned_by = 'human' to add a tag; set excluded = true (and
-- assigned_by = 'human') to keep a rule tag off. Human rows are never touched.
BEGIN;

CREATE TABLE themes (
    slug        text PRIMARY KEY,
    track_slug  text NOT NULL REFERENCES tracks (slug) ON UPDATE CASCADE ON DELETE CASCADE,
    name        text NOT NULL,
    summary     text,
    sort_order  int NOT NULL DEFAULT 100
);

CREATE TABLE theme_rules (
    id          bigserial PRIMARY KEY,
    theme_slug  text NOT NULL REFERENCES themes (slug) ON UPDATE CASCADE ON DELETE CASCADE,
    field       text NOT NULL CHECK (field IN ('title', 'agency', 'kind')),
    pattern     text NOT NULL,                 -- case-insensitive regex
    active      boolean NOT NULL DEFAULT true,
    note        text
);

CREATE TABLE policy_themes (
    policy_id   bigint NOT NULL REFERENCES policies (id) ON DELETE CASCADE,
    theme_slug  text NOT NULL REFERENCES themes (slug) ON UPDATE CASCADE ON DELETE CASCADE,
    assigned_by text NOT NULL CHECK (assigned_by IN ('rule', 'human')),
    excluded    boolean NOT NULL DEFAULT false,  -- true = a human took this tag off
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (policy_id, theme_slug)
);
CREATE INDEX ON policy_themes (theme_slug);

INSERT INTO themes (slug, track_slug, name, summary, sort_order) VALUES
  ('china', 'defense-drones', 'Cutting out China',
   'DJI, the Covered List, Chinese parts and supply chains, and other limits aimed at China.', 10),
  ('industrial-base', 'defense-drones', 'Building the U.S. industrial base',
   'Domestic manufacturing, buy-American rules, tariffs and investigations meant to grow U.S. drone makers.', 20),
  ('allies-exports', 'defense-drones', 'Arming allies and exports',
   'Arms sales, export rules and trade deals that move U.S. drones to partners.', 30),
  ('military-programs', 'defense-drones', 'Military drone programs',
   'DoD drone strategy, reviews, sustainment and units: how the services field drones.', 40),
  ('counter-drone', 'defense-drones', 'Counter-drone defense',
   'Detecting and defeating hostile drones.', 50);

INSERT INTO theme_rules (theme_slug, field, pattern, note) VALUES
  ('china', 'title', '\yDJI\y|covered (UAS|equipment|list)|\ychin(a|ese)\y|\yPRC\y|foreign adversar|countries of concern|communications supply chain|communications technology and services supply chain|espionage|foreign exploitation|security robotics|prohibition on certain|foreign-produced|entity list', 'China-related titles'),
  ('china', 'agency', '^Federal Communications Commission$', 'FCC: Covered List items'),
  ('industrial-base', 'title', 'manufacturing|dominance|domestic|drones for america|skyfoundry|buy american|blue uas|industrial base|section 232|\yimports?\y|polysilicon|acquisition regulation', 'industrial-base titles'),
  ('allies-exports', 'title', 'arms sales|export|taiwan|partnership|united arab emirates|trade and security agreement|framework|reciprocal|strategic trade', 'ally and export titles'),
  ('military-programs', 'title', 'national defense authorization act|department of defense appropriations|posture review|drone center|strategy act|\ysustain|unmanned and autonomous|military drones', 'program titles'),
  ('counter-drone', 'title', 'counter|c-uas|\ycuas\y|defeat|interceptor|jammer|detecting|detection', 'counter-drone titles');

CREATE FUNCTION assign_themes() RETURNS int LANGUAGE sql AS $$
    WITH hits AS (
        SELECT DISTINCT p.id AS policy_id, r.theme_slug
        FROM policies p
        JOIN policy_tracks pt ON pt.policy_id = p.id
        JOIN themes th ON th.track_slug = pt.track_slug
        JOIN theme_rules r ON r.theme_slug = th.slug AND r.active
        WHERE (r.field = 'kind' AND p.kind ~* r.pattern)
           OR (r.field = 'title' AND (p.title ~* r.pattern
                OR EXISTS (SELECT 1 FROM policy_sources s JOIN bills b ON s.source_table = 'bills'
                           AND b.bill_id = s.source_id WHERE s.policy_id = p.id AND b.title ~* r.pattern)
                OR EXISTS (SELECT 1 FROM policy_sources s JOIN regulatory_documents d
                           ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
                           WHERE s.policy_id = p.id AND d.title ~* r.pattern)))
           OR (r.field = 'agency' AND EXISTS (SELECT 1 FROM policy_sources s JOIN regulatory_documents d
                           ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
                           WHERE s.policy_id = p.id AND EXISTS
                             (SELECT 1 FROM unnest(d.agency_names) a WHERE a ~* r.pattern)))
    ), gone AS (
        DELETE FROM policy_themes x WHERE x.assigned_by = 'rule'
          AND NOT EXISTS (SELECT 1 FROM hits h WHERE h.policy_id = x.policy_id AND h.theme_slug = x.theme_slug)
        RETURNING 1
    ), added AS (
        INSERT INTO policy_themes (policy_id, theme_slug, assigned_by)
        SELECT policy_id, theme_slug, 'rule' FROM hits
        ON CONFLICT DO NOTHING
        RETURNING 1
    )
    SELECT ((SELECT count(*) FROM gone) + (SELECT count(*) FROM added))::int;
$$;

SELECT assign_themes();

INSERT INTO schema_migrations (version) VALUES ('010_themes');

COMMIT;
