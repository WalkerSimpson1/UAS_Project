"""AI first drafts for the review queue, via `claude -p` on your Claude subscription (no API key).

    .venv/Scripts/python scripts/summarize.py [--limit N] [--policy SLUG] [--redo] [--model sonnet]

For each policy in an active track whose analysis is 'new' (oldest first; --all for every track), builds a fact sheet
from the database (bill text summary, actions, sponsors; or rule documents,
dates, commenters), asks Claude for a structured draft, and stores it in
analyses (status -> 'ai_drafted'). Suggested tracks go into policy_tracks as
assigned_by 'ai' (unconfirmed). Nothing a human wrote is overwritten.

Claude runs with no tools, in data/ai/ (outside the repo, so it doesn't pick
up this project's CLAUDE.md), and only sees the fact sheet. Each draft is a
starting point for your analysis, not a finished one.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys

from pg_common import REPO, connect, jsonb

WORKDIR = REPO / "data" / "ai"
DEFAULT_LIMIT = 15
TIMEOUT_S = 600

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "3-5 plain sentences: what it does, where it stands, what comes next"},
        "status_line": {"type": "string", "description": "one line for the dashboard"},
        "stakeholders": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"},
            "type": {"type": "string", "enum": ["congress", "agency", "industry", "trade_group", "advocacy",
                                                "state_local", "public_safety", "foreign_government", "other"]},
            "position": {"type": "string", "enum": ["support", "oppose", "mixed", "affected", "unknown"]},
            "evidence": {"type": "string", "description": "the fact-sheet item this rests on, or 'inferred: ...'"},
        }, "required": ["name", "type", "position", "evidence"]}},
        "firm_notes": {"type": "string", "description": "implications and concrete next steps for affected firms"},
        "suggested_tracks": {"type": "array", "items": {"type": "object", "properties": {
            "slug": {"type": "string"}, "confidence": {"type": "number"}}, "required": ["slug", "confidence"]}},
        "relevant": {"type": "boolean", "description": "substantively about drone/UAS policy?"},
        "relevance_note": {"type": "string"},
    },
    "required": ["summary", "status_line", "stakeholders", "firm_notes", "suggested_tracks", "relevant",
                 "relevance_note"],
}

KIND_GUIDE = {
    "bill": ("Stage in Congress (introduced, committee, passed a chamber, enacted), realistic prospects "
             "(standalone vs. riding on the NDAA or appropriations), and what the text would require. "
             "Stakeholders: sponsors and cosponsors from the fact sheet, plus industries the bill clearly affects."),
    "rulemaking": ("Agency, rule stage (proposed, interim final, final), effective and comment dates, and what "
                   "changes for operators and manufacturers. Stakeholders: the agency, commenters named in the "
                   "fact sheet, and industries the rule clearly affects."),
    "executive_action": "What it orders, who must act and by when, and the legal basis.",
    "litigation": "Parties, court, the question presented, and what each outcome would mean.",
    "other": ("Say plainly what this document is. If it's a routine notice (a meeting agenda, privacy notice, "
              "information collection), say so and keep the draft short."),
}

PROMPT = """You draft first-pass policy analyses for a U.S. drone (UAS) policy tracker written by a government affairs analyst. They will edit and publish your draft, so be accurate, specific and brief.

Rules:
- Use only the fact sheet below. If something isn't in it, don't state it as fact; mark inferences "inferred:".
- Plain language, no hype, no hedging boilerplate.
- firm_notes: who is affected (manufacturers, operators, counter-UAS vendors, public-safety agencies, etc.) and 2-4 concrete steps such a firm should consider (comment by a date, engage the committee, review supply chains, ...).
- suggested_tracks: pick from the track list by slug; confidence 0-1; an empty list is fine if none fits.

Focus for a {kind}: {guide}

Tracks:
{tracks}

