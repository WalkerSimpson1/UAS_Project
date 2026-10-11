"""One-time migration of UAS_Tracker.db into MongoDB (docs/mongodb_schema.md).

    python scripts/migrate_sqlite_to_mongo.py [--db NAME] [--sqlite PATH]

- legislation: every SQLite row becomes a legislation_history snapshot keyed on
  (bill_id, date_added); legislation is built from the newest snapshot per bill.
  dataset_date is paired with the latest LegiScan zip on or before each run and
  verified against the bill's change_hash in that zip (3.1a).
- contracts, contract_transactions, contracts_weekly_transactions_all,
  rulemakings: copied with native types, award_key / txn_key added.
- contracts_past*: reloaded from the bulk CSVs (load_contracts_past.py) and
  checked against the SQLite row counts.

SQLite is opened read-only. Re-running is safe: SQLite-derived documents are
written with $setOnInsert, so anything the weekly pipeline has written since is
left alone. contracts_past* are dropped and reloaded (they are frozen).
"""

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from pymongo import UpdateOne

import load_contracts_past
from legiscan_loader import HAND_FIELDS, change_hashes, snapshot_doc, write_snapshots, ZIP_NAME_RE
from mongo_common import (
    LEGACY_ZIP_DIR, SQLITE_PATH, bulk, day, get_db, raw_root, to_datetime, to_float,
    txn_key_from_transaction_id, valid_award_key,
)
from mongo_indexes import create_indexes

TRACKER_FIELDS = [
    "item_name", "identifier", "body", "sequence", "event_date", "event_description",
    "track", "source_url", "source_platform", "notes",
]


def rows(conn, table):
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return [dict(r) for r in cur.execute(f"SELECT * FROM {table}")]


def tracker_fields(row):
    doc = {f: row.get(f) for f in TRACKER_FIELDS}
    doc["event_date"] = to_datetime(doc["event_date"])
    return doc


# ---------------------------------------------------------------- legislation


def find_zips():
    """{dataset_date: (absolute path, raw_source relative to RAW_ROOT)}.
    Zips already under RAW_ROOT win; otherwise the legacy repo folder is read
    and raw_source points at where the file belongs under RAW_ROOT (D15)."""
    zips = {}
    for path in sorted(LEGACY_ZIP_DIR.glob("getDatasetRaw*.zip")):
        d = ZIP_NAME_RE.search(path.name).group(1)
        zips[d] = (path, f"legiscan/{d}/{path.name}")
    root = raw_root()
    for path in sorted(root.glob("legiscan/*/getDatasetRaw*.zip")):
        d = ZIP_NAME_RE.search(path.name).group(1)
        zips[d] = (path, str(path.relative_to(root)))
    return zips


