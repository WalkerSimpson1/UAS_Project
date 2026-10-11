"""CSV exports from MongoDB (docs/mongodb_schema.md sections 3.2, 6.3).

    python scripts/export_csvs.py --out-dir DIR [--db NAME]

Writes:
    Exported_SQL_Legislation_Table.csv   one row per snapshot (legislation_history),
                                         same columns as LegiScan2.ipynb
    Exported_SQL_Contracts_Table.csv     contracts with lookup_status "ok",
                                         same columns as USASpending1.ipynb
    Exported_Contract_Transactions.csv   contract_transactions (primary record)
    Exported_Legislation_Current.csv     legislation current view
    Exported_Rulemakings.csv             rulemakings, hand-entered rows only (D17)

--out-dir is required so a parallel-run export never overwrites the CSVs the
SQLite notebooks write to the repo root. After cut-over, pass the repo root.
"""

import argparse
from datetime import datetime
from pathlib import Path

import pandas as pd

from mongo_common import get_db


def fmt(value, with_time=False):
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S" if with_time else "%Y-%m-%d")
    return value


def frame(docs, columns, time_columns=()):
    rows = [{c: fmt(d.get(c), c in time_columns) for c in columns} for d in docs]
    return pd.DataFrame(rows, columns=columns)


LEGISLATION_COLUMNS = ["item_name", "identifier", "source_url", "source_platform", "date_added",
                       "legiscan_change_hash", "state", "bill_id"]
CONTRACT_COLUMNS = ["api_internal_id", "award_id", "recipient", "awarding_agency", "amount_usd",
                    "description", "award_base_date", "last_modified_date"]
TRANSACTION_COLUMNS = ["transaction_id", "award_key", "type", "action_type", "action_date", "description",
                       "modification_number", "federal_action_obligation", "discovered_via", "date_added"]
CURRENT_COLUMNS = ["bill_id", "identifier", "item_name", "body", "state", "source_url", "source_platform",
                   "legiscan_change_hash", "sequence", "track", "event_date", "event_description", "notes",
                   "date_added", "first_seen", "last_seen", "last_changed"]
RULEMAKING_COLUMNS = ["item_name", "identifier", "body", "sequence", "event_date", "event_description",
                      "statutory_deadline", "track", "source_url", "source_platform", "date_added", "notes"]


def export(db, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}

    # Ordered by (bill_id, snapshot_date): the order the SQLite export
    # (ORDER BY bill_id, ties in insert order) has always had, so the file's
    # git history continues without a one-time reshuffle. Hand-entered rows
    # (no snapshots) follow, as they would in SQLite's NULL-last text order.
    snaps = list(db.legislation_history.find({}, {"raw": 0}).sort([("bill_id", 1), ("snapshot_date", 1)]))
    hand = list(db.legislation.find({"bill_id": None}, {"raw": 0}).sort([("date_added", 1), ("_id", 1)]))
    written["Exported_SQL_Legislation_Table.csv"] = frame(snaps + hand, LEGISLATION_COLUMNS)

    contracts = db.contracts.find({"lookup_status": "ok"}, {"raw": 0}).sort("_id", 1)
    written["Exported_SQL_Contracts_Table.csv"] = frame(contracts, CONTRACT_COLUMNS, {"last_modified_date"})

    txns = db.contract_transactions.find({}, {"raw": 0}).sort([("action_date", 1), ("_id", 1)])
    written["Exported_Contract_Transactions.csv"] = frame(txns, TRANSACTION_COLUMNS)

    current = db.legislation.find({}, {"raw": 0}).sort([("bill_id", 1), ("date_added", 1), ("_id", 1)])
    written["Exported_Legislation_Current.csv"] = frame(current, CURRENT_COLUMNS)

    # Hand-entered rows only; API documents carry a source (D17).
    rules = db.rulemakings.find({"source": None}).sort([("date_added", 1), ("identifier", 1), ("_id", 1)])
    written["Exported_Rulemakings.csv"] = frame(rules, RULEMAKING_COLUMNS)

    for name, df in written.items():
        df.to_csv(out_dir / name, index=False)
    return {name: len(df) for name, df in written.items()}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out-dir", required=True, help="directory to write the CSVs into")
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    args = p.parse_args()
    for name, n in export(get_db(args.db), args.out_dir).items():
        print(f"{name}: {n} rows")


if __name__ == "__main__":
    main()
