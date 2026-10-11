"""Weekly LegiScan load into legislation_history + legislation
(docs/mongodb_schema.md sections 3.1, 3.2, 8.1).

Weekly (since cut-over): call getDatasetList, and call getDatasetRaw only when
the dataset_hash isn't already in manifest.jsonl (LegiScan requires this):

    python scripts/legiscan_loader.py --download

A zip from elsewhere is copied into $RAW_ROOT/legiscan/<dataset_date>/
(write-once) and loaded:

    python scripts/legiscan_loader.py --zip path/to/getDatasetRaw2026-10-11.zip

Re-running on the same day changes nothing: snapshots are keyed on
(bill_id, snapshot_date) and written with $setOnInsert. A dataset an earlier
run already loaded is skipped (LegiScan didn't refresh) unless --reload.
"""

import argparse
import json
import os
import re
import shutil
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from pymongo import UpdateOne

from mongo_common import RawStore, bulk, day, get_db, iso_day, redact, sha256_file, to_datetime
from mongo_indexes import create_indexes

LEGISCAN_URL = "https://api.legiscan.com/"
KEYWORDS = ["uas", "drone", "unmanned", "uav"]
HAND_FIELDS = ["sequence", "track", "event_date", "event_description", "notes"]
# Pipeline-owned fields of the current view, refreshed when change_hash differs.
CONTENT_FIELDS = ["item_name", "identifier", "body", "source_url", "source_platform", "state"]
ZIP_NAME_RE = re.compile(r"getDatasetRaw(\d{4}-\d{2}-\d{2})\.zip$")


# ---------------------------------------------------------------- reading zips


def read_bills(zip_path):
    """All bill objects in a getDatasetRaw zip."""
    bills = []
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if "/bill/" in name:
                bills.append(json.loads(z.read(name))["bill"])
    return bills


def is_uas(bill):
    """Same keyword test as LegiScan2.ipynb."""
    title = bill["title"].lower()
    description = (bill.get("description") or "").lower()
    return any(word in title or word in description for word in KEYWORDS)


def uas_bills(zip_path):
    return [b for b in read_bills(zip_path) if is_uas(b)]


def change_hashes(zip_path):
    """{bill_id (str): change_hash} for every bill in a zip."""
    return {str(b["bill_id"]): b["change_hash"] for b in read_bills(zip_path)}


# ---------------------------------------------------------------- documents


def snapshot_doc(bill, snapshot_date, dataset_date, raw_source):
    """legislation_history document for one bill. Tracker fields use the same
    mapping as LegiScan2.ipynb; hand fields start null."""
    bill_id = str(bill["bill_id"])
    return {
        "_id": {"bill_id": bill_id, "snapshot_date": snapshot_date},
        "bill_id": bill_id,
        "snapshot_date": snapshot_date,
        "dataset_date": dataset_date,
        "session_id": bill.get("session_id") or (bill.get("session") or {}).get("session_id"),
        "state": bill.get("state"),
        "item_name": bill["title"],
        "identifier": bill.get("bill_number"),
        "body": None,
        "sequence": None,
        "event_date": None,
        "event_description": None,
        "track": None,
        "source_url": bill.get("state_link"),
        "source_platform": "LegiScan",
        "notes": None,
        "legiscan_change_hash": bill.get("change_hash"),
        "raw": bill,
        "raw_source": raw_source,
        "date_added": iso_day(snapshot_date),
    }


def previous_hashes(db, bill_ids, before):
    """{bill_id: change_hash} of each bill's newest snapshot before `before`."""
    pipeline = [
        {"$match": {"bill_id": {"$in": list(bill_ids)}, "snapshot_date": {"$lt": before}}},
        {"$sort": {"bill_id": 1, "snapshot_date": -1}},
        {"$group": {"_id": "$bill_id", "hash": {"$first": "$legiscan_change_hash"}}},
    ]
    return {d["_id"]: d["hash"] for d in db.legislation_history.aggregate(pipeline)}


