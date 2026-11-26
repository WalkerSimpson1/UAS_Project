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

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.json"):   # drop files from earlier layouts; metadata.json belongs to export_public_db
        if old.stem not in FILES and old.name != "metadata.json":
            old.unlink()
    for name, value in data.items():
        (OUT / f"{name}.json").write_text(json.dumps(value, indent=1, default=str), encoding="utf-8")
    return {k: len(v) if isinstance(v, list) else 1 for k, v in data.items()}


if __name__ == "__main__":
    with connect() as c:
        print(export(c))
