"""Turn source rows into policies, events and review-queue items (002_analysis.sql, 003_tracks_seed.sql).

    .venv/Scripts/python scripts/sync_policies.py

Runs after the loaders in weekly_run.py; safe to re-run (events are unique
per policy/date/type/source, and nothing a human set is overwritten).

  bills       one policy per bill; a bill whose title matches an existing
              policy's bill (ignoring "of 2025"-style years and punctuation)
              joins it as a companion. Events come from bill_snapshots: the
              first snapshot is 'new'; a later changed one is 'status_change'
              (new history actions), 'text_change' (new text version) or 'other'.
  documents   relevant FR documents and their Regulations.gov documents,
              grouped by docket, RIN or FR document number, one policy per
              group. Documents posted more than OLD_DOC_DAYS before we first
              saw them are context: linked, but no event. New comments (posted within
              OLD_DOC_DAYS of being seen, so backfills stay quiet) become one
              'new_comments' event per docket per day.
  analyses    every policy gets a row (status 'new') for the review queue.
  tracks      policies with no track yet get the tracks whose track_rules match.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from collections import defaultdict

from federal_register_loader import is_relevant
from pg_common import connect
from regulations_loader import docket_ids

OLD_DOC_DAYS = 30


def slugify(text: str, max_len=80) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:max_len].rstrip("-")


def title_key(title: str) -> str:
    t = re.sub(r"\bof (19|20)\d\d\b", " ", title.lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


class Sync:
    def __init__(self, conn, run_id=None):
        self.conn, self.run_id = conn, run_id
        self.stats = defaultdict(int)
        self.slugs = {s for (s,) in conn.execute("SELECT slug FROM policies")}

    # ------------------------------------------------------------ helpers

    def new_policy(self, kind: str, title: str, slug_base: str) -> int:
        slug, n = slugify(slug_base), 1
        while slug in self.slugs:
            n += 1
            slug = f"{slugify(slug_base, 76)}-{n}"
        self.slugs.add(slug)
        pid = self.conn.execute("INSERT INTO policies (slug, kind, title) VALUES (%s, %s, %s) RETURNING id",
                                (slug, kind, title)).fetchone()[0]
        self.conn.execute("INSERT INTO analyses (policy_id) VALUES (%s)", (pid,))
        self.stats["policies_created"] += 1
        return pid

    def link(self, table: str, source_id: str, pid: int, role: str | None) -> None:
        self.conn.execute("INSERT INTO policy_sources (source_table, source_id, policy_id, role)"
                          " VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", (table, source_id, pid, role))

    def event(self, pid, event_date, event_type, summary, table, source_id, detected_on, update=False) -> None:
        conflict = "DO UPDATE SET summary = EXCLUDED.summary" if update else "DO NOTHING"
        cur = self.conn.execute(
            "INSERT INTO policy_events (policy_id, event_date, event_type, summary, source_table, source_id,"
            " run_id, detected_on) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            f" ON CONFLICT (policy_id, event_date, event_type, source_id) {conflict} RETURNING (xmax = 0)",
            (pid, event_date or detected_on, event_type, summary[:500], table, source_id, self.run_id, detected_on))
        r = cur.fetchone()
        if r and r[0]:
            self.stats[f"events_{event_type}"] += 1

    # ------------------------------------------------------------ bills

    def bills(self) -> None:
        mapped = dict(self.conn.execute(
            "SELECT source_id, policy_id FROM policy_sources WHERE source_table = 'bills'").fetchall())
        by_key = {}
        for bid, title in self.conn.execute("SELECT bill_id, title FROM bills WHERE bill_id = ANY(%s)", (list(mapped),)):
            by_key.setdefault(title_key(title), mapped[bid])
        for bid, ident, title in self.conn.execute(
                "SELECT bill_id, identifier, title FROM bills ORDER BY first_seen, bill_id").fetchall():
            if bid in mapped:
                continue
            key = title_key(title)
            if key in by_key:
                pid, role = by_key[key], "companion"
            else:
                pid, role = self.new_policy("bill", title, f"{ident}-{title}"), "primary"
                by_key[key] = pid
            self.link("bills", bid, pid, role)
            mapped[bid] = pid

        snaps = defaultdict(list)
        for row in self.conn.execute("SELECT bill_id, snapshot_date, changed_since_prev, raw FROM bill_snapshots"
                                     " ORDER BY bill_id, snapshot_date"):
            snaps[row[0]].append(row[1:])
        for bid, rows in snaps.items():
            pid = mapped.get(bid)
            if pid is None:
                continue
            prev = None
            for snap_date, changed, raw in rows:
                ident = raw.get("bill_number", bid)
                history = raw.get("history") or []
                last = history[-1] if history else {}
                if prev is None:
                    self.event(pid, snap_date, "new",
                               f"{ident} now tracked. Latest action: {last.get('action', 'none')} ({last.get('date', '?')})",
                               "bills", bid, snap_date)
                elif changed:
                    old_history = prev.get("history") or []
                    new_actions = history[len(old_history):]
                    if new_actions:
                        acts = "; ".join(f"{a.get('action')} ({a.get('date')})" for a in new_actions[-3:])
                        self.event(pid, to_date(new_actions[-1].get("date")) or snap_date, "status_change",
                                   f"{ident}: {acts}", "bills", bid, snap_date)
                    elif len(raw.get("texts") or []) > len(prev.get("texts") or []):
                        t = (raw.get("texts") or [{}])[-1]
                        self.event(pid, to_date(t.get("date")) or snap_date, "text_change",
                                   f"{ident}: new text version ({t.get('type', 'text')})", "bills", bid, snap_date)
                    else:
                        self.event(pid, snap_date, "other", f"{ident}: record updated (sponsors, votes or details)",
                                   "bills", bid, snap_date)
                prev = raw

    # ------------------------------------------------------------ regulatory documents

    def documents(self) -> None:
        docs = {}
        for row in self.conn.execute(
                "SELECT id, source, document_number, fr_document_number, title, abstract, document_type,"
                " agency_names, docket_id, docket_ids, rin, publication_date, comments_close_at, first_seen, raw"
                " FROM regulatory_documents ORDER BY publication_date NULLS LAST, id"):
            d = dict(zip(("id", "source", "num", "fr_num", "title", "abstract", "type", "agencies", "docket_id",
                          "docket_ids", "rin", "pub", "close", "first_seen", "raw"), row))
            if d["source"] == "federal_register" and not is_relevant(d["title"], d["abstract"]):
                continue
            docs[d["id"]] = d
        fr_by_num = {d["num"]: i for i, d in docs.items() if d["source"] == "federal_register"}
        rg_doc_ids = {d["id"] for d in docs.values() if d["source"] == "regulations_gov"}

        # Group: shared docket, RIN, or an rg document pointing at an FR document.
        parent = {i: i for i in docs}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(a, b):
            parent[find(a)] = find(b)

        first_with = {}
        for i, d in docs.items():
            keys = [f"docket:{k}" for t in ([d["docket_id"]] + list(d["docket_ids"] or [])) if t
                    for k in docket_ids(t)]
            if d["rin"]:
                keys.append(f"rin:{d['rin']}")
            if d["fr_num"] and d["fr_num"] in fr_by_num:
                union(i, fr_by_num[d["fr_num"]])
            for k in keys:
                if k in first_with:
                    union(i, first_with[k])
                else:
                    first_with[k] = i
        groups = defaultdict(list)
        for i in docs:
            groups[find(i)].append(i)

        mapped = dict(self.conn.execute("SELECT source_id, policy_id FROM policy_sources"
                                        " WHERE source_table = 'regulatory_documents'").fetchall())
        for members in groups.values():
            members.sort(key=lambda i: (docs[i]["pub"] or dt.date.max, i))
            pid = next((mapped[i] for i in members if i in mapped), None)
            created = pid is None
            if created:
                types = {docs[i]["type"] for i in members}
                kind = ("rulemaking" if types & {"Rule", "Proposed Rule"} else
                        "executive_action" if "Presidential Document" in types else "other")
                lead = next((docs[i] for i in reversed(members) if docs[i]["source"] == "federal_register"
                             and docs[i]["type"] in ("Rule", "Proposed Rule")), None) \
                    or next((docs[i] for i in reversed(members) if docs[i]["source"] == "federal_register"), None) \
                    or docs[members[-1]]
                pid = self.new_policy(kind, lead["title"] or lead["id"], lead["title"] or lead["id"])
            first_event = True  # the earliest eventful document is the policy's "new"
            for i in members:
                d = docs[i]
                if i not in mapped:
                    self.link("regulatory_documents", i, pid, d["type"])
                    mapped[i] = pid
                # One event per real document: an rg copy of an FR document we hold adds nothing.
                if d["source"] == "regulations_gov" and (d["raw"].get("attributes") or {}).get("frDocNum") in fr_by_num:
                    continue
                if d["pub"] and (d["first_seen"] - d["pub"]).days > OLD_DOC_DAYS:
                    continue
                agency = (d["agencies"] or ["?"])[-1]
                etype = "new" if first_event else "new_document"
                first_event = False
                self.event(pid, d["pub"], etype, f"{agency}: {d['type'] or 'Document'}, {d['title']}",
                           "regulatory_documents", i, d["first_seen"])
                if d["close"] and d["pub"] and d["close"].date() > d["pub"]:
                    self.event(pid, d["pub"], "comment_period", f"Comments due {d['close'].date()}",
                               "regulatory_documents", i, d["first_seen"])

        # New comments: one event per docket per detection day.
        docket_policy = {}
        for i in rg_doc_ids:
            if docs[i]["docket_id"]:
                docket_policy.setdefault(docs[i]["docket_id"], mapped[i])
        for docket_id, seen, n in self.conn.execute(
                "SELECT docket_id, first_seen, count(*) FROM regulatory_comments"
                " WHERE posted_at IS NULL OR posted_at::date >= first_seen - %s GROUP BY 1, 2",
                (OLD_DOC_DAYS,)).fetchall():
            if docket_id in docket_policy:
                self.event(docket_policy[docket_id], seen, "new_comments",
                           f"{n} new public comment{'s' * (n != 1)} on {docket_id}", "regulatory_comments",
                           docket_id, seen, update=True)

    # ------------------------------------------------------------ tracks

    def tracks(self) -> None:
        src = ("FROM policy_sources s JOIN regulatory_documents d ON s.source_table = 'regulatory_documents'"
               " AND d.id = s.source_id WHERE s.policy_id = p.id")
        cur = self.conn.execute(f"""
            INSERT INTO policy_tracks (policy_id, track_slug, assigned_by)
            SELECT DISTINCT p.id, r.track_slug, 'rule'
            FROM policies p JOIN track_rules r ON r.active
            WHERE NOT EXISTS (SELECT 1 FROM policy_tracks x WHERE x.policy_id = p.id)
              AND ((r.field = 'title' AND (p.title ~* r.pattern
                     OR EXISTS (SELECT 1 FROM policy_sources s JOIN bills b ON s.source_table = 'bills'
                                AND b.bill_id = s.source_id WHERE s.policy_id = p.id AND b.title ~* r.pattern)
                     OR EXISTS (SELECT 1 {src} AND d.title ~* r.pattern)))
                OR (r.field = 'agency' AND EXISTS (SELECT 1 {src} AND EXISTS
                     (SELECT 1 FROM unnest(d.agency_names) a WHERE a ~* r.pattern)))
                OR (r.field = 'docket_id' AND EXISTS (SELECT 1 {src} AND d.docket_id ~* r.pattern))
                OR (r.field = 'rin' AND EXISTS (SELECT 1 {src} AND d.rin ~* r.pattern))
                OR (r.field = 'cfr' AND EXISTS (SELECT 1 {src} AND d.cfr_references::text ~* r.pattern)))
            ON CONFLICT DO NOTHING""")
        self.stats["track_suggestions"] += cur.rowcount
        self.stats["policies_without_track"] = self.conn.execute(
            "SELECT count(*) FROM policies p WHERE NOT EXISTS"
            " (SELECT 1 FROM policy_tracks t WHERE t.policy_id = p.id)").fetchone()[0]


def to_date(v):
    try:
        return dt.date.fromisoformat(str(v)[:10]) if v else None
    except ValueError:
        return None


def sync(conn, today=None, run_id=None) -> dict:
    s = Sync(conn, run_id)
    with conn.transaction():
        s.bills()
        s.documents()
        s.tracks()
    return dict(s.stats)


def main() -> None:
    with connect() as conn:
        print(json.dumps(sync(conn), indent=1))


if __name__ == "__main__":
    main()
