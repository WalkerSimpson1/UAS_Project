"""Regulations.gov load into regulatory_documents + regulatory_comments (Postgres;
MongoDB version in git history).

    .venv/Scripts/python scripts/regulations_loader.py [--run-date DATE] [--start DATE] [--end DATE]
                                                      [--docket DOCKET_ID] [--max-comment-details N]

Input: the dockets of Federal Register rows the FR loader saw on the run date
(last_seen) with publication_date in [start, end]. FR docket strings are messy
("Docket No.: FAA-2024-0025 Amdt. Nos. ..."), so only IDs shaped like
AGENCY-YYYY-NNNN are looked up. --docket loads one docket instead.

Per docket: /v4/documents filter[docketId] -> rg:<documentId> rows; per
document, /v4/comments filter[commentOnId]=<objectId> -> rgc:<commentId> rows.
Organization, submitter and text need one request per comment, so at most
--max-comment-details (newest first) are fetched per run; the rest wait
(detail_fetched_at IS NULL).

The key travels only in the X-Api-Key header. Rate limit 1000 requests/hour:
calls are ~1s apart, 429s back off, and detail fetches stop when
X-Ratelimit-Remaining runs low. Status "partial" (exit 2) when a docket failed
or the hourly budget ran out; that is a warning, not a failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import time

import requests

from federal_register_loader import WINDOW_DAYS, is_relevant
from pg_common import RawStore, connect, finish_run, jsonb, redact, start_run, to_date, to_ts, upsert

SOURCE = "regulations_gov"
API = "https://api.regulations.gov/v4"
PAGE_SIZE = 250
MAX_PAGES = 20               # the API stops at 20 x 250 = 5000 results
DEFAULT_MAX_DETAILS = 300
RESERVE_REQUESTS = 50
DOCKET_RE = re.compile(r"\b[A-Z]{2,10}(?:-[A-Z]{2,10})?-\d{4}-\d{3,6}\b")


def docket_ids(text: str) -> list[str]:
    """Regulations.gov docket IDs in an FR docket string; fixes doubled prefixes (FAA-FAA-2025-1908)."""
    return [re.sub(r"^([A-Z]+)-\1-", r"\1-", d) for d in DOCKET_RE.findall(text or "")]


class ApiError(Exception):
    pass


def safe_name(v: str) -> str:
    return re.sub(r"[^\w.-]", "_", v)


class Api:
    def __init__(self, api_key, pause=1.0, retries=6, backoff_start=60, backoff_max=900):
        self.session = requests.Session()
        self.session.headers["X-Api-Key"] = api_key
        self.pause, self.retries = pause, retries
        self.backoff_start, self.backoff_max = backoff_start, backoff_max
        self.requests, self.backoffs, self.remaining = 0, [], None

    def get(self, path, params=None) -> dict:
        for attempt in range(self.retries + 1):
            time.sleep(self.pause)
            self.requests += 1
            try:
                resp = self.session.get(f"{API}{path}", params=params, timeout=120)
            except requests.RequestException as e:
                if attempt == self.retries:
                    raise ApiError(f"GET {path}: {redact(e)}") from None
                time.sleep(2 ** attempt * 5)
                continue
            if resp.headers.get("X-Ratelimit-Remaining", "").isdigit():
                self.remaining = int(resp.headers["X-Ratelimit-Remaining"])
            if resp.status_code == 429 and attempt < self.retries:
                wait = min(self.backoff_start * 2 ** attempt, self.backoff_max)
                self.backoffs.append({"path": path, "attempt": attempt + 1, "wait_s": wait})
                print(f"429 on {path}, waiting {wait}s")
                time.sleep(wait)
                continue
            if resp.status_code >= 500 and attempt < self.retries:
                time.sleep(2 ** attempt * 5)
                continue
            if resp.status_code != 200:
                raise ApiError(f"GET {path} -> {resp.status_code}: {redact(resp.text[:500])}")
            return resp.json()
        raise ApiError(f"GET {path}: retries exhausted")

    def low_on_budget(self) -> bool:
        return self.remaining is not None and self.remaining < RESERVE_REQUESTS


def fr_dockets(conn, last_seen=None, start=None, end=None) -> dict[str, set[str]]:
    """{regulations.gov docket id: {FR document numbers}} from relevant FR rows."""
    sql = ("SELECT document_number, docket_ids, title, abstract, agency_names FROM regulatory_documents"
           " WHERE source = 'federal_register'")
    args: list = []
    if last_seen is not None:
        sql += " AND last_seen = %s"
        args.append(last_seen)
    if start and end:
        sql += " AND publication_date BETWEEN %s AND %s"
        args += [start, end]
    out: dict[str, set[str]] = {}
    for num, dockets, title, abstract, agencies in conn.execute(sql, args):
        if not is_relevant(title, abstract, agencies):
            continue
        for text in dockets or []:
            for docket_id in docket_ids(text):
                out.setdefault(docket_id, set()).add(num)
    return out


class RegulationsRun:
    def __init__(self, conn, run_date, api, run_id):
        self.conn, self.run_date, self.api = conn, run_date, api
        self.store = RawStore(run_id)
        self.errors: list[str] = []
        self.capped: list[dict] = []
        self.budget_stop = False
        self.counts = {"documents": 0, "new_documents": 0, "comments": 0, "new_comments": 0, "details": 0}
        self.dockets_done: list[str] = []

    def raw(self, name, payload, record_count=None) -> str:
        return self.store.write_json(SOURCE, self.run_date, name, payload, SOURCE, record_count)

    def paged(self, path, params, prefix, label):
        """Yields (item, raw_source) for every page up to MAX_PAGES."""
        payload = {}
        for n in range(1, MAX_PAGES + 1):
            payload = self.api.get(path, {**params, "page[size]": PAGE_SIZE, "page[number]": n})
            items = payload.get("data") or []
            rel = self.raw(f"{prefix}_p{n}.json", payload, len(items))
            for item in items:
                yield item, rel
            if not (payload.get("meta") or {}).get("hasNextPage"):
                return
        self.capped.append({"what": label, "total_elements": (payload.get("meta") or {}).get("totalElements")})

    def docket(self, docket_id: str, fr_numbers: set[str]) -> None:
        items = list(self.paged("/documents", {"filter[docketId]": docket_id},
                                f"{safe_name(docket_id)}_docs", f"documents {docket_id}"))
        for item, rel in items:
            a = item["attributes"]
            fr_num = a.get("frDocNum")
            if fr_num not in fr_numbers:
                fr_num = next(iter(fr_numbers)) if len(fr_numbers) == 1 else None
            self.counts["documents"] += 1
            self.counts["new_documents"] += upsert(self.conn, "regulatory_documents", {
                "id": f"rg:{item['id']}", "source": SOURCE, "document_id": item["id"],
                "object_id": a.get("objectId"), "fr_document_number": fr_num, "title": a.get("title"),
                "document_type": a.get("documentType"),
                "agency_names": [a["agencyId"]] if a.get("agencyId") else [],
                "docket_id": a.get("docketId"), "docket_ids": [a["docketId"]] if a.get("docketId") else [],
                "publication_date": to_date(a.get("postedDate")), "posted_at": to_ts(a.get("postedDate")),
                "comment_start_at": to_ts(a.get("commentStartDate")),
                "comments_close_at": to_ts(a.get("commentEndDate")),
                "open_for_comment": a.get("openForComment"), "raw": jsonb(item), "raw_source": rel,
                "first_seen": self.run_date, "last_seen": self.run_date,
            }, ("id",), ("first_seen",))
        self.conn.commit()

        for item, _ in items:
            object_id = item["attributes"].get("objectId")
            if not object_id:
                self.errors.append(f"{item['id']}: no attributes.objectId")
                continue
            for c, rel in self.paged("/comments", {"filter[commentOnId]": object_id, "sort": "-lastModifiedDate"},
                                     f"{safe_name(object_id)}_comments", f"comments on {item['id']}"):
                a = c["attributes"]
                self.counts["comments"] += 1
                self.counts["new_comments"] += upsert(self.conn, "regulatory_comments", {
                    "id": f"rgc:{c['id']}", "comment_id": c["id"], "comment_on_id": object_id,
                    "docket_id": docket_id, "agency_id": a.get("agencyId"), "title": a.get("title"),
                    "posted_at": to_ts(a.get("postedDate")), "raw": jsonb(c), "raw_source": rel,
                    "first_seen": self.run_date, "last_seen": self.run_date,
                }, ("id",), ("first_seen",))
            self.conn.commit()
        self.dockets_done.append(docket_id)

    def comment_details(self, limit: int) -> None:
        pending = self.conn.execute(
            "SELECT id, comment_id FROM regulatory_comments WHERE detail_fetched_at IS NULL"
            " ORDER BY posted_at DESC NULLS LAST LIMIT %s", (limit,)).fetchall()
        for rid, comment_id in pending:
            if self.api.low_on_budget():
                self.budget_stop = True
                print(f"stopping comment details: {self.api.remaining} requests left this hour")
                break
            detail = self.api.get(f"/comments/{comment_id}")
            self.raw(f"{safe_name(comment_id)}_detail.json", detail, 1)
            a = detail["data"]["attributes"]
            name = " ".join(p for p in (a.get("firstName"), a.get("lastName")) if p) or None
            self.conn.execute(
                "UPDATE regulatory_comments SET organization = %s, submitter_name = %s, comment_text = %s,"
                " raw_detail = %s, detail_fetched_at = %s WHERE id = %s",
                (a.get("organization"), name, a.get("comment"), jsonb(detail["data"]), self.run_date, rid))
            self.conn.commit()
            self.counts["details"] += 1


def ingest(conn, start=None, end=None, run_date=None, run_tag=None, parent_id=None, docket=None,
           max_details=DEFAULT_MAX_DETAILS, api=None) -> dict:
    run_date = run_date or dt.date.today()
    if docket:
        start = end = None
        dockets = {docket: fr_dockets(conn).get(docket, set())}
    else:
        end = end or run_date
        start = start or end - dt.timedelta(days=WINDOW_DAYS)
        dockets = fr_dockets(conn, run_date, start, end)
    if api is None:
        key = os.environ.get("REGULATIONS_API_KEY")
        if not key:
            raise RuntimeError("REGULATIONS_API_KEY is not set in .env")
        api = Api(key)

    run_id = f"{SOURCE}-{run_date}"
    rid = start_run(conn, SOURCE, run_id, run_tag, parent_id)
    run = RegulationsRun(conn, run_date, api, run_id)
    try:
        for docket_id, fr_numbers in sorted(dockets.items()):
            try:
                run.docket(docket_id, fr_numbers)
            except Exception as e:  # later dockets still run
                conn.rollback()
                run.errors.append(f"{docket_id}: {type(e).__name__}: {e}")
                print(f"docket {docket_id} failed: {redact(e)}")
        try:
            run.comment_details(max_details)
        except Exception as e:
            conn.rollback()
            run.errors.append(f"comment_details: {type(e).__name__}: {e}")
    except Exception as e:
        conn.rollback()
        finish_run(conn, rid, "failed", {"requests": api.requests}, run.errors + [f"{type(e).__name__}: {e}"])
        raise
    pending = conn.execute("SELECT count(*) FROM regulatory_comments WHERE detail_fetched_at IS NULL").fetchone()[0]
    status = "partial" if run.errors or run.budget_stop else "ok"
    details = {
        "window_start": str(start) if start else None, "window_end": str(end) if end else None,
        "docket": docket, "requests": api.requests, "raw_folder": run.store.folders.get(SOURCE),
        "new": run.counts["new_documents"] + run.counts["new_comments"],
        "steps": {"dockets": run.dockets_done, **run.counts, "details_pending": pending,
                  "stopped_on_rate_budget": run.budget_stop, "page_cap_hit": run.capped,
                  "backoffs_429": len(api.backoffs)},
    }
    finish_run(conn, rid, status, details, run.errors)
    return {"run_id": run_id, "status": status, **details}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-date", type=dt.date.fromisoformat, default=dt.date.today())
    p.add_argument("--start", type=dt.date.fromisoformat)
    p.add_argument("--end", type=dt.date.fromisoformat)
    p.add_argument("--docket", help="load this one docket, e.g. FAA-2024-0025")
    p.add_argument("--max-comment-details", type=int, default=DEFAULT_MAX_DETAILS)
    args = p.parse_args()
    if args.docket and (args.start or args.end):
        p.error("--docket can't be combined with --start/--end")
    with connect() as conn:
        run = ingest(conn, args.start, args.end, args.run_date, docket=args.docket,
                     max_details=args.max_comment_details)
    print(json.dumps(run, indent=1, default=str))
    raise SystemExit({"ok": 0, "partial": 2}.get(run["status"], 1))


if __name__ == "__main__":
    main()
