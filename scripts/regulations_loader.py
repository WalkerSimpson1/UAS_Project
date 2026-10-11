"""Regulations.gov load into rulemakings + rulemaking_comments
(docs/mongodb_schema.md sections 3.3, 3.3a, 7, 8.5).

    python scripts/regulations_loader.py [--db NAME] [--run-date DATE]
                                         [--start DATE] [--end DATE | --docket DOCKET_ID]
                                         [--max-comment-details N]

Input: the dockets of Federal Register documents (rulemakings, source
"federal_register") that federal_register_loader saw on the run date
(last_seen) with publication_date in [start, end]. The window defaults to the
Federal Register loader's, so a weekly run follows exactly the documents that
loader just upserted. --docket loads one docket instead (testing).

Per docket: GET /v4/documents filter[docketId] -> one rulemakings document per
Regulations.gov document. Then per document GET /v4/comments
filter[commentOnId]=<attributes.objectId> (the objectId, NOT the documentId)
-> one rulemaking_comments document per comment.

The comments list only carries title, dates and agencyId. Organization,
submitter name and comment text come from GET /v4/comments/{commentId}, one
request per comment, so at most --max-comment-details are fetched per run
(newest posted first, across all dockets). The rest keep detail_fetched_at
unset and are picked up by later runs.

Every response is saved under $RAW_ROOT/regulations_gov/<run date>/ before it
is loaded. All writes are upserts keyed on _id, so re-running is safe.

Rate limit is 1000 requests/hour per key. Calls are spaced ~1s apart, a 429
backs off exponentially, and detail fetches stop early when
X-Ratelimit-Remaining runs low.

Status: "ok"; "partial" when a docket failed (e.g. 429 retries ran out) or
detail fetches stopped on the hourly budget, which is expected now and then
and is a warning for weekly_ingest; "failed" when the run itself crashed.
Exit status: 0 ok, 2 partial, 1 failed.
"""

import argparse
import json
import os
import re
import time
from datetime import date, datetime, timezone

import requests
from pymongo import DESCENDING, UpdateOne

from federal_register_loader import WINDOW_DAYS
from mongo_common import RawStore, add_counts, bulk, day, days_ago, get_db
from mongo_indexes import create_indexes

SOURCE = "regulations_gov"
API = "https://api.regulations.gov/v4"
PAGE_SIZE = 250
MAX_PAGES = 20               # the API stops at 20 pages x 250 = 5000 results
DEFAULT_MAX_DETAILS = 300
RESERVE_REQUESTS = 50        # stop detail fetches when fewer than this remain in the hour


class ApiError(Exception):
    pass


def redact(text):
    """Strip the Regulations.gov key from a message. The key only travels in
    a header, but be safe about anything that echoes requests back."""
    key = os.getenv("REGULATIONS_API_KEY")
    return str(text).replace(key, "<redacted>") if key else str(text)


def safe_name(value):
    return re.sub(r"[^\w.-]", "_", value)


class Api:
    """GET with the key in the X-Api-Key header (never the query string).
    Counts requests and 429 backoffs for the runs document."""

    def __init__(self, api_key, pause=1.0, retries=6, backoff_start=60, backoff_max=900):
        self.session = requests.Session()
        self.session.headers["X-Api-Key"] = api_key
        self.pause = pause
        self.retries = retries
        self.backoff_start = backoff_start
        self.backoff_max = backoff_max
        self.requests = 0
        self.backoffs = []          # [{path, attempt, wait_s}]
        self.remaining = None       # X-Ratelimit-Remaining from the last response

    def get(self, path, params=None):
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

    def low_on_budget(self):
        return self.remaining is not None and self.remaining < RESERVE_REQUESTS


# ---------------------------------------------------------------- documents