def write_snapshots(db, docs):
    """Upsert one run's snapshots (all with the same snapshot_date) into
    legislation_history, then refresh the legislation current view (8.1)."""
    if not docs:
        return {"snapshots": {"bills": 0}, "current": {}}
    snapshot_date = docs[0]["snapshot_date"]
    assert all(d["snapshot_date"] == snapshot_date for d in docs)

    prev = previous_hashes(db, {d["bill_id"] for d in docs}, snapshot_date)
    ops = []
    for d in docs:
        d["changed_since_prev"] = prev.get(d["bill_id"]) != d["legiscan_change_hash"]
        fields = {k: v for k, v in d.items() if k != "_id"}
        ops.append(UpdateOne({"_id": d["_id"]}, {"$setOnInsert": fields}, upsert=True))
    history = bulk(db.legislation_history, ops)

    # Use what is actually stored, so a same-day re-run sees the original snapshot.
    stored = {d["bill_id"]: d for d in db.legislation_history.find(
        {"_id": {"$in": [d["_id"] for d in docs]}})}
    current_ids = [f"legiscan:{b}" for b in stored]
    existing = {d["_id"]: d for d in db.legislation.find({"_id": {"$in": current_ids}})}

    ops = []
    for bill_id, snap in stored.items():
        _id = f"legiscan:{bill_id}"
        cur = existing.get(_id)
        update = {"$max": {"last_seen": snapshot_date, "latest_snapshot": snapshot_date}}
        if cur is None:
            update["$setOnInsert"] = {
                "bill_id": bill_id,
                "first_seen": snapshot_date,
                "date_added": snap["date_added"],
                **{f: snap.get(f) for f in HAND_FIELDS},
            }
        elif snapshot_date < cur["first_seen"]:
            # Loading an older zip than any seen before (out of order).
            update["$set"] = {"first_seen": snapshot_date, "date_added": snap["date_added"]}
        is_newest = cur is None or snapshot_date >= cur["latest_snapshot"]
        if is_newest and (cur is None or cur.get("legiscan_change_hash") != snap["legiscan_change_hash"]):
            update.setdefault("$set", {}).update({
                **{f: snap.get(f) for f in CONTENT_FIELDS},
                "legiscan_change_hash": snap["legiscan_change_hash"],
                "raw": snap["raw"],
                "raw_source": snap["raw_source"],
                "last_changed": snapshot_date,
            })
        ops.append(UpdateOne({"_id": _id}, update, upsert=True))
    current = bulk(db.legislation, ops)

    changed = sum(1 for d in stored.values() if d["changed_since_prev"])
    return {
        "snapshots": {"bills": len(docs), **history, "changed_since_prev": changed},
        "current": current,
    }


# ---------------------------------------------------------------- raw files


def legiscan_zip_entries(store):
    return [e for e in store.manifest_entries() if e.get("source") == "legiscan" and e["path"].endswith(".zip")]


def import_zip(store, zip_path):
    """Copy a getDatasetRaw zip from elsewhere into $RAW_ROOT/legiscan/<date>/.
    Returns (relative path, dataset_date)."""
    m = ZIP_NAME_RE.search(str(zip_path))
    if not m:
        raise ValueError(f"can't read dataset_date from zip name: {zip_path}")
    dataset_date = m.group(1)
    sha = sha256_file(zip_path)
    for e in legiscan_zip_entries(store):
        if e["sha256"] == sha:
            return e["path"], dataset_date
    rel = f"{store.folder('legiscan', dataset_date)}/getDatasetRaw{dataset_date}.zip"
    shutil.copyfile(zip_path, store.root / rel)
    store.record(rel, "legiscan", "getDatasetRaw", dataset_date=dataset_date, dataset_hash=None)
    return rel, dataset_date


def legiscan_get(params, timeout):
    """GET the LegiScan API. requests' error messages contain the full URL,
    including the API key and access_key, so re-raise without it (and without
    the chained exception, which tracebacks would print)."""
    try:
        resp = requests.get(LEGISCAN_URL, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"LegiScan {params.get('op')} failed: {redact(e)}") from None
    return resp


