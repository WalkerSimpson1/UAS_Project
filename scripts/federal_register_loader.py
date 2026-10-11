"""Federal Register load into `rulemakings` (docs/mongodb_schema.md sections 3.3, 8.4, 8.5).

    python scripts/federal_register_loader.py [--db NAME] [--start YYYY-MM-DD] [--end YYYY-MM-DD]

One GET to /documents.json per keyword phrase over a publication_date window
(default: 9 days back from today through today, so each weekly run overlaps
the last), de-duplicated by document_number. Every response page is saved
under $RAW_ROOT/federal_register/<run date>/ before anything is loaded.
Writes are upserts on _id "fr:<document_number>", so re-running any window is
safe: first_seen is kept and matched_keywords only grows.

No API key. Pass --start/--end to backfill a wider window.
"""

import argparse
import json
import re
import time
from datetime import date, datetime, timezone

import requests
from pymongo import UpdateOne

from mongo_common import RawStore, bulk, day, days_ago, get_db
from mongo_indexes import create_indexes

API = "https://www.federalregister.gov/api/v1/documents.json"
SOURCE = "federal_register"
WINDOW_DAYS = 9
PER_PAGE = 1000

# Multi-word phrases are sent in double quotes for an exact-phrase match;
# unquoted, the API matches documents containing all the words anywhere
# (section 3.3). matched_keywords records the phrase without quotes.
KEYWORDS = [
    "unmanned aircraft", "unmanned aerial", "drone", "counter-UAS", "UAS", "advanced air mobility",
]
# Checked against a live response on 2026-10-11. The documentation's
# effective_date is rejected ("field 'effective_date' not valid"); the field
# is effective_on.
FIELDS = [
    "document_number", "title", "abstract", "type", "publication_date", "effective_on",
    "agencies", "agency_names", "docket_ids", "regulation_id_numbers", "cfr_references",
    "comments_close_on", "significant", "html_url", "pdf_url", "json_url",
]


class ApiError(Exception):
    pass


def search_term(phrase):
    return f'"{phrase}"' if " " in phrase else phrase


def phrase_slug(phrase):
    return re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")


def get(session, params, pause=0.2, retries=3):
    """GET the search endpoint; returns the parsed JSON. Retries connection
    errors, 429 and 5xx, like usaspending_loader.Api."""
    for attempt in range(retries + 1):
        time.sleep(pause)
        try:
            resp = session.get(API, params=params, timeout=120)
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt * 5)
            continue
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < retries:
            time.sleep(2 ** attempt * 5)
            continue
        try:
            payload = resp.json()
        except ValueError:
            payload = resp.text
        if resp.status_code != 200 or not isinstance(payload, dict):
            raise ApiError(f"GET {API} -> {resp.status_code}: {str(payload)[:500]}")
        return payload


def search(session, store, run_date, phrase, start, end):
    """Every page of one phrase's search, each saved before it is returned.
    Returns (documents, requests made)."""
    base = [
        ("conditions[term]", search_term(phrase)),
        ("conditions[publication_date][gte]", start.isoformat()),
        ("conditions[publication_date][lte]", end.isoformat()),
        ("per_page", PER_PAGE),
        ("order", "newest"),
    ] + [("fields[]", f) for f in FIELDS]
    docs, page = [], 1
    while True:
        payload = get(session, base + [("page", page)])
        # A search with no hits has neither results nor total_pages.
        results = payload.get("results") or []
        store.write_json(SOURCE, run_date.isoformat(), f"{phrase_slug(phrase)}_p{page}.json",
                         payload, SOURCE, len(results))
        docs.extend(results)
        if page >= (payload.get("total_pages") or 1):
            return docs, page
        page += 1


def first(values):
    return values[0] if values else None


def rulemaking_fields(doc):
    """rulemakings fields from one search result (section 3.3)."""
    significant = doc.get("significant")
    return {
        "source": SOURCE,
        "document_number": doc["document_number"],
        "title": doc.get("title"),
        "abstract": doc.get("abstract"),
        "document_type": doc.get("type"),
        "publication_date": doc.get("publication_date"),
        "effective_date": doc.get("effective_on"),
        "comments_close_on": doc.get("comments_close_on"),
        "agency_names": doc.get("agency_names") or [],
        "docket_id": first(doc.get("docket_ids")),
        "docket_ids": doc.get("docket_ids") or [],
        "rin": first(doc.get("regulation_id_numbers")),
        "cfr_references": doc.get("cfr_references") or [],
        "significant": None if significant is None else bool(significant),
        "html_url": doc.get("html_url"),
        "pdf_url": doc.get("pdf_url"),
        "raw": doc,
    }


def ingest(db, start=None, end=None, run_date=None, run_tag=None, session=None):
    """Search every phrase over [start, end] and upsert into rulemakings.
    Writes a runs document and returns it; on an error the document is marked
    failed and the error re-raised."""
    run_date = run_date or date.today()
    end = end or run_date
    start = start or days_ago(WINDOW_DAYS, end)
    if start > end:
        raise ValueError(f"window start {start} is after end {end}")
    create_indexes(db, {"rulemakings", "rulemaking_comments"})
    store = RawStore(f"{SOURCE}-{run_date}")
    session = session or requests.Session()
    doc = {
        "run_id": store.run_id,
        "pipeline": SOURCE,
        "run_tag": run_tag,
        "started_at": datetime.now(timezone.utc),
        "status": "running",
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "requests": 0,
    }
    doc["_id"] = db.runs.insert_one(doc).inserted_id

    try:
        # document_number -> (document, phrases in KEYWORDS order)
        matched, per_keyword = {}, {}
        for phrase in KEYWORDS:
            docs, n = search(session, store, run_date, phrase, start, end)
            doc["requests"] += n
            per_keyword[phrase] = len(docs)
            for d in docs:
                _, phrases = matched.setdefault(d["document_number"], (d, []))
                if phrase not in phrases:
                    phrases.append(phrase)

        # Midnight of the run date, as elsewhere, so a same-day re-run leaves
        # documents unchanged (mongo_common.day()).
        seen = day(run_date)
        ops = [UpdateOne(
            {"_id": f"fr:{number}"},
            {"$set": {**rulemaking_fields(d), "last_seen": seen},
             "$addToSet": {"matched_keywords": {"$each": phrases}},
             "$setOnInsert": {"first_seen": seen}},
            upsert=True) for number, (d, phrases) in matched.items()]
        counts = bulk(db.rulemakings, ops)
        doc.update({
            "status": "ok",
            "raw_folder": store._folders.get(SOURCE),
            "upserted": len(ops),
            "new": counts["inserted"],
            "steps": {"search": {"documents_returned": sum(per_keyword.values()),
                                 "per_keyword": per_keyword, **counts}},
            "errors": [],
        })
    except Exception as e:
        doc.update({"status": "failed", "raw_folder": store._folders.get(SOURCE),
                    "errors": [f"{type(e).__name__}: {e}"]})
        raise
    finally:
        doc["finished_at"] = datetime.now(timezone.utc)
        db.runs.update_one({"_id": doc["_id"]}, {"$set": {k: v for k, v in doc.items() if k != "_id"}})
    return doc


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=date.fromisoformat,
                   help=f"first publication_date (default: {WINDOW_DAYS} days before --end)")
    p.add_argument("--end", type=date.fromisoformat, help="last publication_date (default: today)")
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    args = p.parse_args()

    run = ingest(get_db(args.db), args.start, args.end)
    print(json.dumps({k: v for k, v in run.items() if k != "_id"}, indent=1, default=str))


if __name__ == "__main__":
    main()