def document_fields(item, fr_document_number):
    """rulemakings fields from one /v4/documents list item (section 3.3).
    Dates stay the API's UTC ISO strings: a comment period ends at e.g.
    2026-10-07T03:59:59Z, which is 2026-10-06 Eastern. The list has only the
    agency acronym and no RIN, so agency_names holds the acronym and rin is null."""
    a = item["attributes"]
    docket_id = a.get("docketId")
    return {
        "source": SOURCE,
        "document_id": item["id"],
        "object_id": a.get("objectId"),
        "docket_id": docket_id,
        "docket_ids": [docket_id] if docket_id else [],
        "agency_id": a.get("agencyId"),
        "agency_names": [a["agencyId"]] if a.get("agencyId") else [],
        "title": a.get("title"),
        "document_type": a.get("documentType"),
        "publication_date": a.get("postedDate"),
        "comment_start_date": a.get("commentStartDate"),
        "comments_close_on": a.get("commentEndDate"),
        "open_for_comment": a.get("openForComment"),
        "rin": None,
        "fr_document_number": fr_document_number,
        "raw": item,
    }


def comment_list_fields(item, object_id, docket_id):
    """rulemaking_comments fields from one /v4/comments list item (3.3a). The
    list has no docketId, so it comes from the document the comment is on."""
    a = item["attributes"]
    return {
        "source": SOURCE,
        "comment_id": item["id"],
        "comment_on_id": object_id,
        "docket_id": docket_id,
        "agency_id": a.get("agencyId"),
        "title": a.get("title"),
        "posted_date": a.get("postedDate"),
        "raw": item,
    }


def comment_detail_fields(detail):
    """Fields only GET /v4/comments/{id} returns. comment can be truncated by
    the API for long submissions (the full text is in the attachments)."""
    a = detail["data"]["attributes"]
    name = " ".join(p for p in (a.get("firstName"), a.get("lastName")) if p)
    return {
        "organization": a.get("organization"),
        "submitter_name": name or None,
        "comment_text": a.get("comment"),
        "raw_detail": detail["data"],
    }


# ---------------------------------------------------------------- run