def download(store, api_key):
    """getDatasetList, then getDatasetRaw only for an unseen dataset_hash.
    Returns (relative path, dataset_date)."""
    listing = legiscan_get({"key": api_key, "op": "getDatasetList", "state": "US"}, timeout=60).json()
    if listing.get("status") != "OK":
        raise RuntimeError(f"getDatasetList failed: {redact(listing)}")
    ds = listing["datasetlist"][0]  # same choice as LegiScan1.ipynb
    dataset_date, dataset_hash = ds["dataset_date"], ds["dataset_hash"]

    for e in legiscan_zip_entries(store):
        # A zip imported with --zip has no hash; match it by dataset_date.
        if e.get("dataset_hash") == dataset_hash or (
                e.get("dataset_hash") is None and e.get("dataset_date") == dataset_date):
            print(f"dataset {dataset_date} ({dataset_hash}) already downloaded: {e['path']}")
            return e["path"], dataset_date

    listing["datasetlist"] = [{k: v for k, v in d.items() if k != "access_key"} for d in listing["datasetlist"]]
    store.write_json("legiscan", dataset_date, "getDatasetList.json", listing, "legiscan")
    resp = legiscan_get({
        "key": api_key, "op": "getDatasetRaw", "id": ds["session_id"], "access_key": ds["access_key"],
    }, timeout=300)
    rel = f"{store.folder('legiscan', dataset_date)}/getDatasetRaw{dataset_date}.zip"
    with open(store.root / rel, "xb") as f:
        f.write(resp.content)
    store.record(rel, "legiscan", "getDatasetRaw", dataset_date=dataset_date,
                 dataset_hash=dataset_hash, session_id=ds["session_id"])
    return rel, dataset_date


# ---------------------------------------------------------------- run


def newest_zip(folder):
    """Newest getDatasetRaw<date>.zip in a folder, by the date in its name."""
    zips = [p for p in Path(folder).glob("getDatasetRaw*.zip") if ZIP_NAME_RE.search(p.name)]
    if not zips:
        raise FileNotFoundError(f"no getDatasetRaw<date>.zip in {folder}")
    return max(zips, key=lambda p: p.name)


def already_loaded(db, dataset_date, snapshot_date):
    """Snapshot date of an earlier run that loaded this dataset, or None.
    A same-day re-run doesn't count (its upserts are no-ops)."""
    prev = db.legislation_history.find_one(
        {"dataset_date": dataset_date, "snapshot_date": {"$ne": snapshot_date}},
        sort=[("snapshot_date", -1)])
    return prev["snapshot_date"] if prev else None


def ingest(db, zip_path=None, snapshot_date=None, reload=False, run_tag=None):
    """Load one LegiScan dataset: the given zip, or a download when zip_path
    is None. Skips a dataset an earlier run already loaded (the LegiScan
    dataset didn't refresh), as weekly_run.sh does for SQLite, unless reload.
    Writes a runs document and returns it."""
    snapshot_date = snapshot_date or date.today()
    started = datetime.now(timezone.utc)
    create_indexes(db, {"legislation_history", "legislation"})
    store = RawStore(f"legiscan-{snapshot_date}")
    if zip_path:
        zip_rel, dataset_date = import_zip(store, zip_path)
    else:
        api_key = os.getenv("LEGI_API_KEY")
        if not api_key:
            raise RuntimeError("LEGI_API_KEY is not set in .env")
        zip_rel, dataset_date = download(store, api_key)

    snap_dt, dataset_dt = day(snapshot_date), to_datetime(dataset_date)
    loaded_on = None if reload else already_loaded(db, dataset_dt, snap_dt)
    if loaded_on:
        status = "skipped"
        steps = {"skipped": f"dataset {dataset_date} already loaded on {loaded_on:%Y-%m-%d}"}
    else:
        status = "ok"
        bills = uas_bills(store.root / zip_rel)
        steps = write_snapshots(db, [snapshot_doc(b, snap_dt, dataset_dt, zip_rel) for b in bills])
    doc = {
        "run_id": store.run_id,
        "pipeline": "legiscan",
        "run_tag": run_tag,
        "started_at": started,
        "finished_at": datetime.now(timezone.utc),
        "status": status,
        "dataset_date": dataset_dt,
        "raw_folder": zip_rel.rsplit("/", 1)[0],
        "steps": steps,
    }
    doc["_id"] = db.runs.insert_one(doc).inserted_id
    return doc


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", help="load an already-downloaded getDatasetRaw<date>.zip")
    src.add_argument("--download", action="store_true", help="fetch from the LegiScan API")
    p.add_argument("--snapshot-date", type=date.fromisoformat, default=date.today(),
                   help="run date recorded as snapshot_date (default: today)")
    p.add_argument("--reload", action="store_true",
                   help="load the dataset even if an earlier run already loaded it")
    p.add_argument("--db", help="database name (default: MONGO_DB or uas_tracker)")
    args = p.parse_args()

    run = ingest(get_db(args.db), args.zip, args.snapshot_date, args.reload)
    print(f"dataset {run['dataset_date']:%Y-%m-%d} from {run['raw_folder']}, "
          f"snapshot {args.snapshot_date}: {run['status']}")
    print(json.dumps(run["steps"], indent=1))


if __name__ == "__main__":
    main()
