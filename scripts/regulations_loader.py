"""Regulations.gov load into rulemakings + rulemaking_comments.

    python scripts/regulations_loader.py [--db NAME] [--run-date DATE]
                                         [--since DATE | --docket DOCKET_ID]
                                         [--max-comment-details N]

Input: distinct docket_id of rulemakings documents written by
federal_register_loader.py (source "federal_register") whose last_seen is the
run date, or on/after --since. --docket loads one docket instead (testing).

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
"""

import argparse
import json
import os
import re
import time
from datetime import date, datetime, timezone

import requests
from pymongo import DESCENDING, UpdateOne

from mongo_common import RawStore, add_counts, bulk, day, get_db

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


def document_doc(item, fr_document_number, today):
    """rulemakings fields from one /v4/documents list item. Dates stay the
    API's ISO strings: a comment period ends at e.g. 2026-10-07T03:59:59Z,
    which is 2026-10-06 Eastern, so truncating to a date would be wrong."""
    a = item["attributes"]
    return {
        "source": "regulations_gov",
        "document_id": item["id"],
        "object_id": a.get("objectId"),
        "docket_id": a.get("docketId"),
        "agency_id": a.get("agencyId"),
        "title": a.get("title"),
        "document_type": a.get("documentType"),
        "posted_date": a.get("postedDate"),
        "comment_start_date": a.get("commentStartDate"),
        "comment_end_date": a.get("commentEndDate"),
        "open_for_comment": a.get("openForComment"),
        "fr_document_number": fr_document_number,
        "last_seen": today,
        "raw": item,
    }


def comment_list_doc(item, object_id, docket_id):
    """rulemaking_comments fields from one /v4/comments list item. The list
    has no docketId, so it comes from the document the comment is on."""
    a = item["attributes"]
    return {
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
    def __init__(self, db, run_date, api, run_tag=None):
        self.db = db
        self.today = day(run_date)
        self.date_str = run_date.isoformat()
        self.run_id = f"regulations_gov-{self.date_str}"
        self.store = RawStore(self.run_id)
        self.api = api
        self.errors = []
        self.capped = []
        self.counts = {"documents": {}, "comments": {}, "comment_details": {}}
        self.dockets_done = []
        self.run_doc_id = db.runs.insert_one({
            "run_id": self.run_id,
            "pipeline": "regulations_gov",
            "run_tag": run_tag,
            "started_at": datetime.now(timezone.utc),
            "status": "running",
            "steps": {},
        }).inserted_id

    def raw(self, name, payload, record_count=None):
        return self.store.write_json("regulations_gov", self.date_str, name, payload,
                                     "regulations_gov", record_count)

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
        ops = []
        for d in docs:
            ops.append(UpdateOne({"_id": d.pop("_id")}, {
                "$set": d, "$setOnInsert": {"first_seen": self.today}}, upsert=True))
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
            docs.append({"_id": f"rg:{item['id']}", **document_doc(item, fr_num, self.today)})
        self.upsert(self.db.rulemakings, docs, "documents")

        for item in items:
            object_id = item["attributes"].get("objectId")
            if not object_id:
                self.errors.append(f"{item['id']}: no attributes.objectId")
                continue
            comments = [
                {"_id": f"rgc:{c['id']}", **comment_list_doc(c, object_id, docket_id)}
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
        fetched = 0
        for c in pending:
            if self.api.low_on_budget():
                print(f"stopping comment details: {self.api.remaining} requests left this hour")
                break
            detail = self.api.get(f"/comments/{c['comment_id']}")
            self.raw(f"{safe_name(c['comment_id'])}_detail.json", detail, 1)
            r = self.db.rulemaking_comments.update_one({"_id": c["_id"]}, {"$set": {
                **comment_detail_fields(detail), "detail_fetched_at": self.today}})
            self.counts["comment_details"]["modified"] = \
                self.counts["comment_details"].get("modified", 0) + r.modified_count
            fetched += 1
        self.counts["comment_details"]["fetched"] = fetched

    def finish(self):
        pending = self.db.rulemaking_comments.count_documents({"detail_fetched_at": {"$exists": False}})
        status = "partial" if self.errors else "ok"
        steps = {
            "dockets": {"processed": len(self.dockets_done), "docket_ids": self.dockets_done},
            "documents": self.counts["documents"],
            "comments": self.counts["comments"],
            "comment_details": {**self.counts["comment_details"], "pending": pending},
            "requests": self.api.requests,
            "backoffs_429": {"count": len(self.api.backoffs),
                             "wait_s": sum(b["wait_s"] for b in self.api.backoffs),
                             "events": self.api.backoffs},
            "page_cap_hit": self.capped,
        }
        self.db.runs.update_one({"_id": self.run_doc_id}, {"$set": {
            "finished_at": datetime.now(timezone.utc),
            "status": status,
            "raw_folder": dict(self.store._folders),
            "errors": self.errors,
            "steps": steps,
        }})
        return status, steps


def fr_dockets(db, last_seen=None):
    """{docket_id: {FR document_number, ...}} from the Federal Register loader's
    rulemakings documents, optionally only those matching a last_seen filter.
    docket_id may be a string or a list (the FR API returns docket_ids as a list)."""
    query = {"source": "federal_register", "docket_id": {"$nin": [None, "", []]}}
    if last_seen is not None:
        query["last_seen"] = last_seen
    out = {}
    for d in db.rulemakings.find(query, {"docket_id": 1, "document_number": 1}):
        ids = d["docket_id"] if isinstance(d["docket_id"], list) else [d["docket_id"]]
        for docket_id in filter(None, ids):
            out.setdefault(docket_id, set())
            if d.get("document_number"):
                out[docket_id].add(d["document_number"])
    return out


def ingest(db, run_date=None, since=None, docket=None, max_details=DEFAULT_MAX_DETAILS,
           run_tag=None, api=None):
    """Load the dockets, then comment details. Writes a runs document and
    returns (status, steps, run_id)."""
    run_date = run_date or date.today()
    if api is None:
        key = os.getenv("REGULATIONS_API_KEY")
        if not key:
            raise RuntimeError("REGULATIONS_API_KEY is not set in .env")
        api = Api(key)
    if docket:
        dockets = {docket: fr_dockets(db).get(docket, set())}
    else:
        dockets = fr_dockets(db, {"$gte": day(since)} if since else day(run_date))

    run = RegulationsRun(db, run_date, api, run_tag)
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
    status, steps = run.finish()
    return status, steps, run.run_id


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    p.add_argument("--run-date", type=date.fromisoformat, default=date.today())
    src = p.add_mutually_exclusive_group()
    src.add_argument("--since", type=date.fromisoformat,
                     help="dockets of FR documents last seen on/after this date (default: the run date)")
    src.add_argument("--docket", help="load this one docket, e.g. FAA-2026-9077")
    p.add_argument("--max-comment-details", type=int, default=DEFAULT_MAX_DETAILS,
                   help=f"comment detail requests per run (default {DEFAULT_MAX_DETAILS})")
    args = p.parse_args()

    status, steps, run_id = ingest(get_db(args.db), args.run_date, args.since, args.docket,
                                   args.max_comment_details)
    print(json.dumps({"run_id": run_id, "status": status, "steps": steps}, indent=1, default=str))
    if status != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
