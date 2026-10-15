"""Weekly USASpending load (docs/mongodb_schema.md sections 3.4, 8.2-8.5, 9.1).

    python scripts/usaspending_loader.py [--db NAME] [--test-award-unique-id]
                                         [--backfill-method auto|award_unique_id|award_ids_exact]
                                         [--no-coverage-pull] [--zfs-snapshot-pre NAME]
    python scripts/usaspending_loader.py --only-award-unique-id-test

Run order (D7): 1 weekly transaction feed (14 days, D12) -> 2 transaction
history for those awards -> 3 FY award search (current FY, at least 120 days
back, D21) -> 3b coverage pull (awards modified in the past 7 days that step 2
didn't fetch) -> 4 award backfill -> 5 integrity check. Every response is saved under $RAW_ROOT/usaspending/ before
it is loaded. All writes are upserts keyed on _id, so re-running is safe.

--backfill-method auto (default) uses award_unique_id only if the most recent
award_unique_id test in `runs` passed, otherwise the quoted award_ids
fallback (8.3). In week 1, pass --test-award-unique-id so the test runs
before step 4.
"""

import argparse
import json
import time
from datetime import date, datetime, timedelta, timezone

import requests
from pymongo import UpdateOne

from mongo_common import (
    RawStore, add_counts, bulk, day, days_ago, fiscal_year, fy_start, get_db, piid_from_award_key,
    psc_code_from, to_datetime, to_float, txn_key_from_transaction_id, valid_award_key,
)
from mongo_indexes import create_indexes

API = "https://api.usaspending.gov/api/v2"
AWARD_TYPES = ["A", "B", "C", "D"]
FEED_DAYS = 14          # D12
# The award search covers the current FY, reaching back at least this many
# days so the prior FY's late postings (DoD publishes with a ~90-day delay)
# are still picked up early in a new FY (D21).
AWARD_SEARCH_MIN_DAYS = 120
COVERAGE_DAYS = 7       # today's USASpending2 rule
NEEDS_ATTENTION_ATTEMPTS = 4

# Same fields as USASpending1.ipynb plus PSC (D11).
AWARD_FIELDS = [
    "generated_internal_id", "Award ID", "Recipient Name", "Awarding Agency", "Funding Agency",
    "Description", "Base Obligation Date", "Last Modified Date", "Start Date", "End Date",
    "Award Amount", "Contract Award Type", "NAICS", "Total Outlays", "PSC",
]
# Same fields as the visualizations notebook plus PSC (D11).
TXN_SEARCH_FIELDS = [
    "Award ID", "Action Date", "Transaction Amount", "Transaction Description",
    "generated_internal_id", "Mod", "Recipient Name", "Recipient UEI", "Awarding Agency",
    "Award Type", "Action Type", "PSC",
]


class ApiError(Exception):
    pass


class Api:
    def __init__(self, pause=0.2, retries=3):
        self.session = requests.Session()
        self.pause = pause
        self.retries = retries

    def post(self, path, body, retries=None):
        """Returns (status_code, parsed JSON or response text). Retries
        connection errors, 429 and 5xx."""
        retries = self.retries if retries is None else retries
        for attempt in range(retries + 1):
            time.sleep(self.pause)
            try:
                resp = self.session.post(f"{API}{path}", json=body, timeout=120)
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(2 ** attempt * 5)
                continue
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < retries:
                time.sleep(2 ** attempt * 5)
                continue
            try:
                return resp.status_code, resp.json()
            except ValueError:
                return resp.status_code, resp.text

    def post_ok(self, path, body):
        status, payload = self.post(path, body)
        if status != 200 or not isinstance(payload, dict):
            raise ApiError(f"POST {path} -> {status}: {str(payload)[:500]}")
        return payload


def id_key(_id):
    """Hashable form of an _id (compound _ids are dicts)."""
    return tuple(_id.items()) if isinstance(_id, dict) else _id


def keep_unchanged_raw_source(collection, docs):
    """raw files are write-once, so a re-run writes a new folder. Keep the old
    raw_source when the payload itself didn't change, so a re-run leaves the
    document untouched. Returns the stored docs by id_key(_id)."""
    existing = {id_key(d["_id"]): d for d in collection.find(
        {"_id": {"$in": [d["_id"] for d in docs]}}, {"raw": 1, "raw_source": 1, "first_seen": 1})}
    for d in docs:
        old = existing.get(id_key(d["_id"]))
        if old and old.get("raw") == d.get("raw") and old.get("raw_source"):
            d["raw_source"] = old["raw_source"]
    return existing