def migrate_legislation(db, sql_rows):
    report = {"hash_mismatches": [], "hand_field_conflicts": [], "hand_rows": 0}
    zips = find_zips()
    zip_dates = sorted(zips)
    hash_cache = {}

    by_run = defaultdict(list)
    hand_rows = []
    for r in sql_rows:
        (by_run[r["date_added"]] if r.get("bill_id") else hand_rows).append(r)

    steps = []
    for run_date in sorted(by_run):
        paired = max((d for d in zip_dates if d <= run_date), default=None)
        if paired and paired not in hash_cache:
            hash_cache[paired] = change_hashes(zips[paired][0])
        docs = []
        for r in by_run[run_date]:
            bill_id = str(r["bill_id"])
            raw = json.loads(r["legiscan_raw_json"]) if r.get("legiscan_raw_json") else {
                "bill_id": bill_id, "title": r["item_name"]}
            ok = paired is not None and hash_cache[paired].get(bill_id) == r["legiscan_change_hash"]
            if not ok:
                report["hash_mismatches"].append(
                    {"sqlite_id": r["id"], "bill_id": bill_id, "date_added": run_date, "paired_zip": paired})
            doc = snapshot_doc(raw, day(to_datetime(run_date)),
                               to_datetime(paired) if ok else None,
                               zips[paired][1] if ok else None)
            doc.update(tracker_fields(r))
            doc.update({
                "bill_id": bill_id,
                "state": r.get("state"),
                "legiscan_change_hash": r.get("legiscan_change_hash"),
                "date_added": r["date_added"],
                "sqlite_id": r["id"],
            })
            docs.append(doc)
        steps.append({"run_date": run_date, "paired_zip": paired, **write_snapshots(db, docs)})
    report["snapshot_runs"] = steps

    # Hand-maintained fields come from each bill's newest row. Only fill fields
    # that are still null, so a re-run never overwrites later hand edits.
    newest, older_values = {}, defaultdict(dict)
    for r in sorted((r for rs in by_run.values() for r in rs), key=lambda r: (r["date_added"], r["id"])):
        bill_id = str(r["bill_id"])
        if bill_id in newest:
            for f in HAND_FIELDS:
                if newest[bill_id].get(f) is not None:
                    older_values[bill_id][f] = newest[bill_id][f]
        newest[bill_id] = r
    ops = []
    for bill_id, r in newest.items():
        hand = {f: tracker_fields(r)[f] for f in HAND_FIELDS}
        for f, old in older_values[bill_id].items():
            if hand[f] is None:
                report["hand_field_conflicts"].append({"bill_id": bill_id, "field": f, "older_value": old})
        for f, v in hand.items():
            if v is not None:
                ops.append(UpdateOne({"_id": f"legiscan:{bill_id}", f: None}, {"$set": {f: v}}))
    report["hand_fields_set"] = bulk(db.legislation, ops)

    # Hand-entered rows (no bill_id) go straight to the current view.
    ops = []
    for r in hand_rows:
        doc = {**tracker_fields(r), "bill_id": None, "state": r.get("state"),
               "date_added": r.get("date_added"), "first_seen": to_datetime(r.get("date_added")),
               "sqlite_id": r["id"]}
        ops.append(UpdateOne({"sqlite_id": r["id"], "bill_id": None}, {"$setOnInsert": doc}, upsert=True))
    report["hand_rows"] = bulk(db.legislation, ops)
    return report


def migrate_rulemakings(db, sql_rows):
    ops = []
    for r in sql_rows:
        doc = {**tracker_fields(r), "statutory_deadline": to_datetime(r.get("statutory_deadline")),
               "date_added": r.get("date_added"), "sqlite_id": r["id"]}
        ops.append(UpdateOne({"sqlite_id": r["id"]}, {"$setOnInsert": doc}, upsert=True))
    return bulk(db.rulemakings, ops)


# ---------------------------------------------------------------- contracts


def migrate_contracts(db, sql_rows, today):
    ops, invalid = [], []
    for r in sql_rows:
        key = r["api_internal_id"]
        if not valid_award_key(key):
            invalid.append(key)
        doc = {
            "api_internal_id": key,
            "award_key": key,
            "award_id": r["award_id"],
            "recipient": r["recipient"],
            "awarding_agency": r["awarding_agency"],
            "naics_code": r["naics_code"],
            "description": r["description"],
            "notes": r["notes"],
            # SQLite's "1550" came from the search filter, not the award (D11).
            # The first award search with "PSC" in its fields fills this in.
            "psc_code": None,
            "amount_usd": to_float(r["amount_usd"]),
            "award_base_date": to_datetime(r["award_base_date"]),
            "last_modified_date": to_datetime(r["last_modified_date"]),
            "source": "award_search",
            "lookup_status": "ok",
            "raw": json.loads(r["raw_json"]) if r["raw_json"] else None,
            "raw_source": None,
            "first_seen": today,
            "last_seen": today,
        }
        ops.append(UpdateOne({"_id": key}, {"$setOnInsert": doc}, upsert=True))
    return {**bulk(db.contracts, ops), "invalid_award_keys": invalid}


def migrate_contract_transactions(db, sql_rows):
    ops, invalid = [], []
    for r in sql_rows:
        seen = day(to_datetime(r["date_added"]))
        txn_key = txn_key_from_transaction_id(r["transaction_id"])
        if not valid_award_key(r["api_internal_id"]) or txn_key.startswith("CONT_TX_"):
            invalid.append(r["transaction_id"])
        doc = {
            "transaction_id": r["transaction_id"],
            "txn_key": txn_key,
            "api_internal_id": r["api_internal_id"],
            "award_key": r["api_internal_id"],
            "type": r["type"],
            "action_type": r["action_type"],
            "action_date": to_datetime(r["action_date"]),
            "description": r["description"],
            "modification_number": r["modification_number"],
            "federal_action_obligation": to_float(r["federal_action_obligation"]),
            # USASpending2 only pulled awards with a recent last_modified_date
            "discovered_via": "award_modified",
            "date_added": r["date_added"],
            "raw": None,
            "raw_source": None,
            "first_seen": seen,
            "last_seen": seen,
        }
        ops.append(UpdateOne({"_id": r["transaction_id"]}, {"$setOnInsert": doc}, upsert=True))
    return {**bulk(db.contract_transactions, ops), "invalid_keys": invalid}


