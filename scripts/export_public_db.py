"""Write the public, query-able copy of the database: site/data/uas_public.db (SQLite)
plus site/data/metadata.json (table notes and recommended queries) for Datasette Lite.

    .venv/Scripts/python scripts/export_public_db.py

site/query.html links to https://lite.datasette.io/?url=<this db>&metadata=<metadata.json>,
which runs the queries in the visitor's browser; nothing runs on a server (P20).

Left out on purpose: AI drafts and your notes (analyses.ai_*, human_notes), raw API
payloads, and commenters' personal names (organizations are kept).
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from pg_common import REPO, connect

OUT = REPO / "site" / "data"
DB = OUT / "uas_public.db"

# (table, SELECT on Postgres, column notes). Columns come out in SELECT order.
TABLES = [
    ("tracks", "SELECT slug, name, summary, status, substack_url FROM tracks WHERE status <> 'archived'",
     "Groupings of related policies. status 'active' = the published focus; 'draft' = watch list."),
    ("levers", """SELECT l.slug, l.track_slug, l.name, l.summary, l.sort_order FROM levers l
                  JOIN tracks t ON t.slug = l.track_slug WHERE t.status <> 'archived'""",
     "Policy levers inside a track: funding, buying rules, trade controls, foreign bans, other."),
    ("themes", """SELECT th.slug, th.track_slug, th.name, th.summary, th.sort_order FROM themes th
                  JOIN tracks t ON t.slug = th.track_slug WHERE t.status <> 'archived'""",
     "Story tags on policies (China, industrial base, allies, military programs, counter-drone). A policy can have several."),
    ("policy_themes", "SELECT policy_id, theme_slug, assigned_by FROM policy_themes WHERE NOT excluded",
     "Which themes each policy carries. assigned_by: rule or human."),
    ("policies", """SELECT p.id, p.slug, p.kind, p.title, p.lever, st.stage, st.stage_rank, p.created_at::date AS tracked_since,
                           CASE WHEN a.status = 'published' THEN 'published'
                                WHEN a.status = 'not_relevant' THEN 'not_relevant' ELSE 'tracked' END AS status,
                           CASE WHEN a.status = 'published' THEN a.substack_url END AS analysis_url
                    FROM policies p JOIN analyses a ON a.policy_id = p.id
                    LEFT JOIN policy_stage st ON st.policy_id = p.id""",
     "One thing analyzed: a bill with its companions, a rulemaking with its documents, an executive action."),
    ("policy_tracks", "SELECT policy_id, track_slug, assigned_by, confirmed FROM policy_tracks",
     "Which tracks each policy is in. assigned_by: rule, ai or human; confirmed = reviewed by the author."),
    ("policy_sources", "SELECT policy_id, source_table, source_id, role FROM policy_sources",
     "Which bills or regulatory documents make up each policy."),
    ("policy_events", """SELECT policy_id, event_date, detected_on, event_type, summary, source_table, source_id
                         FROM policy_events""",
     "What changed, per policy. event_date = when it happened; detected_on = when the pipeline found it."),
    ("bills", """SELECT bill_id, identifier, title, description, status, status_date, state_link AS url,
                        raw->'committee'->>'name' AS committee, jsonb_array_length(coalesce(raw->'sponsors', '[]'))
                        AS sponsor_count, first_seen, last_changed FROM bills""",
     "Drone-related bills in the 119th Congress (LegiScan). status: 1 introduced, 2 engrossed, 3 enrolled, 4 passed."),
    ("bill_sponsors", """SELECT b.bill_id, b.identifier, s->>'name' AS name, s->>'party' AS party,
                                s->>'role' AS role, coalesce(s->>'district', '') AS district,
                                (s->>'sponsor_order')::int AS sponsor_order,
                                (s->>'sponsor_type_id')::int = 1 AS primary_sponsor
                         FROM bills b, jsonb_array_elements(b.raw->'sponsors') s""",
     "Sponsors and cosponsors. primary_sponsor = the bill's sponsor."),
    ("bill_actions", """SELECT b.bill_id, b.identifier, (h->>'date')::date AS action_date, h->>'chamber' AS chamber,
                               h->>'action' AS action
                        FROM bills b, jsonb_array_elements(b.raw->'history') h""",
     "Every recorded action on each bill."),
    ("regulatory_documents", """SELECT id, source, document_type, title, abstract, array_to_string(agency_names, '; ')
                                       AS agencies, docket_id, rin, publication_date, effective_date,
                                       comments_close_at::date AS comments_close, html_url, first_seen
                                FROM regulatory_documents""",
     "Federal Register (fr:) and Regulations.gov (rg:) documents."),
    ("regulatory_comments", """SELECT id, docket_id, title, posted_at::date AS posted, organization
                               FROM regulatory_comments""",
     "Public comments on tracked dockets (organization only; details fill in over time)."),
    ("contract_awards", """SELECT award_key, award_id, recipient, awarding_agency, funding_agency, naics_code,
                                  psc_code, source, description, amount_usd, award_base_date, last_modified_at::date AS last_modified,
                                  'https://www.usaspending.gov/award/' || award_key AS url
                           FROM contract_awards WHERE lookup_status = 'ok'""",
     "Awards from the weekly USASpending pull, current and recent fiscal years: source 'award_search' = drones"
     " (PSC 1550); 'keyword_search' = counter-UAS awards under other PSC codes (not in the 1550 totals)."),
    ("contract_actions_recent", """SELECT award_key, mod, action_date, recipient_name, awarding_agency, award_type,
                                          transaction_description, transaction_amount, late_posting
                                   FROM contract_activity_feed""",
     "Contract actions from the weekly 14-day feed (posted late = more than 7 days after the action date)."),
    ("bulk_awards", """SELECT award_key, award_kind, recipient_name, awarding_agency_name, awarding_sub_agency_name,
                              total_obligated_amount, award_base_action_date, award_latest_action_date, description
                       FROM past_awards""",
     "Complete PSC 1550 awards from USASpending bulk downloads, FY2020 on."),
    ("bulk_transactions", """SELECT txn_key, award_key, action_date, action_date_fiscal_year AS fiscal_year,
                                    federal_action_obligation, recipient_name, awarding_agency_name,
                                    row->>'awarding_sub_agency_name' AS awarding_sub_agency_name,
                                    extent_competed, type_of_set_aside, country_of_product_or_service_origin,
                                    row->>'transaction_description' AS description
                             FROM past_transactions""",
     "Every PSC 1550 contract transaction from the bulk downloads, FY2020 on. Use this for market totals."),
]

QUERIES = {
    "this_week": ("What changed in the past 7 days", """
