"""Load the FY2020-2025 bulk CSVs into contracts_past / contracts_past_transactions
(docs/mongodb_schema.md sections 3.5, 3.6, 8.4).

    python scripts/load_contracts_past.py [--db NAME]

One-time load: each collection is dropped and re-inserted. Awards are deduped
by contract_award_unique_key keeping the latest action, exactly as
USASpending_Past_Awards.ipynb does.
"""

import argparse
from collections import Counter

import pandas as pd

from mongo_common import REPO_ROOT, derive_award_key, get_db, to_datetime, to_float, valid_award_key
from mongo_indexes import create_indexes

YEARS = range(2020, 2026)
AWARDS_DIR = "usa_spending_old_data/base_awards"
TXNS_DIR = "usa_spending_old_data/transactions"

AWARD_COLUMNS = [
    "contract_award_unique_key", "parent_award_agency_name", "current_total_value_of_award",
    "total_obligated_amount", "award_base_action_date", "award_latest_action_date",
    "prime_award_base_transaction_description", "number_of_actions", "awarding_agency_name",
    "awarding_sub_agency_name", "funding_agency_name", "funding_sub_agency_name", "recipient_uei",
    "recipient_name", "recipient_state_code", "award_type",
]
TXN_COLUMNS = [
    "contract_transaction_unique_key", "contract_award_unique_key", "modification_number",
    "parent_award_id_piid", "federal_action_obligation", "current_total_value_of_award",
    "action_date", "action_date_fiscal_year", "last_modified_date", "awarding_agency_name",
    "awarding_sub_agency_name", "funding_agency_name", "funding_sub_agency_name", "recipient_uei",
    "recipient_name", "contracting_officers_determination_of_business_size", "recipient_parent_uei",
    "recipient_parent_name", "recipient_state_code", "primary_place_of_performance_state_code",
    "country_of_product_or_service_origin", "award_type", "action_type", "action_type_code",
    "type_of_contract_pricing", "transaction_description", "extent_competed",
    "number_of_offers_received", "type_of_set_aside",
]
FLOAT_COLUMNS = {
    "current_total_value_of_award", "total_obligated_amount", "number_of_actions",
    "federal_action_obligation", "number_of_offers_received",
}
INT_COLUMNS = {"action_date_fiscal_year"}
DATE_COLUMNS = {"award_base_action_date", "award_latest_action_date", "action_date", "last_modified_date"}


def read_csvs(folder, pattern, columns):
    dtypes = {c: str for c in columns if c not in FLOAT_COLUMNS | INT_COLUMNS}
    frames = []
    for year in YEARS:
        rel = f"{folder}/{pattern.format(year=year)}"
        df = pd.read_csv(REPO_ROOT / rel, low_memory=False, usecols=columns, dtype=dtypes)
        df["source_file"] = rel
        frames.append(df)
    return pd.concat(frames, ignore_index=True)


def clean(record):
    doc = {}
    for k, v in record.items():
        if isinstance(v, float) and v != v:
            v = None
        if k in DATE_COLUMNS:
            v = to_datetime(v)
        elif k in FLOAT_COLUMNS:
            v = to_float(v)
        elif k in INT_COLUMNS and v is not None:
            v = int(v)
        doc[k] = v
    return doc


def award_docs():
    awards = read_csvs(AWARDS_DIR, "psc1550_fy{year}.csv", AWARD_COLUMNS)
    # Same sort and dedup as USASpending_Past_Awards.ipynb (default, non-stable
    # sort), so the kept row per key matches the SQLite contracts_past table.
    latest = awards.sort_values("award_latest_action_date", ascending=True)
    latest = latest.drop_duplicates("contract_award_unique_key", keep="last")
    docs = []
    for record in latest.to_dict("records"):
        doc = clean(record)
        key = doc["contract_award_unique_key"]
        doc = {
            "_id": key,
            "contract_award_unique_key": key,
            "award_key": key,
            "award_kind": key.split("_")[1] if valid_award_key(key) else None,
            **{c: doc[c] for c in AWARD_COLUMNS[1:]},
            "source_file": doc["source_file"],
        }
        docs.append(doc)
    return docs


def transaction_docs():
    txns = read_csvs(TXNS_DIR, "psc1550_fy{year}_t.csv", TXN_COLUMNS)
    docs = []
    for record in txns.to_dict("records"):
        doc = clean(record)
        key = doc["contract_transaction_unique_key"]
        award_key = doc["contract_award_unique_key"]
        docs.append({
            "_id": key,
            "contract_transaction_unique_key": key,
            "txn_key": key,
            "contract_award_unique_key": award_key,
            "award_key": award_key,
            **{c: doc[c] for c in TXN_COLUMNS[2:]},
            "source_file": doc["source_file"],
        })
    return docs


def validate(awards, txns):
    """Section 5.3 rules 1-3 plus the orphan check from 3.6. Report, never drop."""
    award_ids = {d["_id"] for d in awards}
    bad_award_keys = [d["_id"] for d in awards if not valid_award_key(d["_id"])]
    bad_txn_award_keys = [d["_id"] for d in txns if not valid_award_key(d["award_key"])]
    prefixed_txn_keys = [d["_id"] for d in txns if d["txn_key"].startswith("CONT_TX_")]
    derive_mismatch = Counter()
    for d in txns:
        if d["award_key"].startswith("CONT_AWD_") and derive_award_key(d["txn_key"]) != d["award_key"]:
            derive_mismatch["CONT_AWD"] += 1
    orphans = sorted({d["award_key"] for d in txns} - award_ids)
    return {
        "awards": len(awards),
        "award_kinds": dict(Counter(d["award_kind"] for d in awards)),
        "transactions": len(txns),
        "invalid_award_keys": bad_award_keys,
        "invalid_txn_award_keys": bad_txn_award_keys,
        "txn_keys_with_prefix": prefixed_txn_keys,
        "derived_award_key_mismatches": sum(derive_mismatch.values()),
        "txn_award_keys_without_award": orphans,
    }


def load(db):
    awards = award_docs()
    txns = transaction_docs()
    report = validate(awards, txns)
    for name, docs in (("contracts_past", awards), ("contracts_past_transactions", txns)):
        db[name].drop()
        db[name].insert_many(docs, ordered=False)
    create_indexes(db, {"contracts_past", "contracts_past_transactions"})
    return report


def print_report(report):
    print(f"contracts_past: {report['awards']} docs {report['award_kinds']}")
    print(f"contracts_past_transactions: {report['transactions']} docs")
    print(f"  derived award_key mismatches (CONT_AWD_ only): {report['derived_award_key_mismatches']}")
    for label in ("invalid_award_keys", "invalid_txn_award_keys", "txn_keys_with_prefix"):
        if report[label]:
            print(f"  {label}: {len(report[label])} e.g. {report[label][:3]}")
    orphans = report["txn_award_keys_without_award"]
    print(f"  transaction award_keys with no contracts_past doc (kept): {len(orphans)}")
    for key in orphans:
        print(f"    {key}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    args = p.parse_args()
    print_report(load(get_db(args.db)))


if __name__ == "__main__":
    main()