def migrate_weekly_transactions(db, sql_rows):
    ops, rejected = [], []
    for r in sql_rows:
        if r["generated_internal_id"] is None or r["mod"] is None:
            rejected.append(r)
            continue
        seen = day(to_datetime(r["date_added"]))
        action_date = to_datetime(r["action_date"])
        doc = {
            "award_key": r["generated_internal_id"],
            "award_id": r["award_id"],
            "action_date": action_date,
            "date_added": r["date_added"],
            "recipient_name": r["recipient_name"],
            "recipient_uei": r["recipient_uei"],
            "awarding_agency": r["awarding_agency"],
            "award_type": r["award_type"],
            "action_type": r["action_type"],
            "transaction_description": r["transaction_description"],
            "psc_code": None,  # SQLite never requested the PSC field
            "transaction_amount": to_float(r["transaction_amount"]),
            "late_posting": action_date is not None and seen > action_date + timedelta(days=7),
            "raw": None,
            "raw_source": None,
            "first_seen": seen,
            "last_seen": seen,
        }
        _id = {"generated_internal_id": r["generated_internal_id"], "mod": r["mod"]}
        ops.append(UpdateOne({"_id": _id}, {"$setOnInsert": doc}, upsert=True))
    return {**bulk(db.contracts_weekly_transactions_all, ops), "rejected": len(rejected)}


# ---------------------------------------------------------------- verification


def verify(db, conn):
    """Count and content checks against SQLite (section 9.2, the parts that
    apply to migrated data)."""
    q = lambda sql: conn.execute(sql).fetchall()
    checks = {}

    sql_per_date = dict(q("SELECT date_added, COUNT(*) FROM legislation WHERE bill_id IS NOT NULL GROUP BY 1"))
    mongo_per_date = {d["_id"].strftime("%Y-%m-%d"): d["n"] for d in db.legislation_history.aggregate(
        [{"$group": {"_id": "$snapshot_date", "n": {"$sum": 1}}}])}
    checks["legislation snapshots per date"] = (sql_per_date == mongo_per_date, sql_per_date, mongo_per_date)

    sql_pairs = set(q("SELECT bill_id, legiscan_change_hash FROM legislation WHERE bill_id IS NOT NULL"))
    mongo_pairs = {(d["bill_id"], d["legiscan_change_hash"]) for d in db.legislation_history.find({}, {"bill_id": 1, "legiscan_change_hash": 1})}
    checks["legislation (bill_id, change_hash) set"] = (sql_pairs == mongo_pairs, len(sql_pairs), len(mongo_pairs))

    n_bills = q("SELECT COUNT(DISTINCT bill_id) FROM legislation WHERE bill_id IS NOT NULL")[0][0]
    n_current = db.legislation.count_documents({"bill_id": {"$type": "string"}})
    checks["legislation current docs"] = (n_bills == n_current, n_bills, n_current)

    for table, key in (("contracts", "api_internal_id"), ("contract_transactions", "transaction_id")):
        sql_keys = {r[0] for r in q(f"SELECT {key} FROM {table}")}
        mongo_keys = set(db[table].distinct("_id"))
        checks[f"{table} keys"] = (sql_keys <= mongo_keys, len(sql_keys), len(mongo_keys))

    sql_weekly = {(a, m) for a, m in q("SELECT generated_internal_id, mod FROM contracts_weekly_transactions_all")}
    mongo_weekly = {(d["_id"]["generated_internal_id"], d["_id"]["mod"])
                    for d in db.contracts_weekly_transactions_all.find({}, {"_id": 1})}
    checks["contracts_weekly_transactions_all keys"] = (sql_weekly <= mongo_weekly, len(sql_weekly), len(mongo_weekly))

    for table, col, coll, field, match in (
            ("contracts", "amount_usd", "contracts", "amount_usd", {"source": "award_search"}),
            ("contract_transactions", "federal_action_obligation", "contract_transactions", "federal_action_obligation", {}),
            ("contracts_weekly_transactions_all", "transaction_amount", "contracts_weekly_transactions_all", "transaction_amount", {}),
            ("contracts_past", "total_obligated_amount", "contracts_past", "total_obligated_amount", {}),
            ("contracts_past_transactions", "federal_action_obligation", "contracts_past_transactions", "federal_action_obligation", {})):
        sql_sum = q(f"SELECT COALESCE(SUM({col}), 0) FROM {table}")[0][0]
        res = list(db[coll].aggregate([{"$match": match}, {"$group": {"_id": None, "s": {"$sum": f"${field}"}}}]))
        mongo_sum = res[0]["s"] if res else 0
        checks[f"SUM({table}.{col})"] = (abs(sql_sum - mongo_sum) < 0.01, round(sql_sum, 2), round(mongo_sum, 2))

    # rulemakings: SQLite only ever had hand rows; API documents carry a source (D17).
    for table, match in (("contracts_past", {}), ("contracts_past_transactions", {}),
                         ("rulemakings", {"source": None})):
        n_sql = q(f"SELECT COUNT(*) FROM {table}")[0][0]
        n_mongo = db[table].count_documents(match)
        checks[f"{table} count"] = (n_sql == n_mongo, n_sql, n_mongo)

    # Validation rule 4: every live award_key resolves. Expected to fail until
    # the first weekly run's backfill (step 4) adds the missing awards.
    contract_ids = set(db.contracts.distinct("_id"))
    referenced = set(db.contract_transactions.distinct("award_key")) | set(
        db.contracts_weekly_transactions_all.distinct("award_key"))
    unresolved = sorted(referenced - contract_ids)
    return checks, unresolved