select e.event_date, p.title, e.event_type, e.summary
from policy_events e join policies p on p.id = e.policy_id
where e.detected_on >= date('now', '-7 days') and p.status <> 'not_relevant'
order by e.event_date desc"""),
    "policies_by_track": ("Policies per track", """
select t.name, count(*) as policies, sum(p.kind = 'bill') as bills, sum(p.kind = 'rulemaking') as rulemakings
from policy_tracks pt join tracks t on t.slug = pt.track_slug join policies p on p.id = pt.policy_id
where p.status <> 'not_relevant' group by t.name order by policies desc"""),
    "policies_by_lever": ("Defense policies by lever", """
select l.name as lever, count(p.id) as policies, sum(p.kind = 'bill') as bills,
       sum(p.kind = 'rulemaking') as rulemakings,
       max((select max(event_date) from policy_events e where e.policy_id = p.id)) as last_change
from levers l left join policies p on p.lever = l.slug and p.status <> 'not_relevant'
group by l.slug order by l.sort_order"""),
    "policies_by_theme": ("Defense policies by theme (a policy can have several)", """
select th.name as theme, count(p.id) as policies,
       group_concat(distinct l.name) as levers
from themes th left join policy_themes pt on pt.theme_slug = th.slug
left join policies p on p.id = pt.policy_id and p.status <> 'not_relevant'
left join levers l on l.slug = p.lever
group by th.slug order by th.sort_order"""),
    "track_policies": ("Everything in one track, latest change first", """
select p.title, p.kind,
       (select max(event_date) from policy_events e where e.policy_id = p.id) as last_change
from policies p join policy_tracks pt on pt.policy_id = p.id
where pt.track_slug = :track and p.status <> 'not_relevant'
order by last_change desc"""),
    "bills_moving": ("Bills that moved past introduction", """
select identifier, title, case status when 2 then 'passed one chamber' when 3 then 'enrolled'
       when 4 then 'law' else status end as stage, status_date, url
from bills where status >= 2 order by status_date desc"""),
    "top_sponsors": ("Members sponsoring the most drone bills", """
select name, party, count(*) as bills, sum(primary_sponsor) as as_primary
from bill_sponsors group by name, party order by bills desc limit 25"""),
    "party_split": ("Partisan mix of each bill's sponsors", """
select identifier, sum(party = 'R') as r, sum(party = 'D') as d, count(*) as total,
       case when sum(party = 'R') > 0 and sum(party = 'D') > 0 then 'bipartisan' else 'one party' end as mix
