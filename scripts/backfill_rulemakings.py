"""Backfill rulemakings over a wider publication_date window (section 3.3).

    python scripts/backfill_rulemakings.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
                                           [--snapshot NAME] [--no-snapshot-check] [--db NAME]

Runs every loader in weekly_ingest.RULEMAKING_LOADERS over [start, end]
(default: 14 days back from today through today), each logging its own `runs`
document, plus one `runs` document (pipeline "backfill_rulemakings") that ties
them together.

This runs outside weekly_run.sh, so nothing snapshots ZFS for it. Take one
atomic snapshot of both datasets first:

    zfs snapshot uaspool/mongo@pre-rulemakings-backfill uaspool/raw@pre-rulemakings-backfill

The script refuses to start unless both exist (--snapshot names another,
--no-snapshot-check skips the check, e.g. against a test database).

Every write is an upsert on _id, so overlapping a weekly run's window is safe:
the overlap rewrites the same documents and first_seen is kept.
"""

import argparse
import subprocess
import sys
import traceback
from datetime import date, datetime, timezone

from mongo_common import days_ago, get_db, redact
from weekly_ingest import EXIT_CODES, outcome, run_rulemakings

DATASETS = ("uaspool/mongo", "uaspool/raw")
DEFAULT_SNAPSHOT = "pre-rulemakings-backfill"
BACKFILL_DAYS = 14


def missing_snapshots(name):
    missing = []
    for dataset in DATASETS:
        r = subprocess.run(["zfs", "list", "-H", "-t", "snapshot", "-o", "name", f"{dataset}@{name}"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            missing.append(f"{dataset}@{name}")
    return missing


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=date.fromisoformat,
                   help=f"first publication_date (default: {BACKFILL_DAYS} days before --end)")
    p.add_argument("--end", type=date.fromisoformat, help="last publication_date (default: today)")
    p.add_argument("--snapshot", default=DEFAULT_SNAPSHOT, help=f"ZFS snapshot to require (default: {DEFAULT_SNAPSHOT})")
    p.add_argument("--no-snapshot-check", action="store_true", help="don't require the ZFS snapshot")
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    args = p.parse_args()

    today = date.today()
    end = args.end or today
    start = args.start or days_ago(BACKFILL_DAYS, end)
    if start > end:
        p.error(f"--start {start} is after --end {end}")

    if not args.no_snapshot_check:
        missing = missing_snapshots(args.snapshot)
        if missing:
            print(f"missing ZFS snapshot(s): {', '.join(missing)}. Take them first:\n"
                  f"  zfs snapshot {' '.join(f'{d}@{args.snapshot}' for d in DATASETS)}", file=sys.stderr)
            return 1

    run_tag = f"backfill-rulemakings-{datetime.now():%Y-%m-%d_%H%M%S}"
    db = get_db(args.db)
    parent_id = db.runs.insert_one({
        "run_id": run_tag,
        "pipeline": "backfill_rulemakings",
        "run_tag": run_tag,
        "zfs_snapshot_pre": None if args.no_snapshot_check else args.snapshot,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "started_at": datetime.now(timezone.utc),
        "status": "running",
    }).inserted_id
    children, errors = {}, []

    def fail(name, e):
        errors.append(redact(f"{name}: {type(e).__name__}: {e}"))
        print(redact(traceback.format_exc()), file=sys.stderr)

    run_rulemakings(db, children, fail, run_tag, run_date=today, start=start, end=end)

    status = outcome(errors, children)
    db.runs.update_one({"_id": parent_id}, {"$set": {
        "finished_at": datetime.now(timezone.utc),
        "status": status,
        "children": children,
        "errors": errors,
    }})
    print(f"backfill_rulemakings {start}..{end}: {status}")
    for e in errors:
        print(f"  {e}", file=sys.stderr)
    return EXIT_CODES[status]


if __name__ == "__main__":
    sys.exit(main())
