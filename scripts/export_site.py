"""Write the public dashboard's data (site/data/*.json) from Postgres.

    .venv/Scripts/python scripts/export_site.py

site/index.html reads these files; GitHub Pages serves site/ (see
.github/workflows/pages.yml). The site is a small weekly analytics page plus the
public database (P25); the written analysis lives on Substack. Only source facts
go out: AI drafts stay private (P15).
"""
from __future__ import annotations

import datetime as dt
import json

from pg_common import REPO, connect

OUT = REPO / "site" / "data"
FILES = ("meta", "summary", "levers", "obligations_by_fy", "top_recipients")
ACTIVE = """EXISTS (SELECT 1 FROM policy_tracks pt JOIN tracks t ON t.slug = pt.track_slug
                   WHERE pt.policy_id = p.id AND t.status = 'active')
            AND NOT EXISTS (SELECT 1 FROM analyses a WHERE a.policy_id = p.id AND a.status = 'not_relevant')"""


def rows(conn, sql, args=()):
    cur = conn.execute(sql, args)
    cols = [c.name for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def export(conn) -> dict:
    data = {}
    data["meta"] = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "last_run": rows(conn, "SELECT run_id, status, finished_at FROM runs WHERE pipeline = 'weekly'"
                               " ORDER BY started_at DESC LIMIT 1"),
        "track": rows(conn, "SELECT name, summary, substack_url FROM tracks WHERE status = 'active' LIMIT 1"),
    }
    data["summary"] = rows(conn, f"""
        SELECT (SELECT count(*) FROM policies p WHERE {ACTIVE}) AS policies,
               (SELECT count(*) FROM weekly_changes w JOIN policies p ON p.slug = w.slug WHERE {ACTIVE}) AS changes_this_week,
               (SELECT count(*) FROM contract_awards WHERE source = 'keyword_search') AS counter_uas_awards,
               (SELECT round(sum(amount_usd))::bigint FROM contract_awards WHERE source = 'keyword_search') AS counter_uas_amount
        """)[0]
    # Policy levers of the active track (P24), in display order.
    data["levers"] = rows(conn, f"""
        SELECT l.slug, l.name, l.summary, count(p.id) AS policies
        FROM levers l JOIN tracks t ON t.slug = l.track_slug AND t.status = 'active'
        LEFT JOIN policies p ON p.lever = l.slug AND {ACTIVE}
        GROUP BY l.slug ORDER BY l.sort_order""")
    # Theme tags (P27): a policy can carry several, so these counts overlap.
    data["themes"] = rows(conn, f"""
        SELECT th.slug, th.name, th.summary, count(p.id) AS policies
        FROM themes th JOIN tracks t ON t.slug = th.track_slug AND t.status = 'active'
        LEFT JOIN policy_themes pt ON pt.theme_slug = th.slug AND NOT pt.excluded
        LEFT JOIN policies p ON p.id = pt.policy_id AND {ACTIVE}
        GROUP BY th.slug ORDER BY th.sort_order""")
    # PSC 1550 obligations per fiscal year, from the complete bulk downloads (FY2020 on). The weekly
    # API tables only hold awards the pipeline touched, so they aren't a market total.
    data["obligations_by_fy"] = rows(conn, """
        SELECT action_date_fiscal_year AS fy, count(*) AS actions,
               round(sum(federal_action_obligation))::bigint AS obligated
        FROM past_transactions WHERE action_date_fiscal_year IS NOT NULL GROUP BY 1 ORDER BY 1""")
    data["top_recipients"] = rows(conn, """
        SELECT recipient_name AS recipient, count(*) AS actions,
               round(sum(federal_action_obligation))::bigint AS obligated
        FROM past_transactions
        WHERE action_date_fiscal_year = (SELECT max(action_date_fiscal_year) FROM past_transactions)
        GROUP BY 1 ORDER BY sum(federal_action_obligation) DESC NULLS LAST LIMIT 10""")

    csvs = export_csv(conn)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):   # drop files from earlier layouts; metadata.json belongs to export_public_db
        if old.stem not in FILES and old.name != "metadata.json":
            old.unlink()
    for name, value in data.items():
        (OUT / f"{name}.json").write_text(json.dumps(value, indent=1, default=str), encoding="utf-8")
    out = {k: len(v) if isinstance(v, list) else 1 for k, v in data.items()}
    out["csv"] = csvs
    return out


