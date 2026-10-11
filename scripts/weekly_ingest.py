"""Weekly MongoDB ingest, called by weekly_run.sh after the SQLite notebooks.

    python scripts/weekly_ingest.py [--db NAME] [--legiscan-download]
                                    [--no-award-unique-id-test] [--no-coverage-pull]
                                    [--backfill-method auto|award_unique_id|award_ids_exact]

Runs legiscan_loader, usaspending_loader, then each rulemaking loader in
RULEMAKING_LOADERS (federal_register_loader), each of which logs its own
`runs` document, and writes one more `runs` document (pipeline
"weekly_ingest") that ties them together.

Environment (set by weekly_run.sh; both optional for a manual run):
    UAS_RUN_TAG           run tag, e.g. weekly-2026-10-11 (default: manual-<timestamp>)
    UAS_ZFS_SNAPSHOT_PRE  name of the -pre ZFS snapshot, recorded in runs

LegiScan, parallel run (default): load the newest zip in
legiscan_getDatasetRaw_data/, the same one LegiScan2.ipynb reads. A dataset
an earlier run already loaded is skipped, matching weekly_run.sh's LegiScan2
check. After cut-over pass --legiscan-download.

The section 9.1 award_unique_id test runs before the backfill unless
--no-award-unique-id-test (the doc runs it every parallel-run week).

Exit status: 0 when every pipeline finishes ok (a skipped LegiScan dataset
is ok), 1 otherwise. USASpending "partial" counts as a failure, so
weekly_run.sh rolls the run back; re-running is safe because every write
is an upsert.
"""

import argparse
import os
import sys
import traceback
from datetime import date, datetime, timezone

import federal_register_loader
import legiscan_loader
import usaspending_loader
from mongo_common import LEGACY_ZIP_DIR, get_db, redact

OK_STATUSES = {"ok", "skipped"}

# Rulemaking sources, run in this order by weekly_ingest and
# backfill_rulemakings. Each loader's ingest(db, start=None, end=None,
# run_date=None, run_tag=None) writes its own runs document and returns it
# with run_id, status, window_start, window_end, requests, upserted and new.
RULEMAKING_LOADERS = [
    ("federal_register", federal_register_loader),
    # >>> HOOK: regulations-api branch adds `import regulations_loader` above and this line:
    # ("regulations_gov", regulations_loader),
]
RULEMAKING_SUMMARY = ("window_start", "window_end", "requests", "upserted", "new")


def run_rulemakings(db, children, fail, run_tag, run_date=None, start=None, end=None):
    """Run every RULEMAKING_LOADERS source; record each in children."""
    for name, loader in RULEMAKING_LOADERS:
        try:
            run = loader.ingest(db, start, end, run_date=run_date, run_tag=run_tag)
            children[name] = {"run_doc": run["_id"], "run_id": run["run_id"], "status": run["status"],
                              **{k: run.get(k) for k in RULEMAKING_SUMMARY}}
            print(f"{name}: {run['status']} {run['window_start']}..{run['window_end']} "
                  f"requests={run['requests']} upserted={run['upserted']} new={run['new']}")
        except Exception as e:
            fail(name, e)
            children[name] = {"status": "failed"}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    p.add_argument("--legiscan-download", action="store_true",
                   help="fetch the dataset from the LegiScan API instead of using the newest zip")
    p.add_argument("--no-award-unique-id-test", action="store_true", help="skip the 9.1 test")
    p.add_argument("--no-coverage-pull", action="store_true", help="skip step 3b (D16)")
    p.add_argument("--backfill-method", choices=["auto", "award_unique_id", "award_ids_exact"], default="auto")
    args = p.parse_args()

    run_tag = os.environ.get("UAS_RUN_TAG") or f"manual-{datetime.now():%Y-%m-%d_%H%M%S}"
    zfs_pre = os.environ.get("UAS_ZFS_SNAPSHOT_PRE") or None
    today = date.today()
    db = get_db(args.db)

    parent_id = db.runs.insert_one({
        "run_id": run_tag,
        "pipeline": "weekly_ingest",
        "run_tag": run_tag,
        "zfs_snapshot_pre": zfs_pre,
        "started_at": datetime.now(timezone.utc),
        "status": "running",
    }).inserted_id
    children, errors = {}, []

    def fail(name, e):
        # Redacted: errors land in runs (and so in mongodump archives) and the log.
        errors.append(redact(f"{name}: {type(e).__name__}: {e}"))
        print(redact(traceback.format_exc()), file=sys.stderr)

    # The weekly loaders build on the migrated history; refuse to start without it.
    if not db.runs.find_one({"pipeline": "migration", "status": "ok"}):
        errors.append("no successful migration in runs; run scripts/migrate_sqlite_to_mongo.py first")
    else:
        try:
            zip_path = None if args.legiscan_download else legiscan_loader.newest_zip(LEGACY_ZIP_DIR)
            run = legiscan_loader.ingest(db, zip_path, today, run_tag=run_tag)
            children["legiscan"] = {"run_doc": run["_id"], "run_id": run["run_id"], "status": run["status"]}
            print(f"legiscan: {run['status']} {run['steps']}")
        except Exception as e:
            fail("legiscan", e)
            children["legiscan"] = {"status": "failed"}

        # Independent of LegiScan, so it runs even if LegiScan failed.
        try:
            run, status = usaspending_loader.weekly(
                db, today, args.backfill_method, not args.no_award_unique_id_test,
                not args.no_coverage_pull, zfs_pre, run_tag)
            children["usaspending"] = {"run_doc": run.run_doc_id, "run_id": run.run_id, "status": status}
            errors.extend(redact(f"usaspending {e}") for e in run.errors)
            summary = {k: {x: y for x, y in v.items() if isinstance(y, (int, bool, str))}
                       for k, v in run.steps.items()}
            print(f"usaspending: {status} {summary}")
        except Exception as e:
            fail("usaspending", e)
            children["usaspending"] = {"status": "failed"}

        run_rulemakings(db, children, fail, run_tag, run_date=today)

    # Without the migration no child runs, and errors is non-empty.
    ok = not errors and all(c["status"] in OK_STATUSES for c in children.values())
    status = "ok" if ok else "failed"
    db.runs.update_one({"_id": parent_id}, {"$set": {
        "finished_at": datetime.now(timezone.utc),
        "status": status,
        "children": children,
        "errors": errors,
    }})
    print(f"weekly_ingest {run_tag}: {status}")
    for e in errors:
        print(f"  {e}", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