from bill_sponsors group by identifier order by total desc"""),
    "open_comment_periods": ("Comment periods still open", """
select comments_close, agencies, title, html_url from regulatory_documents
where comments_close >= date('now') order by comments_close"""),
    "who_comments": ("Organizations commenting most across dockets", """
select organization, count(*) as comments, count(distinct docket_id) as dockets
from regulatory_comments where organization is not null and organization <> ''
group by organization order by comments desc limit 25"""),
    "obligations_by_fy": ("Drone contract obligations by fiscal year", """
select fiscal_year, round(sum(federal_action_obligation) / 1e6, 1) as obligated_millions, count(*) as transactions
from bulk_transactions group by fiscal_year order by fiscal_year"""),
    "top_agencies": ("Top buying agencies by fiscal year", """
select fiscal_year, awarding_agency_name, round(sum(federal_action_obligation) / 1e6, 1) as millions
from bulk_transactions group by fiscal_year, awarding_agency_name
having millions > 1 order by fiscal_year desc, millions desc"""),
    "top_vendors": ("Top vendors, all years", """
select recipient_name, round(sum(federal_action_obligation) / 1e6, 1) as millions,
       count(distinct award_key) as awards, min(fiscal_year) as first_fy, max(fiscal_year) as last_fy
from bulk_transactions group by recipient_name order by millions desc limit 25"""),
    "competition_trend": ("Share of dollars awarded through competition, by fiscal year", """
select fiscal_year,
       round(100.0 * sum(case when extent_competed like '%COMPET%' and extent_competed not like 'NOT %'
                              then federal_action_obligation else 0 end) / sum(federal_action_obligation), 1)
         as pct_dollars_competed
from bulk_transactions where extent_competed is not null group by fiscal_year order by fiscal_year"""),
    "counter_uas_contracts": ("Counter-drone contracts", """
select action_date, recipient_name, awarding_agency_name, round(federal_action_obligation) as amount, description
from bulk_transactions
where description like '%COUNTER-UAS%' or description like '%COUNTER UAS%' or description like '%C-UAS%'
   or description like '%CUAS%' or description like '%COUNTER-SUAS%' or description like '%COUNTER SUAS%'
   or description like '%COUNTER-DRONE%' or description like '%COUNTER DRONE%' or description like '%COUNTER%UNMANNED%'
order by action_date desc"""),
    "foreign_origin": ("Drone purchases by country of origin", """
select fiscal_year, coalesce(country_of_product_or_service_origin, 'unknown') as origin,
       round(sum(federal_action_obligation) / 1e6, 2) as millions
from bulk_transactions group by fiscal_year, origin order by fiscal_year desc, millions desc"""),
}


def export(conn) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = DB.with_suffix(".part")
    tmp.unlink(missing_ok=True)
    lite = sqlite3.connect(tmp)
    counts = {}
    for table, sql, _ in TABLES:
        cur = conn.execute(sql)
        cols = [c.name for c in cur.description]
        rows = [tuple(v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else
                      json.dumps(v) if isinstance(v, (list, dict)) else
                      float(v) if type(v).__name__ == "Decimal" else v for v in r) for r in cur.fetchall()]
        lite.execute(f"CREATE TABLE {table} ({', '.join(cols)})")
        lite.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(cols))})", rows)
        counts[table] = len(rows)
    for idx in ("policy_events(policy_id)", "policy_tracks(track_slug)", "policy_themes(theme_slug)", "bill_sponsors(identifier)",
                "bulk_transactions(fiscal_year)", "regulatory_comments(docket_id)"):
        lite.execute(f"CREATE INDEX idx_{idx.split('(')[0]}_{idx.split('(')[1][:-1]} ON {idx}")
    lite.commit()
    lite.execute("VACUUM")
    lite.close()
    tmp.replace(DB)

    meta = {
        "title": "U.S. Drone Policy Tracker: public data",
        "description": f"Exported {dt.date.today()}. Sources: LegiScan, Federal Register, Regulations.gov, "
                       "USASpending.gov. Policy groupings and tracks by the author.",
        "databases": {"uas_public": {
            "tables": {t: {"description": d} for t, _, d in TABLES},
            "queries": {k: {"title": t, "sql": q.strip()} for k, (t, q) in QUERIES.items()},
        }},
    }
    (OUT / "metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return counts


if __name__ == "__main__":
    with connect() as c:
        print(export(c), f"{DB.stat().st_size / 1e6:.1f} MB")