# ---------------------------------------------------------------- CSVs for Datawrapper (P28)
# site/data/csv/*.csv, linked in Datawrapper as external datasets so Substack embeds update
# themselves. Keep column names stable: Datawrapper's column formatting is keyed on them.
CSV_DIR = OUT / "csv"
LINK = """coalesce(
    (SELECT coalesce(b.state_link, b.url) FROM policy_sources s JOIN bills b
       ON s.source_table = 'bills' AND b.bill_id = s.source_id WHERE s.policy_id = p.id
       ORDER BY s.role DESC NULLS LAST LIMIT 1),
    (SELECT d.html_url FROM policy_sources s JOIN regulatory_documents d
       ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
       WHERE s.policy_id = p.id AND d.html_url IS NOT NULL
       ORDER BY d.publication_date DESC NULLS LAST LIMIT 1))"""
# Short names as Markdown links, which Datawrapper tables render as clickable text.
# Square brackets in a title would break the link, so they become parentheses.
def md_link(text_sql: str, url_sql: str) -> str:
    return (f"CASE WHEN ({url_sql}) IS NULL THEN {text_sql} ELSE '[' || "
            f"translate({text_sql}, '[]', '()') || '](' || ({url_sql}) || ')' END")


# Short name for narrow table columns (012_short_names.sql, P30): hand-set short_title, else rule-trimmed.
SHORT = "coalesce(p.short_title, short_name(p.title))"
# Status square from stage (P31): green = law / final rule / in effect, red = vetoed or failed, yellow = in progress.
STATUS = """CASE st.stage_rank WHEN 7 THEN '🟩' WHEN 0 THEN '🟥' ELSE '🟨' END AS "Status" """
KIND = """CASE p.kind WHEN 'bill' THEN 'Bill' WHEN 'rulemaking' THEN 'Rulemaking'
          WHEN 'executive_action' THEN 'Executive action' WHEN 'litigation' THEN 'Litigation' ELSE 'Notice' END"""
POLICY_COLS = f"""{STATUS}, {md_link(SHORT, LINK)} AS "Policy", p.title AS "Policy (longform)", {KIND} AS "Type", st.stage AS "Stage", l.name AS "Lever",
    (SELECT string_agg(th.name, '; ' ORDER BY th.sort_order) FROM policy_themes pt JOIN themes th ON th.slug = pt.theme_slug
       WHERE pt.policy_id = p.id AND NOT pt.excluded) AS "Themes",
    e.event_date AS "Last change", e.summary AS "What changed", {LINK} AS "Link" """
# Also joins policy_stage (011_policy_stage.sql, P29) as st.
LATEST = """LEFT JOIN policy_stage st ON st.policy_id = p.id
           LEFT JOIN LATERAL (SELECT event_date, summary FROM policy_events WHERE policy_id = p.id
                             ORDER BY event_date DESC, id DESC LIMIT 1) e ON true"""


def write_csv(name, conn, sql) -> int:
    import csv
    cur = conn.execute(sql)
    path = CSV_DIR / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([c.name for c in cur.description])
        n = 0
        for r in cur:
            w.writerow(["" if v is None else v for v in r])
            n += 1
    return n