def award_fields(row):
    """contracts fields from a spending_by_award result (USASpending1 mapping)."""
    naics = row.get("NAICS")
    key = row["generated_internal_id"]
    return {
        "api_internal_id": key,
        "award_key": key,
        "award_id": row.get("Award ID"),
        "recipient": row.get("Recipient Name"),
        "awarding_agency": row.get("Awarding Agency"),
        "naics_code": naics.get("code") if isinstance(naics, dict) else naics,
        "description": row.get("Description"),
        "psc_code": psc_code_from(row.get("PSC")),
        "amount_usd": to_float(row.get("Award Amount")),
        "award_base_date": to_datetime(row.get("Base Obligation Date")),
        "last_modified_date": to_datetime(row.get("Last Modified Date")),
        "raw": row,
    }


class WeeklyRun:
    def __init__(self, db, run_date, api=None, zfs_snapshot_pre=None, run_tag=None):
        self.db = db
        self.run_date = run_date
        self.today = day(run_date)
        self.date_str = run_date.isoformat()
        self.run_id = f"usaspending-{self.date_str}"
        self.store = RawStore(self.run_id)
        self.api = api or Api()
        self.steps = {}
        self.errors = []
        self.run_doc_id = db.runs.insert_one({
            "run_id": self.run_id,
            "pipeline": "usaspending",
            "run_tag": run_tag,
            "started_at": datetime.now(timezone.utc),
            "status": "running",
            "zfs_snapshot_pre": zfs_snapshot_pre,
            "steps": {},
        }).inserted_id

    def raw(self, endpoint, name, payload, record_count=None):
        return self.store.write_json(f"usaspending/{endpoint}", self.date_str, name, payload,
                                     "usaspending", record_count)

    def paged(self, endpoint, path, body):
        """POST every page until hasNext is false, saving each page.
        Yields (row, raw_source)."""
        self.raw(endpoint, "request.json", body)
        page = 1
        while True:
            payload = self.api.post_ok(path, {**body, "page": page})
            results = payload.get("results") or []
            rel = self.raw(endpoint, f"page_{page:03d}.json", payload, len(results))
            for row in results:
                yield row, rel
            if not payload.get("page_metadata", {}).get("hasNext"):
                break
            page += 1

    def step(self, name, fn, *args):
        try:
            return fn(*args)
        except Exception as e:  # later steps still run (8.2)
            self.errors.append(f"{name}: {type(e).__name__}: {e}")
            self.steps.setdefault(name, {})["error"] = str(e)
            print(f"step {name} failed: {e}")
            return None

    # ------------------------------------------------------------ step 1

    def weekly_feed(self):
        start = days_ago(FEED_DAYS, self.run_date).isoformat()
        body = {
            "filters": {
                "award_type_codes": AWARD_TYPES,
                "psc_codes": ["1550"],
                "time_period": [{"start_date": start, "end_date": self.date_str}],
            },
            "fields": TXN_SEARCH_FIELDS,
            "limit": 100,
            "sort": "Action Date",
        }
        docs, rejected = {}, []
        for row, rel in self.paged("weekly_transactions", "/search/spending_by_transaction/", body):
            gid, mod = row.get("generated_internal_id"), row.get("Mod")
            if gid is None or mod is None or not valid_award_key(gid):
                rejected.append(row)
                continue
            _id = {"generated_internal_id": gid, "mod": mod}
            docs[(gid, mod)] = {
                "_id": _id,
                "award_key": gid,
                "award_id": row.get("Award ID"),
                "action_date": to_datetime(row.get("Action Date")),
                "recipient_name": row.get("Recipient Name"),
                "recipient_uei": row.get("Recipient UEI"),
                "awarding_agency": row.get("Awarding Agency"),
                "award_type": row.get("Award Type"),
                "action_type": row.get("Action Type"),
                "transaction_description": row.get("Transaction Description"),
                "psc_code": psc_code_from(row.get("PSC")),
                "transaction_amount": to_float(row.get("Transaction Amount")),
                "raw": row,
                "raw_source": rel,
            }
        docs = list(docs.values())
        existing = keep_unchanged_raw_source(self.db.contracts_weekly_transactions_all, docs)
        ops, late = [], 0
        for d in docs:
            is_late = d["action_date"] is not None and self.today > d["action_date"] + timedelta(days=7)
            if id_key(d["_id"]) not in existing and is_late:
                late += 1
            fields = {k: v for k, v in d.items() if k != "_id"}
            ops.append(UpdateOne({"_id": d["_id"]}, {
                "$set": {**fields, "last_seen": self.today},
                "$setOnInsert": {"first_seen": self.today, "date_added": self.date_str, "late_posting": is_late},
            }, upsert=True))
        counts = bulk(self.db.contracts_weekly_transactions_all, ops)
        award_ids = {}
        for d in docs:
            award_ids.setdefault(d["award_key"], d["award_id"])
        self.steps["weekly_feed"] = {
            "window_days": FEED_DAYS, "start_date": start, "end_date": self.date_str,
            "rows": len(docs), "award_keys": len(award_ids), "new_rows": counts["inserted"],
            "modified": counts["modified"], "late_postings": late, "rejected": len(rejected),
        }
        return award_ids

    # ------------------------------------------------------------ steps 2 / 3b

    def transaction_history(self, award_keys, discovered_via, step_name):
        totals = {"inserted": 0, "modified": 0, "unchanged": 0}
        failed, invalid = [], []
        for award_key in sorted(award_keys):
            try:
                body = {"award_id": award_key, "sort": "action_date", "order": "asc", "limit": 5000}
                pages, page = [], 1
                while True:
                    payload = self.api.post_ok("/transactions/", {**body, "page": page})
                    pages.append(payload)
                    if not payload.get("page_metadata", {}).get("hasNext"):
                        break
                    page += 1
                results = [r for p in pages for r in (p.get("results") or [])]
                rel = self.raw("transactions", f"{award_key}.json",
                               {"request": body, "pages": pages}, len(results))
            except Exception as e:
                failed.append({"award_key": award_key, "error": str(e)})
                continue
            docs = []
            for r in results:
                tid = r.get("id")
                if not tid or not tid.startswith("CONT_TX_"):
                    invalid.append(tid)
                    continue
                docs.append({
                    "_id": tid,
                    "transaction_id": tid,
                    "txn_key": txn_key_from_transaction_id(tid),
                    "api_internal_id": award_key,
                    "award_key": award_key,
                    # Descriptions, as USASpending2 stored them; codes stay in raw.
                    "type": r.get("type_description"),
                    "action_type": r.get("action_type_description"),
                    "action_date": to_datetime(r.get("action_date")),
                    "description": r.get("description"),
                    "modification_number": r.get("modification_number"),
                    "federal_action_obligation": to_float(r.get("federal_action_obligation")),
                    "raw": r,
                    "raw_source": rel,
                })
            keep_unchanged_raw_source(self.db.contract_transactions, docs)
            ops = [UpdateOne({"_id": d["_id"]}, {
                "$set": {**{k: v for k, v in d.items() if k != "_id"}, "last_seen": self.today},
                "$setOnInsert": {"first_seen": self.today, "discovered_via": discovered_via,
                                 "date_added": self.date_str},
            }, upsert=True) for d in docs]
            add_counts(totals, bulk(self.db.contract_transactions, ops))
        self.steps[step_name] = {"awards": len(award_keys), **totals,
                                 "failed": failed, "invalid_ids": len(invalid)}
        if failed:
            self.errors.append(f"{step_name}: {len(failed)} awards failed")
        return totals

    # ------------------------------------------------------------ step 3

    def award_search(self):
        start = min(fy_start(self.run_date), days_ago(AWARD_SEARCH_MIN_DAYS, self.run_date))
        time_period = {"start_date": start.isoformat(), "end_date": days_ago(1, self.run_date).isoformat()}
        body = {
            "subawards": False,
            "limit": 100,
            "filters": {
                "award_type_codes": AWARD_TYPES,
                "psc_codes": ["1550"],
                "time_period": [time_period],
            },
            "fields": AWARD_FIELDS,
        }
        docs, rejected = {}, 0
        for row, rel in self.paged("awards", "/search/spending_by_award/", body):
            if not row.get("generated_internal_id"):
                rejected += 1
                continue
            d = award_fields(row)
            d["_id"] = row["generated_internal_id"]
            d["raw_source"] = rel
            docs[d["_id"]] = d
        docs = list(docs.values())
        keep_unchanged_raw_source(self.db.contracts, docs)
        ops = [UpdateOne({"_id": d["_id"]}, {
            "$set": {**{k: v for k, v in d.items() if k != "_id"},
                     "source": "award_search", "lookup_status": "ok", "last_seen": self.today},
            "$unset": {"backfill": ""},
            "$setOnInsert": {"first_seen": self.today, "notes": None},
        }, upsert=True) for d in docs]
        counts = bulk(self.db.contracts, ops)
        self.steps["award_search"] = {
            "fiscal_year": fiscal_year(self.run_date), **time_period,
            **counts, "results": len(docs), "rejected": rejected,
            "psc_not_1550": sum(1 for d in docs if d["psc_code"] not in (None, "1550")),
            "psc_null": sum(1 for d in docs if d["psc_code"] is None),
            "psc_shapes": sorted({type(d["raw"].get("PSC")).__name__ for d in docs}),
        }
        return {d["_id"] for d in docs}

    # ------------------------------------------------------------ step 3b

    def coverage_pull(self, already_fetched):
        since = day(days_ago(COVERAGE_DAYS, self.run_date))
        keys = set(self.db.contracts.distinct("_id", {"last_modified_date": {"$gte": since}}))
        keys -= set(already_fetched)
        totals = self.transaction_history(keys, "award_modified", "coverage_pull")
        self.steps["coverage_pull"]["new_txns_not_in_feed"] = totals["inserted"]

    # ------------------------------------------------------------ step 4

    def backfill_method(self, requested):
        if requested != "auto":
            return requested
        if "award_unique_id_test" in self.steps:  # ran earlier in this run
            return "award_unique_id" if self.steps["award_unique_id_test"].get("passed") else "award_ids_exact"
        last = self.db.runs.find_one({"steps.award_unique_id_test.passed": {"$exists": True}},
                                     sort=[("started_at", -1)])
        passed = bool(last and last["steps"]["award_unique_id_test"]["passed"])
        return "award_unique_id" if passed else "award_ids_exact"

    def lookup_award(self, award_key, piid, method):
        """Returns (accepted result or None, method used, attempts). A result
        is accepted only if exactly one row has generated_internal_id ==
        award_key, so the unquoted (fuzzy) PIID search is safe as a last try."""
        tries = [("award_ids_exact", {"award_ids": [f'"{piid}"']}, 0),
                 # The quoted form has returned 503 consistently (2026-10-10);
                 # the unquoted form works.
                 ("award_ids_exact", {"award_ids": [piid]}, None)]
        if method == "award_unique_id":
            tries.insert(0, ("award_unique_id", {"award_unique_id": award_key}, None))
        attempts = []
        for m, extra, retries in tries:
            filters = {"award_type_codes": AWARD_TYPES, **extra}
            body = {"subawards": False, "limit": 100, "page": 1, "filters": filters, "fields": AWARD_FIELDS}
            status, payload = self.api.post("/search/spending_by_award/", body, retries=retries)
            attempts.append({"method": m, "request": body, "status": status, "response": payload})
            if status != 200 or not isinstance(payload, dict):
                continue
            matches = [r for r in payload.get("results") or [] if r.get("generated_internal_id") == award_key]
            if len(matches) == 1:
                return matches[0], m, attempts
        if all(a["status"] != 200 for a in attempts):
            raise ApiError(f"every lookup failed: {[a['status'] for a in attempts]}")
        return None, tries[-1][0], attempts

    def backfill(self, requested_method):
        method = self.backfill_method(requested_method)
        weekly_award_ids = {d["award_key"]: d.get("award_id") for d in
                            self.db.contracts_weekly_transactions_all.find({}, {"award_key": 1, "award_id": 1})}
        txn_keys = set(self.db.contract_transactions.distinct("award_key"))
        referenced = set(weekly_award_ids) | txn_keys
        resolved = set(self.db.contracts.distinct("_id", {"lookup_status": "ok"}))
        missing = sorted(referenced - resolved)
        stored = {d["_id"]: d for d in self.db.contracts.find({"_id": {"$in": missing}})}

        stats = {"method": method, "missing": len(missing), "resolved_unique_id": 0,
                 "resolved_award_ids": 0, "pending": 0, "psc_not_1550": 0, "errors": 0}
        ops = []
        for award_key in missing:
            piid = weekly_award_ids.get(award_key) or piid_from_award_key(award_key)
            referenced_by = ("contracts_weekly_transactions_all" if award_key in weekly_award_ids
                             else "contract_transactions")
            try:
                hit, used, attempts = self.lookup_award(award_key, piid, method)
            except Exception as e:
                # Not a miss: no stub attempt is counted. Step 5 reports the key.
                stats["errors"] += 1
                self.errors.append(f"backfill {award_key}: {e}")
                continue
            rel = self.raw("award_backfill", f"{award_key}.json", {"attempts": attempts}, 1 if hit else 0)
            old = stored.get(award_key) or {}
            same_day = (old.get("backfill") or {}).get("last_attempt") == self.today
            update = {
                "$setOnInsert": {"first_seen": self.today, "notes": None, "backfill.first_attempt": self.today},
                "$set": {"backfill.method": used, "backfill.last_attempt": self.today,
                         "backfill.referenced_by": referenced_by, "last_seen": self.today},
            }
            if not same_day:
                update["$inc"] = {"backfill.attempts": 1}
            if hit:
                fields = award_fields(hit)
                update["$set"].update({**fields, "raw_source": rel, "source": "backfill", "lookup_status": "ok"})
                stats["resolved_unique_id" if used == "award_unique_id" else "resolved_award_ids"] += 1
                if fields["psc_code"] not in (None, "1550"):
                    stats["psc_not_1550"] += 1
            else:
                update["$setOnInsert"].update({
                    "api_internal_id": award_key, "award_key": award_key, "award_id": piid,
                    "psc_code": None, "source": "backfill", "lookup_status": "pending",
                })
                stats["pending"] += 1
            ops.append(UpdateOne({"_id": award_key}, update, upsert=True))
        add_counts(stats, bulk(self.db.contracts, ops))
        stats["needs_attention"] = self.db.contracts.distinct("_id", {
            "lookup_status": "pending", "backfill.attempts": {"$gte": NEEDS_ATTENTION_ATTEMPTS}})
        self.steps["backfill"] = stats

    # ------------------------------------------------------------ step 5

    def integrity(self):
        referenced = set(self.db.contract_transactions.distinct("award_key")) | set(
            self.db.contracts_weekly_transactions_all.distinct("award_key"))
        unresolved = sorted(referenced - set(self.db.contracts.distinct("_id")))
        pending = self.db.contracts.count_documents({"lookup_status": "pending"})
        self.steps["integrity"] = {"referenced_award_keys": len(referenced),
                                   "unresolved_award_keys": len(unresolved),
                                   "unresolved": unresolved, "pending": pending}

    # ------------------------------------------------------------ 9.1

    def award_unique_id_test(self):
        """Section 9.1 cases. Records results; passes if cases 1-5 and 8 behave."""
        today = self.date_str
        cases = [
            (1, "CONT_AWD_W91ZRS26FC010_9700_47QSMS25D008P_4732", "award", None, "one"),
            (2, "CONT_AWD_W91QVN26PA017_9700_-NONE-_-NONE-", "award", None, "one"),
            (3, "CONT_AWD_W911QY19F0431_9700_W911QY18D0192_9700", "award", None, "one"),
            (4, "CONT_AWD_19GT5026P0853_1900_-NONE-_-NONE-", "award", None, "one"),
            (5, "CONT_AWD_DOESNOTEXIST_0000_-NONE-_-NONE-", "award", None, "zero"),
            (6, "cont_awd_w91qvn26pa017_9700_-none-_-none-", "award", None, "record"),
            # Fixed start, as specified in 9.1, so results stay comparable across runs.
            (7, "CONT_AWD_W911QY19F0431_9700_W911QY18D0192_9700", "award", "2025-10-01", "record"),
            (8, "CONT_AWD_W91ZRS26FC010_9700_47QSMS25D008P_4732", "transaction", None, "only_key"),
        ]
        results = []
        for n, key, endpoint, start, expect in cases:
            filters = {"award_type_codes": AWARD_TYPES, "award_unique_id": key}
            if start:
                filters["time_period"] = [{"start_date": start, "end_date": today}]
            if endpoint == "award":
                path = "/search/spending_by_award/"
                body = {"subawards": False, "limit": 100, "page": 1, "filters": filters, "fields": AWARD_FIELDS}
            else:
                path = "/search/spending_by_transaction/"
                body = {"limit": 100, "page": 1, "filters": filters, "fields": TXN_SEARCH_FIELDS,
                        "sort": "Action Date"}
            status, payload = self.api.post(path, body)
            self.raw("award_unique_id_test", f"case_{n}_{endpoint}.json",
                     {"request": body, "status": status, "response": payload})
            rows = payload.get("results") or [] if isinstance(payload, dict) else []
            gids = [r.get("generated_internal_id") for r in rows]
            if expect == "one":
                ok = status == 200 and len(rows) == 1 and gids[0] == key
            elif expect == "zero":
                ok = status == 200 and len(rows) == 0
            elif expect == "only_key":
                ok = status == 200 and len(rows) > 0 and all(g == key for g in gids)
            else:
                ok = None
            psc = [r.get("PSC") for r in rows[:1]] if endpoint == "award" else []
            results.append({
                "case": n, "endpoint": endpoint, "award_unique_id": key, "time_period_start": start,
                "status": status, "rejected": status != 200, "rows": len(rows),
                "matching_rows": sum(1 for g in gids if g == key), "expected": expect, "ok": ok,
                "psc_shape": type(psc[0]).__name__ if psc else None, "psc_example": psc[0] if psc else None,
            })
        passed = all(r["ok"] for r in results if r["case"] in (1, 2, 3, 4, 5, 8))
        self.steps["award_unique_id_test"] = {"passed": passed, "cases": results}
        return passed

    # ------------------------------------------------------------ run

    def finish(self):
        status = "partial" if self.errors else "ok"
        self.db.runs.update_one({"_id": self.run_doc_id}, {"$set": {
            "finished_at": datetime.now(timezone.utc),
            "status": status,
            "raw_folder": dict(self.store._folders),
            "errors": self.errors,
            "steps": self.steps,
        }})
        return status