# ---------------------------------------------------------------- main


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    p.add_argument("--sqlite", default=str(SQLITE_PATH), help="SQLite file (default: repo UAS_Tracker.db)")
    args = p.parse_args()

    started = datetime.now(timezone.utc)
    conn = sqlite3.connect(f"file:{args.sqlite}?mode=ro", uri=True)
    db = get_db(args.db)
    create_indexes(db)
    today = day()

    steps = {
        "legislation": migrate_legislation(db, rows(conn, "legislation")),
        "rulemakings": migrate_rulemakings(db, rows(conn, "rulemakings")),
        "contracts": migrate_contracts(db, rows(conn, "contracts"), today),
        "contract_transactions": migrate_contract_transactions(db, rows(conn, "contract_transactions")),
        "contracts_weekly_transactions_all": migrate_weekly_transactions(db, rows(conn, "contracts_weekly_transactions_all")),
        "contracts_past": load_contracts_past.load(db),
    }
    checks, unresolved = verify(db, conn)
    passed = all(ok for ok, *_ in checks.values())
    db.runs.insert_one({
        "run_id": f"migration-{today:%Y-%m-%d}",
        "pipeline": "migration",
        "started_at": started,
        "finished_at": datetime.now(timezone.utc),
        "status": "ok" if passed else "partial",
        "sqlite_path": args.sqlite,
        "steps": {
            **{k: v for k, v in steps.items() if k != "legislation"},
            "legislation": {k: v for k, v in steps["legislation"].items() if k != "snapshot_runs"},
            "checks": {k: {"ok": ok, "sqlite": str(a), "mongo": str(b)} for k, (ok, a, b) in checks.items()},
            "integrity": {"unresolved_award_keys": len(unresolved)},
        },
    })

    leg = steps["legislation"]
    print(f"database: {db.name}")
    for s in leg["snapshot_runs"]:
        print(f"  legislation run {s['run_date']} <- zip {s['paired_zip']}: {s['snapshots']}")
    print(f"  dataset_date hash mismatches (dataset_date left null): {len(leg['hash_mismatches'])}")
    for m in leg["hash_mismatches"]:
        print(f"    {m}")
    print(f"  older hand-field values not on newest snapshot: {len(leg['hand_field_conflicts'])}")
    for c in leg["hand_field_conflicts"]:
        print(f"    {c}")
    for name in ("rulemakings", "contracts", "contract_transactions", "contracts_weekly_transactions_all"):
        print(f"  {name}: {steps[name]}")
    load_contracts_past.print_report(steps["contracts_past"])
    print("checks:")
    for name, (ok, a, b) in checks.items():
        print(f"  [{'ok' if ok else 'FAIL'}] {name}: sqlite={a} mongo={b}")
    print(f"award_keys with no contracts doc (backfilled by the first weekly run): {len(unresolved)}")
    for key in unresolved:
        print(f"  {key}")
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