class RegulationsRun:
    def __init__(self, db, run_date, api, doc):
        self.db = db
        self.today = day(run_date)
        self.date_str = run_date.isoformat()
        self.store = RawStore(doc["run_id"])
        self.api = api
        self.doc = doc
        self.errors = []
        self.capped = []
        self.budget_stop = False
        self.counts = {"documents": {}, "comments": {}, "comment_details": {}}
        self.dockets_done = []

    def raw(self, name, payload, record_count=None):
        return self.store.write_json(SOURCE, self.date_str, name, payload, SOURCE, record_count)

    def paged(self, path, params, name_prefix, label):
        """GET every page while meta.hasNextPage, up to MAX_PAGES, saving each
        page before yielding its items."""
        for n in range(1, MAX_PAGES + 1):
            payload = self.api.get(path, {**params, "page[size]": PAGE_SIZE, "page[number]": n})
            items = payload.get("data") or []
            self.raw(f"{name_prefix}_p{n}.json", payload, len(items))
            yield from items
            meta = payload.get("meta") or {}
            if not meta.get("hasNextPage"):
                return
        total = (payload.get("meta") or {}).get("totalElements")
        self.capped.append({"what": label, "pages": MAX_PAGES, "total_elements": total})
        print(f"{label}: stopped at {MAX_PAGES} pages ({MAX_PAGES * PAGE_SIZE} of {total})")

    def upsert(self, collection, docs, key):
        ops = [UpdateOne({"_id": _id}, {
            "$set": {**fields, "last_seen": self.today},
            "$setOnInsert": {"first_seen": self.today}}, upsert=True) for _id, fields in docs]
        add_counts(self.counts[key], bulk(collection, ops))

    def docket(self, docket_id, fr_numbers):
        """Documents of one docket, then the comments on each of them."""
        items = list(self.paged("/documents", {"filter[docketId]": docket_id},
                                f"{safe_name(docket_id)}_docs", f"documents {docket_id}"))
        docs = []
        for item in items:
            fr_num = item["attributes"].get("frDocNum")
            # The FR document whose docket_id led here: the one matching this
            # document's frDocNum, or the only one if the docket came from one.
            if fr_num not in fr_numbers:
                fr_num = next(iter(fr_numbers)) if len(fr_numbers) == 1 else None
            docs.append((f"rg:{item['id']}", document_fields(item, fr_num)))
        self.upsert(self.db.rulemakings, docs, "documents")

        for item in items:
            object_id = item["attributes"].get("objectId")
            if not object_id:
                self.errors.append(f"{item['id']}: no attributes.objectId")
                continue
            comments = [
                (f"rgc:{c['id']}", comment_list_fields(c, object_id, docket_id))
                for c in self.paged("/comments", {"filter[commentOnId]": object_id,
                                                  "sort": "-lastModifiedDate"},
                                    f"{safe_name(object_id)}_comments", f"comments on {item['id']}")
            ]
            self.upsert(self.db.rulemaking_comments, comments, "comments")
        self.dockets_done.append(docket_id)

    def comment_details(self, limit):
        """Fill organization / submitter_name / comment_text for up to `limit`
        comments that don't have them yet, newest posted first."""
        pending = self.db.rulemaking_comments.find(
            {"detail_fetched_at": {"$exists": False}}, {"comment_id": 1}
        ).sort("posted_date", DESCENDING).limit(limit)
        fetched = modified = 0
        for c in pending:
            if self.api.low_on_budget():
                self.budget_stop = True
                print(f"stopping comment details: {self.api.remaining} requests left this hour")
                break
            detail = self.api.get(f"/comments/{c['comment_id']}")
            self.raw(f"{safe_name(c['comment_id'])}_detail.json", detail, 1)
            r = self.db.rulemaking_comments.update_one({"_id": c["_id"]}, {"$set": {
                **comment_detail_fields(detail), "detail_fetched_at": self.today}})
            fetched += 1
            modified += r.modified_count
        self.counts["comment_details"].update(fetched=fetched, modified=modified)

    def finish(self):
        """Fill the runs document with the summary weekly_ingest reads."""
        pending = self.db.rulemaking_comments.count_documents({"detail_fetched_at": {"$exists": False}})
        docs, comments = self.counts["documents"], self.counts["comments"]
        self.doc.update({
            "status": "partial" if self.errors or self.budget_stop else "ok",
            "raw_folder": self.store._folders.get(SOURCE),
            "requests": self.api.requests,
            "upserted": sum(docs.values()) + sum(comments.values()),
            "new": docs.get("inserted", 0) + comments.get("inserted", 0),
            "steps": {
                "dockets": {"processed": len(self.dockets_done), "docket_ids": self.dockets_done},
                "documents": docs,
                "comments": comments,
                "comment_details": {**self.counts["comment_details"], "pending": pending,
                                    "stopped_on_rate_budget": self.budget_stop},
                "requests": self.api.requests,
                "backoffs_429": {"count": len(self.api.backoffs),
                                 "wait_s": sum(b["wait_s"] for b in self.api.backoffs),
                                 "events": self.api.backoffs},
                "page_cap_hit": self.capped,
            },
            "errors": self.errors,
        })


def fr_dockets(db, last_seen=None, start=None, end=None):
    """{docket_id: {FR document_number, ...}} from the Federal Register loader's
    rulemakings documents, optionally filtered by last_seen and by
    publication_date in [start, end]."""
    query = {"source": "federal_register", "docket_ids.0": {"$exists": True}}
    if last_seen is not None:
        query["last_seen"] = last_seen
    if start and end:
        query["publication_date"] = {"$gte": start.isoformat(), "$lte": end.isoformat()}
    out = {}
    for d in db.rulemakings.find(query, {"docket_ids": 1, "document_number": 1}):
        for docket_id in filter(None, d["docket_ids"]):
            out.setdefault(docket_id, set())
            if d.get("document_number"):
                out[docket_id].add(d["document_number"])
    return out