Fact sheet:
{facts}
"""


def bill_facts(conn, pid) -> list[dict]:
    out = []
    for ident, title, desc, status_date, url, raw, role in conn.execute(
            "SELECT b.identifier, b.title, b.description, b.status_date, b.state_link, b.raw, s.role"
            " FROM policy_sources s JOIN bills b ON s.source_table = 'bills' AND b.bill_id = s.source_id"
            " WHERE s.policy_id = %s ORDER BY s.role DESC NULLS LAST, b.identifier", (pid,)):
        sponsors = [f"{s.get('name')} ({s.get('party')}-{s.get('district') or s.get('state') or ''})"
                    + (" [primary]" if s.get("sponsor_order") == 1 else "") for s in raw.get("sponsors") or []]
        out.append({
            "bill": ident, "role": role, "title": title, "description": desc, "status_date": str(status_date),
            "url": url, "committee": (raw.get("committee") or {}).get("name"),
            "sponsors": sponsors[:12], "sponsor_count": len(sponsors),
            "recent_actions": [f"{h.get('date')}: {h.get('action')}" for h in (raw.get("history") or [])[-8:]],
            "subjects": [s.get("subject_name") for s in raw.get("subjects") or []][:10],
            "texts": [f"{t.get('date')} {t.get('type')}" for t in raw.get("texts") or []],
        })
    return out


def doc_facts(conn, pid) -> dict:
    docs = [{
        "id": i, "type": t, "title": ti, "agencies": ag, "published": str(pub), "effective": str(eff) if eff else None,
        "comments_close": str(close.date()) if close else None, "docket": dk, "rin": rin,
        "abstract": (ab or "")[:1500], "url": url,
    } for i, t, ti, ag, pub, eff, close, dk, rin, ab, url in conn.execute(
        "SELECT d.id, d.document_type, d.title, d.agency_names, d.publication_date, d.effective_date,"
        " d.comments_close_at, d.docket_id, d.rin, d.abstract, d.html_url FROM policy_sources s"
        " JOIN regulatory_documents d ON s.source_table = 'regulatory_documents' AND d.id = s.source_id"
        " WHERE s.policy_id = %s ORDER BY d.publication_date", (pid,))]
    dockets = sorted({d["docket"] for d in docs if d["docket"] and d["id"].startswith("rg:")})
    comments = conn.execute(
        "SELECT count(*), array_remove(array_agg(DISTINCT organization), NULL) FROM regulatory_comments"
        " WHERE docket_id = ANY(%s)", (dockets,)).fetchone()
    return {"documents": docs, "public_comments": comments[0], "commenting_organizations": (comments[1] or [])[:25]}


def fact_sheet(conn, pid, kind, title) -> str:
    facts = {"policy": title, "kind": kind, "today": str(dt.date.today())}
    facts["bills" if kind == "bill" else "regulatory"] = bill_facts(conn, pid) if kind == "bill" else doc_facts(conn, pid)
    return json.dumps(facts, indent=1, default=str)


def ask_claude(prompt: str, model: str) -> tuple[dict, str]:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", "", "--strict-mcp-config",
           "--no-session-persistence", "--json-schema", json.dumps(SCHEMA)]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", cwd=WORKDIR,
                          timeout=TIMEOUT_S)
    try:
        out = json.loads(proc.stdout)
    except ValueError:
        raise RuntimeError(f"claude exit {proc.returncode}: {(proc.stderr or proc.stdout)[:500]}") from None
    if out.get("is_error") or not out.get("structured_output"):
        raise RuntimeError(f"claude error: {str(out.get('result'))[:500]}")
    used = next(iter(out.get("modelUsage") or {}), model)
    return out["structured_output"], used


ACTIVE_TRACK_SQL = "EXISTS (SELECT 1 FROM policy_tracks pt JOIN tracks t ON t.slug = pt.track_slug WHERE pt.policy_id = p.id AND t.status = 'active')"


def summarize(conn, limit=DEFAULT_LIMIT, slug=None, redo=False, model="sonnet", log=print,
              all_tracks=False) -> dict:
    """Drafts policies in an 'active' track (P23) unless all_tracks or a slug is given."""
    tracks = conn.execute("SELECT slug, name, summary FROM tracks WHERE status <> 'archived' ORDER BY slug").fetchall()
    track_text = "\n".join(f"- {s}: {n}. {d}" for s, n, d in tracks)
    valid = {s for s, _, _ in tracks}
    where = "p.slug = %s" if slug else ("a.status IN ('new', 'ai_drafted')" if redo else "a.status = 'new'")
    if not slug and not all_tracks:
        where += f" AND {ACTIVE_TRACK_SQL}"
    todo = conn.execute(f"SELECT p.id, p.slug, p.kind, p.title FROM policies p JOIN analyses a ON a.policy_id = p.id"
                        f" WHERE {where} ORDER BY p.created_at, p.id LIMIT %s", ([slug] if slug else []) + [limit]).fetchall()
    stats = {"drafted": 0, "failed": 0, "left": 0}
    for pid, pslug, kind, title in todo:
        prompt = PROMPT.format(kind=kind, guide=KIND_GUIDE[kind], tracks=track_text,
                               facts=fact_sheet(conn, pid, kind, title))
        try:
            d, used = ask_claude(prompt, model)
        except Exception as e:
            stats["failed"] += 1
            log(f"summarize {pslug} failed: {e}")
            continue
        conn.execute(
            "UPDATE analyses SET status = CASE WHEN status = 'new' THEN 'ai_drafted' ELSE status END,"
            " ai_summary = %s, ai_status_line = %s, ai_stakeholders = %s, ai_firm_notes = %s, ai_relevant = %s,"
            " ai_relevance_note = %s, ai_raw = %s, ai_model = %s, ai_generated_at = now(), updated_at = now()"
            " WHERE policy_id = %s",
            (d["summary"], d["status_line"], jsonb(d["stakeholders"]), d["firm_notes"], d["relevant"],
             d["relevance_note"], jsonb(d), used, pid))
        for t in d["suggested_tracks"]:
            if t["slug"] in valid:
                conn.execute("INSERT INTO policy_tracks (policy_id, track_slug, assigned_by, confidence)"
                             " VALUES (%s, %s, 'ai', %s) ON CONFLICT DO NOTHING", (pid, t["slug"], t["confidence"]))
        conn.commit()
        stats["drafted"] += 1
        log(f"drafted {pslug}: {d['status_line']}")
    stats["left"] = conn.execute("SELECT count(*) FROM analyses a JOIN policies p ON p.id = a.policy_id"
                                 f" WHERE a.status = 'new' AND {ACTIVE_TRACK_SQL}").fetchone()[0]
    return stats


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument("--policy", help="one policy slug (drafts it again even if already drafted)")
    p.add_argument("--redo", action="store_true", help="also redo 'ai_drafted' ones")
    p.add_argument("--all", action="store_true", help="include policies outside the active track(s)")
    p.add_argument("--model", default="sonnet")
    args = p.parse_args()
    with connect() as conn:
        print(json.dumps(summarize(conn, args.limit, args.policy, args.redo, args.model, all_tracks=args.all), indent=1))


if __name__ == "__main__":
    sys.exit(main())