def weekly(db, run_date=None, backfill_method="auto", test_award_unique_id=False,
           coverage_pull=True, zfs_snapshot_pre=None, run_tag=None):
    """Steps 1-5 in D7 order. Returns (WeeklyRun, status)."""
    create_indexes(db, {"contracts", "contract_transactions", "contracts_weekly_transactions_all"})
    run = WeeklyRun(db, run_date or date.today(), zfs_snapshot_pre=zfs_snapshot_pre, run_tag=run_tag)
    feed = run.step("weekly_feed", run.weekly_feed) or {}
    run.step("txn_history", run.transaction_history, set(feed), "weekly_feed", "txn_history")
    run.step("award_search", run.award_search)
    if coverage_pull:
        run.step("coverage_pull", run.coverage_pull, set(feed))
    if test_award_unique_id:
        run.step("award_unique_id_test", run.award_unique_id_test)
    run.step("backfill", run.backfill, backfill_method)
    run.step("integrity", run.integrity)
    return run, run.finish()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    p.add_argument("--run-date", type=date.fromisoformat, default=date.today())
    p.add_argument("--backfill-method", choices=["auto", "award_unique_id", "award_ids_exact"], default="auto")
    p.add_argument("--test-award-unique-id", action="store_true", help="run the 9.1 test before step 4")
    p.add_argument("--only-award-unique-id-test", action="store_true", help="run only the 9.1 test")
    p.add_argument("--no-coverage-pull", action="store_true", help="skip step 3b (D16)")
    p.add_argument("--zfs-snapshot-pre", help="name of the -pre ZFS snapshot, recorded in runs")
    args = p.parse_args()

    db = get_db(args.db)
    if args.only_award_unique_id_test:
        create_indexes(db, {"contracts", "contract_transactions", "contracts_weekly_transactions_all"})
        run = WeeklyRun(db, args.run_date, zfs_snapshot_pre=args.zfs_snapshot_pre)
        run.step("award_unique_id_test", run.award_unique_id_test)
        status = run.finish()
    else:
        run, status = weekly(db, args.run_date, args.backfill_method, args.test_award_unique_id,
                             not args.no_coverage_pull, args.zfs_snapshot_pre)
    print(json.dumps({"run_id": run.run_id, "status": status, "errors": run.errors, "steps": run.steps},
                     indent=1, default=str))
    if status != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