def ingest(db, start=None, end=None, run_date=None, run_tag=None, docket=None,
           max_details=DEFAULT_MAX_DETAILS, api=None):
    """Load the dockets, then comment details. Same contract as
    federal_register_loader.ingest (weekly_ingest.RULEMAKING_LOADERS): writes a
    runs document and returns it with run_id, status, window_start,
    window_end, requests, upserted and new. A failed docket makes the run
    "partial"; anything else marks it "failed" and re-raises."""
    run_date = run_date or date.today()
    if docket:
        start = end = None
        dockets = {docket: fr_dockets(db).get(docket, set())}
    else:
        end = end or run_date
        start = start or days_ago(WINDOW_DAYS, end)
        if start > end:
            raise ValueError(f"window start {start} is after end {end}")
        dockets = fr_dockets(db, day(run_date), start, end)
    if api is None:
        key = os.getenv("REGULATIONS_API_KEY")
        if not key:
            raise RuntimeError("REGULATIONS_API_KEY is not set in .env")
        api = Api(key)
    create_indexes(db, {"rulemakings", "rulemaking_comments"})

    doc = {
        "run_id": f"{SOURCE}-{run_date}",
        "pipeline": SOURCE,
        "run_tag": run_tag,
        "started_at": datetime.now(timezone.utc),
        "status": "running",
        "window_start": start.isoformat() if start else None,
        "window_end": end.isoformat() if end else None,
        "docket": docket,
        "requests": 0,
    }
    doc["_id"] = db.runs.insert_one(doc).inserted_id
    run = RegulationsRun(db, run_date, api, doc)
    try:
        for docket_id, fr_numbers in sorted(dockets.items()):
            try:
                run.docket(docket_id, fr_numbers)
            except Exception as e:  # later dockets still run
                run.errors.append(redact(f"{docket_id}: {type(e).__name__}: {e}"))
                print(f"docket {docket_id} failed: {redact(e)}")
        try:
            run.comment_details(max_details)
        except Exception as e:
            run.errors.append(redact(f"comment_details: {type(e).__name__}: {e}"))
            print(f"comment details failed: {redact(e)}")
        run.finish()
    except Exception as e:
        doc.update({"status": "failed", "requests": api.requests,
                    "errors": run.errors + [redact(f"{type(e).__name__}: {e}")]})
        raise
    finally:
        doc["finished_at"] = datetime.now(timezone.utc)
        db.runs.update_one({"_id": doc["_id"]}, {"$set": {k: v for k, v in doc.items() if k != "_id"}})
    return doc


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    p.add_argument("--run-date", type=date.fromisoformat, default=date.today(),
                   help="FR documents last seen on this date (default: today)")
    p.add_argument("--start", type=date.fromisoformat,
                   help=f"first FR publication_date (default: {WINDOW_DAYS} days before --end)")
    p.add_argument("--end", type=date.fromisoformat, help="last FR publication_date (default: --run-date)")
    p.add_argument("--docket", help="load this one docket instead, e.g. FAA-2026-9077")
    p.add_argument("--max-comment-details", type=int, default=DEFAULT_MAX_DETAILS,
                   help=f"comment detail requests per run (default {DEFAULT_MAX_DETAILS})")
    args = p.parse_args()
    if args.docket and (args.start or args.end):
        p.error("--docket can't be combined with --start/--end")

    run = ingest(get_db(args.db), args.start, args.end, args.run_date, docket=args.docket,
                 max_details=args.max_comment_details)
    print(json.dumps({k: v for k, v in run.items() if k != "_id"}, indent=1, default=str))
    raise SystemExit({"ok": 0, "partial": 2}.get(run["status"], 1))


if __name__ == "__main__":
    main()
