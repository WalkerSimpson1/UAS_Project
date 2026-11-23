"""One-time copy of the restored MongoDB data into Postgres (docs/DECISIONS.md P3).

Prereqs:
    docker compose --profile migrate up -d mongo
    docker compose exec -T mongo mongorestore --gzip --archive=/import/<dump>.archive.gz --drop
    data/raw/ extracted from raw.tar

Re-runnable: it empties the source tables and reloads them in one transaction,
then checks counts and dollar sums against MongoDB. Analysis tables are untouched.

    .venv/Scripts/python scripts/migrate_mongo_to_pg.py
"""
from __future__ import annotations

import json
import os
import sys

from pymongo import MongoClient

from pg_common import RAW_ROOT, connect, eastern_midnight, jsonb, to_date, to_ts

MONGO_URI = os.environ.get("MIGRATE_MONGO_URI", "mongodb://127.0.0.1:27017/")
MONGO_DB = os.environ.get("MONGO_DB", "uas_tracker")

TABLES = [
    "raw_files", "bills", "bill_snapshots", "regulatory_documents", "regulatory_comments",
    "contract_awards", "contract_transactions", "contract_activity_feed",
    "past_awards", "past_transactions",
]


def insert(cur, table: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    cols = list(rows[0])
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
    cur.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


def strip_id(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "_id"}


# ------------------------------------------------------------------ mappers

def raw_files() -> list[dict]:
    out = []
    manifest = RAW_ROOT / "manifest.jsonl"
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        m = json.loads(line)
        known = {"path", "sha256", "source", "endpoint", "fetched_at", "record_count", "run_id"}
        out.append({
            "path": m["path"], "sha256": m["sha256"], "source": m["source"],
            "endpoint": m["endpoint"], "fetched_at": to_ts(m["fetched_at"]),
            "record_count": m.get("record_count"), "run_id": m.get("run_id"),
            "extra": jsonb({k: v for k, v in m.items() if k not in known}),
        })
    return out


def bill(d: dict) -> dict:
    r = d.get("raw") or {}
    return {
        "bill_id": d["bill_id"], "state": d["state"], "session_id": r.get("session_id"),
        "identifier": d["identifier"], "title": d["item_name"],
        "description": r.get("description"), "status": r.get("status"),
        "status_date": to_date(r.get("status_date")), "url": r.get("url"),
        "state_link": r.get("state_link") or d.get("source_url"),
        "change_hash": d["legiscan_change_hash"], "raw": jsonb(r), "raw_source": d.get("raw_source"),
        "first_seen": to_date(d["first_seen"]), "last_seen": to_date(d["last_seen"]),
        "last_changed": to_date(d["last_changed"]),
    }


def bill_snapshot(d: dict) -> dict:
    return {
        "bill_id": d["bill_id"], "snapshot_date": to_date(d["snapshot_date"]),
        "dataset_date": to_date(d.get("dataset_date")), "change_hash": d["legiscan_change_hash"],
        "changed_since_prev": bool(d["changed_since_prev"]), "raw": jsonb(d["raw"]),
        "raw_source": d.get("raw_source"),
    }


def reg_doc(d: dict) -> dict:
    fr = d["source"] == "federal_register"
    close = d.get("comments_close_on") if fr else d.get("comment_end_date")
    return {
        "id": d["_id"], "source": d["source"],
        "document_number": d.get("document_number"), "document_id": d.get("document_id"),
        "object_id": d.get("object_id"), "fr_document_number": d.get("fr_document_number"),
        "title": d.get("title"), "abstract": d.get("abstract"), "document_type": d.get("document_type"),
        "agency_names": d.get("agency_names") or [], "docket_id": d.get("docket_id"),
        "docket_ids": d.get("docket_ids") or [], "rin": d.get("rin"),
        "cfr_references": jsonb(d.get("cfr_references") or []), "significant": d.get("significant"),
        "publication_date": to_date(d.get("publication_date")), "posted_at": to_ts(d.get("posted_date")),
        "effective_date": to_date(d.get("effective_date")),
        "comment_start_at": to_ts(d.get("comment_start_date")),
        "comments_close_at": eastern_midnight(close) if fr else to_ts(close),
        "open_for_comment": d.get("open_for_comment"),
        "html_url": d.get("html_url"), "pdf_url": d.get("pdf_url"),
        "matched_keywords": d.get("matched_keywords") or [], "raw": jsonb(d["raw"]),
        "raw_source": d.get("raw_source"),
        "first_seen": to_date(d["first_seen"]), "last_seen": to_date(d["last_seen"]),
    }


def reg_comment(d: dict) -> dict:
    return {
        "id": d["_id"], "comment_id": d["comment_id"], "comment_on_id": d["comment_on_id"],
        "docket_id": d.get("docket_id"), "agency_id": d.get("agency_id"), "title": d.get("title"),
        "posted_at": to_ts(d.get("posted_date")), "organization": d.get("organization"),
        "submitter_name": d.get("submitter_name"), "comment_text": d.get("comment_text"),
        "raw": jsonb(d["raw"]), "raw_detail": jsonb(d.get("raw_detail")),
        "raw_source": d.get("raw_source"), "detail_fetched_at": to_date(d.get("detail_fetched_at")),
        "first_seen": to_date(d["first_seen"]), "last_seen": to_date(d["last_seen"]),
    }


def award(d: dict) -> dict:
    r = d.get("raw") or {}
    return {
        "award_key": d["award_key"], "award_id": d.get("award_id"), "recipient": d.get("recipient"),
        "awarding_agency": d.get("awarding_agency"), "funding_agency": r.get("Funding Agency"),
        "naics_code": d.get("naics_code"), "psc_code": d.get("psc_code"),
        "description": d.get("description"), "amount_usd": d.get("amount_usd"),
        "award_base_date": to_date(d.get("award_base_date")),
        "last_modified_at": to_ts(d.get("last_modified_date")), "source": d["source"],
        "lookup_status": d.get("lookup_status") or "ok", "backfill": jsonb(d.get("backfill")),
        "raw": jsonb(d.get("raw")), "raw_source": d.get("raw_source"),
        "first_seen": to_date(d["first_seen"]), "last_seen": to_date(d["last_seen"]),
    }


def transaction(d: dict) -> dict:
    return {
        "transaction_id": d["transaction_id"], "txn_key": d["txn_key"], "award_key": d["award_key"],
        "type": d.get("type"), "action_type": d.get("action_type"),
        "action_date": to_date(d.get("action_date")), "description": d.get("description"),
        "modification_number": d.get("modification_number"),
        "federal_action_obligation": d.get("federal_action_obligation"),
        "discovered_via": d["discovered_via"], "raw": jsonb(d.get("raw")),
        "raw_source": d.get("raw_source"),
        "first_seen": to_date(d["first_seen"]), "last_seen": to_date(d["last_seen"]),
    }


def feed(d: dict) -> dict:
    return {
        "award_key": d["award_key"], "mod": d["_id"]["mod"], "award_id": d.get("award_id"),
        "action_date": to_date(d.get("action_date")), "recipient_name": d.get("recipient_name"),
        "recipient_uei": d.get("recipient_uei"), "awarding_agency": d.get("awarding_agency"),
        "award_type": d.get("award_type"), "action_type": d.get("action_type"),
        "transaction_description": d.get("transaction_description"), "psc_code": d.get("psc_code"),
        "transaction_amount": d.get("transaction_amount"),
        "late_posting": bool(d.get("late_posting")), "raw": jsonb(d.get("raw")),
        "raw_source": d.get("raw_source"),
        "first_seen": to_date(d["first_seen"]), "last_seen": to_date(d["last_seen"]),
    }


def past_award(d: dict) -> dict:
    return {
        "award_key": d["award_key"], "award_kind": d.get("award_kind"),
        "recipient_name": d.get("recipient_name"), "recipient_uei": d.get("recipient_uei"),
        "awarding_agency_name": d.get("awarding_agency_name"),
        "awarding_sub_agency_name": d.get("awarding_sub_agency_name"),
        "total_obligated_amount": d.get("total_obligated_amount"),
        "award_base_action_date": to_date(d.get("award_base_action_date")),
        "award_latest_action_date": to_date(d.get("award_latest_action_date")),
        "description": d.get("prime_award_base_transaction_description"),
        "row": jsonb(strip_id(d)), "source_file": d["source_file"],
    }


def past_txn(d: dict) -> dict:
    return {
        "txn_key": d["txn_key"], "award_key": d["award_key"],
        "action_date": to_date(d.get("action_date")),
        "action_date_fiscal_year": d.get("action_date_fiscal_year"),
        "federal_action_obligation": d.get("federal_action_obligation"),
        "recipient_name": d.get("recipient_name"), "awarding_agency_name": d.get("awarding_agency_name"),
        "extent_competed": d.get("extent_competed"), "type_of_set_aside": d.get("type_of_set_aside"),
        "country_of_product_or_service_origin": d.get("country_of_product_or_service_origin"),
        "row": jsonb(strip_id(d)), "source_file": d["source_file"],
    }


COPIES = [  # (mongo collection, postgres table, mapper)
    ("legislation", "bills", bill),
    ("legislation_history", "bill_snapshots", bill_snapshot),
    ("rulemakings", "regulatory_documents", reg_doc),
    ("rulemaking_comments", "regulatory_comments", reg_comment),
    ("contracts", "contract_awards", award),
    ("contract_transactions", "contract_transactions", transaction),
    ("contracts_weekly_transactions_all", "contract_activity_feed", feed),
    ("contracts_past", "past_awards", past_award),
    ("contracts_past_transactions", "past_transactions", past_txn),
]

SUMS = [  # (mongo collection, field, postgres table, column)
    ("contracts", "amount_usd", "contract_awards", "amount_usd"),
    ("contract_transactions", "federal_action_obligation", "contract_transactions", "federal_action_obligation"),
    ("contracts_weekly_transactions_all", "transaction_amount", "contract_activity_feed", "transaction_amount"),
    ("contracts_past", "total_obligated_amount", "past_awards", "total_obligated_amount"),
    ("contracts_past_transactions", "federal_action_obligation", "past_transactions", "federal_action_obligation"),
]


# ------------------------------------------------------------------ runs

RUN_KEYS = {"_id", "run_id", "pipeline", "run_tag", "started_at", "finished_at", "status", "errors"}


def copy_runs(cur, mdb) -> int:
    """Old run log -> runs (only if not copied before). details keeps everything else."""
    cur.execute("DELETE FROM runs WHERE details ? 'mongo_id'")
    ids: dict[str, int] = {}
    docs = list(mdb.runs.find().sort("started_at", 1))
    for d in docs:
        details = {k: v for k, v in d.items() if k not in RUN_KEYS}
        details["mongo_id"] = str(d["_id"])
        cur.execute(
            "INSERT INTO runs (run_id, pipeline, run_tag, started_at, finished_at, status, details, errors)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (d["run_id"], d["pipeline"], d.get("run_tag"), to_ts(d["started_at"]),
             to_ts(d.get("finished_at")), d["status"], jsonb(details),
             [str(e) for e in d.get("errors") or []]),
        )
        ids[d["run_id"]] = cur.fetchone()[0]
    for d in docs:
        for child in (d.get("children") or {}).values():
            if child.get("run_id") in ids:
                cur.execute("UPDATE runs SET parent_id = %s WHERE id = %s",
                            (ids[d["run_id"]], ids[child["run_id"]]))
    return len(docs)


