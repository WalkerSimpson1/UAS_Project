"""Write the public dashboard's data (site/data/*.json) from Postgres.

    .venv/Scripts/python scripts/export_site.py

site/index.html reads these files; GitHub Pages serves site/ (see
.github/workflows/pages.yml). Only facts from the sources and your published
work go out: AI drafts stay private until you publish an analysis (P15).
"""
from __future__ import annotations

import datetime as dt
import json

from pg_common import REPO, connect

OUT = REPO / "site" / "data"
COUNTER_UAS_RE = r"counter|c-uas|cuas|defeat|interceptor|jammer|detection"


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
    }
    data["weekly"] = rows(conn, """
        SELECT detected_on, event_date, event_type, summary, slug, kind, title, tracks,
               CASE WHEN analysis_status = 'published' THEN substack_url END AS analysis_url
        FROM weekly_changes w
        WHERE EXISTS (SELECT 1 FROM policies p WHERE p.slug = w.slug AND EXISTS (SELECT 1 FROM policy_tracks pt JOIN tracks t ON t.slug = pt.track_slug WHERE pt.policy_id = p.id AND t.status = 'active'))
        ORDER BY event_date DESC, slug""")
    data["tracks"] = rows(conn, """
        SELECT t.slug, t.name, t.summary, t.substack_url, count(pt.policy_id) AS policies
        FROM tracks t LEFT JOIN policy_tracks pt ON pt.track_slug = t.slug
        LEFT JOIN analyses a ON a.policy_id = pt.policy_id AND a.status = 'not_relevant'
        WHERE t.status = 'active' AND a.policy_id IS NULL
        GROUP BY t.slug ORDER BY count(pt.policy_id) DESC""")
    data["policies"] = rows(conn, """
        SELECT p.slug, p.kind, p.title,
               coalesce(array_agg(DISTINCT pt.track_slug) FILTER (WHERE pt.track_slug IS NOT NULL), '{}') AS tracks,
               CASE WHEN a.status = 'published' THEN a.substack_url END AS analysis_url,
               (SELECT coalesce(b.state_link, b.url) FROM policy_sources s JOIN bills b
                  ON s.source_table = 'bills' AND b.bill_id = s.source_id WHERE s.policy_id = p.id
                  ORDER BY s.role DESC NULLS LAST LIMIT 1) AS bill_url,
               (SELECT d.html_url FROM policy_sources s JOIN regulatory_documents d
                  ON s.source_table = 'regulatory_documents' AND d.id = s.source_id
                  WHERE s.policy_id = p.id AND d.html_url IS NOT NULL
                  ORDER BY d.publication_date DESC NULLS LAST LIMIT 1) AS document_url,
               (SELECT json_build_object('date', e.event_date, 'summary', e.summary) FROM policy_events e
                  WHERE e.policy_id = p.id ORDER BY e.event_date DESC, e.id DESC LIMIT 1) AS latest
        FROM policies p JOIN analyses a ON a.policy_id = p.id
        LEFT JOIN policy_tracks pt ON pt.policy_id = p.id
        WHERE a.status <> 'not_relevant' AND EXISTS (SELECT 1 FROM policy_tracks pt JOIN tracks t ON t.slug = pt.track_slug WHERE pt.policy_id = p.id AND t.status = 'active')
        GROUP BY p.id, a.status, a.substack_url ORDER BY p.title""")
    # Monthly obligations, PSC 1550, from the complete bulk downloads (FY2020 on; refreshed with backfill.py contracts). The weekly
    # API pull only sees transactions on awards it touches, so it isn't a full monthly total.
    data["obligations_by_month"] = rows(conn, """
        SELECT to_char(date_trunc('month', action_date), 'YYYY-MM') AS month,
               round(sum(federal_action_obligation))::bigint AS obligated
        FROM past_transactions WHERE action_date IS NOT NULL GROUP BY 1 ORDER BY 1""")
    data["recent_contract_actions"] = rows(conn, f"""
        SELECT action_date, award_id, recipient_name, awarding_agency, transaction_amount,
               transaction_description, late_posting,
               transaction_description ~* '{COUNTER_UAS_RE}' AS counter_uas,
               'https://www.usaspending.gov/award/' || award_key AS url
        FROM contract_activity_feed WHERE action_date >= current_date - 60
        ORDER BY action_date DESC, transaction_amount DESC NULLS LAST""")
    data["top_recipients_fy"] = rows(conn, """
        SELECT recipient, count(*) AS awards, round(sum(amount_usd))::bigint AS amount
        FROM contract_awards WHERE lookup_status = 'ok'
          AND award_base_date >= make_date(extract(year FROM current_date + interval '3 months')::int - 1, 10, 1)
        GROUP BY recipient ORDER BY sum(amount_usd) DESC NULLS LAST LIMIT 10""")

    OUT.mkdir(parents=True, exist_ok=True)
    for name, value in data.items():
        (OUT / f"{name}.json").write_text(json.dumps(value, indent=1, default=str), encoding="utf-8")
    return {k: len(v) if isinstance(v, list) else 1 for k, v in data.items()}


if __name__ == "__main__":
    with connect() as c:
        print(export(c))
