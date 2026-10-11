"""Create the indexes from docs/mongodb_schema.md section 4.

    python scripts/mongo_indexes.py [--db NAME]

Safe to re-run: create_index is a no-op when the index already exists.
"""

import argparse

from pymongo import ASCENDING as ASC, DESCENDING as DESC, TEXT

from mongo_common import get_db

INDEXES = {
    "legislation_history": [
        ([("bill_id", ASC), ("snapshot_date", DESC)], {}),
        ([("snapshot_date", DESC), ("changed_since_prev", ASC)], {}),
        ([("bill_id", ASC), ("legiscan_change_hash", ASC)], {}),
    ],
    "legislation": [
        ([("bill_id", ASC)], {"unique": True, "partialFilterExpression": {"bill_id": {"$type": "string"}}}),
        ([("track", ASC), ("event_date", DESC)], {}),
        ([("item_name", TEXT), ("raw.description", TEXT)], {}),
    ],
    "rulemakings": [
        ([("identifier", ASC)], {"unique": True, "partialFilterExpression": {"identifier": {"$type": "string"}}}),
        ([("source", ASC), ("last_seen", DESC)], {}),
        ([("docket_id", ASC)], {}),
        ([("publication_date", DESC)], {}),
        ([("comments_close_on", ASC)], {}),
    ],
    "rulemaking_comments": [
        ([("comment_on_id", ASC)], {}),
        ([("docket_id", ASC)], {}),
        ([("detail_fetched_at", ASC), ("posted_date", DESC)], {}),  # pending comment details
    ],
    "contract_transactions": [
        ([("award_key", ASC), ("action_date", ASC)], {}),
        ([("action_date", DESC)], {}),
        ([("txn_key", ASC)], {"unique": True}),
    ],
    "contracts_weekly_transactions_all": [
        ([("action_date", DESC)], {}),
        ([("award_key", ASC)], {}),
    ],
    "contracts": [
        ([("last_modified_date", DESC)], {}),
        ([("lookup_status", ASC)], {"partialFilterExpression": {"lookup_status": "pending"}}),
        ([("award_base_date", DESC)], {}),
    ],
    "contracts_past": [
        ([("award_kind", ASC)], {}),
    ],
    "contracts_past_transactions": [
        ([("action_date_fiscal_year", ASC), ("action_date", ASC)], {}),
        ([("award_key", ASC)], {}),
        ([("txn_key", ASC)], {"unique": True}),
    ],
}


def create_indexes(db, collections=None):
    created = []
    for name, specs in INDEXES.items():
        if collections and name not in collections:
            continue
        for keys, opts in specs:
            created.append(f"{name}.{db[name].create_index(keys, **opts)}")
    return created


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    args = p.parse_args()
    for name in create_indexes(get_db(args.db)):
        print(name)


if __name__ == "__main__":
    main()