def export_csv(conn) -> dict:
    CSV_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    out["weekly_changes"] = write_csv("weekly_changes", conn, f"""
        SELECT w.event_date AS "Date", {STATUS}, {md_link(SHORT, LINK)} AS "Policy", p.title AS "Policy (longform)", {KIND} AS "Type", st.stage AS "Stage", l.name AS "Lever",
               (SELECT string_agg(th.name, '; ' ORDER BY th.sort_order) FROM policy_themes pt JOIN themes th ON th.slug = pt.theme_slug
                  WHERE pt.policy_id = p.id AND NOT pt.excluded) AS "Themes",
               w.summary AS "What changed", {LINK} AS "Link"
        FROM weekly_changes w JOIN policies p ON p.slug = w.slug LEFT JOIN levers l ON l.slug = p.lever
        LEFT JOIN policy_stage st ON st.policy_id = p.id
        WHERE {ACTIVE} ORDER BY w.event_date DESC, p.title""")
    out["policies"] = write_csv("policies", conn, f"""
        SELECT {POLICY_COLS} FROM policies p LEFT JOIN levers l ON l.slug = p.lever {LATEST}
        WHERE {ACTIVE} ORDER BY st.stage_rank DESC, e.event_date DESC NULLS LAST, p.title""")
    themes = [r[0] for r in conn.execute("""SELECT th.slug FROM themes th JOIN tracks t ON t.slug = th.track_slug
                                            AND t.status = 'active' ORDER BY th.sort_order""")]
    for slug in themes:   # one file per theme, for each theme's Substack page
        out[f"theme_{slug}"] = write_csv(f"theme_{slug}", conn, f"""
            SELECT {POLICY_COLS} FROM policies p JOIN policy_themes x ON x.policy_id = p.id
                   AND x.theme_slug = '{slug}' AND NOT x.excluded
            LEFT JOIN levers l ON l.slug = p.lever {LATEST}
            WHERE {ACTIVE} ORDER BY st.stage_rank DESC, e.event_date DESC NULLS LAST, p.title""")
    out["levers_themes"] = write_csv("levers_themes", conn, f"""
        SELECT th.name AS "Theme",
               {", ".join(f'''count(*) FILTER (WHERE p.lever = '{s}') AS "{n}"''' for s, n in
                          conn.execute("SELECT l.slug, l.name FROM levers l JOIN tracks t ON t.slug = l.track_slug AND t.status = 'active' ORDER BY l.sort_order"))},
               count(*) AS "Total"
        FROM themes th JOIN policy_themes x ON x.theme_slug = th.slug AND NOT x.excluded
        JOIN policies p ON p.id = x.policy_id
        WHERE {ACTIVE} GROUP BY th.slug ORDER BY th.sort_order""")
    out["obligations_by_fy"] = write_csv("obligations_by_fy", conn, """
        SELECT 'FY' || action_date_fiscal_year AS "Fiscal year",
               round(sum(federal_action_obligation) FILTER (WHERE awarding_agency_name = 'Department of Defense'))::bigint AS "DoD",
               round(sum(federal_action_obligation) FILTER (WHERE awarding_agency_name IS DISTINCT FROM 'Department of Defense'))::bigint AS "Other agencies",
               round(sum(federal_action_obligation))::bigint AS "Total",
               count(*) AS "Contract actions"
        FROM past_transactions WHERE action_date_fiscal_year IS NOT NULL GROUP BY action_date_fiscal_year ORDER BY action_date_fiscal_year""")
    out["top_recipients"] = write_csv("top_recipients", conn, """
        WITH fy AS (SELECT max(action_date_fiscal_year) AS fy FROM past_transactions),
             t AS (SELECT recipient_name, sum(federal_action_obligation) AS amt, count(*) AS n
                   FROM past_transactions, fy WHERE action_date_fiscal_year = fy.fy GROUP BY 1)
        SELECT recipient_name AS "Recipient", round(amt)::bigint AS "Obligated",
               round((100 * amt / sum(amt) OVER ())::numeric, 1) AS "Share (%)", n AS "Contract actions",
               (SELECT 'FY' || fy FROM fy) AS "Fiscal year"
        FROM t ORDER BY amt DESC NULLS LAST LIMIT 15""")
    out["counter_drone_awards"] = write_csv("counter_drone_awards", conn, f"""
        SELECT award_base_date AS "Start date",
               {md_link("recipient", "'https://www.usaspending.gov/award/' || award_key")} AS "Recipient", awarding_agency AS "Agency",
               round(amount_usd)::bigint AS "Amount", description AS "Description",
               'https://www.usaspending.gov/award/' || award_key AS "Link"
        FROM contract_awards WHERE source = 'keyword_search' ORDER BY amount_usd DESC NULLS LAST""")
    keep = {f"{k}.csv" for k in out}
    for old in CSV_DIR.glob("*.csv"):   # drop files for themes that no longer exist
        if old.name not in keep:
            old.unlink()
    return out


if __name__ == "__main__":
    with connect() as c:
        print(export(c))