# ------------------------------------------------------------------ main

def main() -> None:
    mdb = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)[MONGO_DB]
    mdb.command("ping")
    counts: dict[str, int] = {}

    with connect() as conn, conn.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(TABLES)}")
        counts["raw_files"] = insert(cur, "raw_files", raw_files())
        for coll, table, fn in COPIES:
            counts[table] = insert(cur, table, [fn(d) for d in mdb[coll].find()])
        counts["runs"] = copy_runs(cur, mdb)

        # ---- checks: same counts and sums as MongoDB, else roll back
        problems = []
        for coll, table, _ in COPIES:
            m = mdb[coll].count_documents({})
            p = cur.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if m != p:
                problems.append(f"{table}: mongo {m} vs pg {p}")
        for coll, field, table, col in SUMS:
            m = next(mdb[coll].aggregate([{"$group": {"_id": None, "s": {"$sum": f"${field}"}}}]), {"s": 0})["s"]
            p = cur.execute(f"SELECT coalesce(sum({col}), 0) FROM {table}").fetchone()[0]
            if round(m, 2) != round(p, 2):
                problems.append(f"sum {table}.{col}: mongo {m:.2f} vs pg {p:.2f}")
        missing = [p for (p,) in cur.execute("SELECT path FROM raw_files")
                   if not (RAW_ROOT / p).is_file()]
        if missing:
            problems.append(f"{len(missing)} manifest files missing under data/raw, e.g. {missing[0]}")

        details = {"counts": counts, "problems": problems, "mongo_db": MONGO_DB}
        if problems:
            conn.rollback()
            print("FAILED, nothing written:", *problems, sep="\n  ")
            sys.exit(1)
        cur.execute(
            "INSERT INTO runs (run_id, pipeline, finished_at, status, details)"
            " VALUES ('migration-mongo-to-pg', 'migration', now(), 'ok', %s)", (jsonb(details),))

    for t, n in counts.items():
        print(f"{n:6}  {t}")
    print("ok: counts and sums match MongoDB")


if __name__ == "__main__":
    main()
