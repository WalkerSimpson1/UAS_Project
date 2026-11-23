"""Weekly LegiScan load into bill_snapshots + bills (Postgres; MongoDB version in git history).

Calls getDatasetList, and getDatasetRaw only when that dataset_hash isn't in
raw_files yet (LegiScan asks clients to do this):

    .venv/Scripts/python scripts/legiscan_loader.py --download

A zip from elsewhere is copied into $RAW_ROOT/legiscan/<dataset_date>/ and loaded:

    .venv/Scripts/python scripts/legiscan_loader.py --zip path/to/getDatasetRaw2026-10-11.zip

Safe to re-run: snapshots are keyed on (bill_id, snapshot_date) and never
overwritten. A dataset an earlier run already loaded is skipped (LegiScan
didn't refresh) unless --reload. This loader only writes source tables;
scripts/sync_policies.py turns new and changed bills into policy events.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import zipfile
from pathlib import Path

import requests

from pg_common import RawStore, connect, finish_run, jsonb, redact, sha256_file, start_run, to_date

LEGISCAN_URL = "https://api.legiscan.com/"
KEYWORDS = ["uas", "drone", "unmanned", "uav"]
ZIP_NAME_RE = re.compile(r"getDatasetRaw(\d{4}-\d{2}-\d{2})\.zip$")
# Bills with drone provisions whose title and description don't say so
# (bill_number as LegiScan writes it). The FY2027 NDAA: House and Senate.
WATCH_BILLS = {"HB8800", "SB4784"}
# Must-pass defense bills carry drone provisions without saying so in the title (P23).
# The bills themselves, not ones that amend an earlier NDAA ("To amend the National Defense ...").
WATCH_TITLE_RE = re.compile(r"^(?!(a bill )?to (amend|make)\b).*(national defense authorization act for fiscal year"
                            r"|department of defense appropriations act,) (19|20)\d\d", re.IGNORECASE)


# ---------------------------------------------------------------- reading zips

def read_bills(zip_path) -> list[dict]:
    with zipfile.ZipFile(zip_path) as z:
        return [json.loads(z.read(n))["bill"] for n in z.namelist() if "/bill/" in n]


def is_uas(bill: dict) -> bool:
    """Substring match on title + description (unchanged from the Mongo loader), plus WATCH_BILLS."""
    if bill.get("bill_number") in WATCH_BILLS or WATCH_TITLE_RE.search(bill.get("title") or ""):
        return True
    text = (bill["title"] + " " + (bill.get("description") or "")).lower()
    return any(word in text for word in KEYWORDS)


def bill_columns(raw: dict) -> dict:
    """bills columns taken from a LegiScan bill object."""
    return {
        "state": raw.get("state"),
        "session_id": raw.get("session_id") or (raw.get("session") or {}).get("session_id"),
        "identifier": raw.get("bill_number"),
        "title": raw["title"],
        "description": raw.get("description"),
        "status": raw.get("status"),
        "status_date": to_date(raw.get("status_date")),
        "url": raw.get("url"),
        "state_link": raw.get("state_link"),
    }


# ---------------------------------------------------------------- writes

def write_snapshots(conn, bills: list[dict], snapshot_date, dataset_date, raw_source) -> dict:
    """Insert this run's snapshots, then refresh the bills rows."""
    ids = [str(b["bill_id"]) for b in bills]
    prev = dict(conn.execute(
        "SELECT DISTINCT ON (bill_id) bill_id, change_hash FROM bill_snapshots"
        " WHERE bill_id = ANY(%s) AND snapshot_date < %s ORDER BY bill_id, snapshot_date DESC",
        (ids, snapshot_date)).fetchall())

    inserted = 0
    for b in bills:
        bid = str(b["bill_id"])
        cur = conn.execute(
            "INSERT INTO bill_snapshots (bill_id, snapshot_date, dataset_date, change_hash,"
            " changed_since_prev, raw, raw_source) VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT DO NOTHING",
            (bid, snapshot_date, dataset_date, b["change_hash"],
             prev.get(bid) != b["change_hash"], jsonb(b), raw_source))
        inserted += cur.rowcount

    # Use what's stored, so a same-day re-run sees the original snapshot.
    stored = conn.execute(
        "SELECT bill_id, change_hash, changed_since_prev, raw, raw_source FROM bill_snapshots"
        " WHERE bill_id = ANY(%s) AND snapshot_date = %s", (ids, snapshot_date)).fetchall()
    existing = {r[0]: r[1:] for r in conn.execute(
        "SELECT bill_id, first_seen, last_seen, change_hash FROM bills WHERE bill_id = ANY(%s)", (ids,))}

    new = changed = 0
    for bid, change_hash, _, raw, raw_source_ in stored:
        cols = bill_columns(raw)
        if bid not in existing:
            new += 1
            conn.execute(
                "INSERT INTO bills (bill_id, state, session_id, identifier, title, description, status,"
                " status_date, url, state_link, change_hash, raw, raw_source, first_seen, last_seen,"
                " last_changed) VALUES (%(bill_id)s, %(state)s, %(session_id)s, %(identifier)s,"
                " %(title)s, %(description)s, %(status)s, %(status_date)s, %(url)s, %(state_link)s,"
                " %(change_hash)s, %(raw)s, %(raw_source)s, %(d)s, %(d)s, %(d)s)",
                {**cols, "bill_id": bid, "change_hash": change_hash, "raw": jsonb(raw),
                 "raw_source": raw_source_, "d": snapshot_date})
            continue
        first_seen, last_seen, old_hash = existing[bid]
        conn.execute("UPDATE bills SET first_seen = least(first_seen, %s), last_seen = greatest(last_seen, %s)"
                     " WHERE bill_id = %s", (snapshot_date, snapshot_date, bid))
        if snapshot_date >= last_seen and change_hash != old_hash:
            changed += 1
            conn.execute(
                "UPDATE bills SET state = %(state)s, session_id = %(session_id)s, identifier = %(identifier)s,"
                " title = %(title)s, description = %(description)s, status = %(status)s,"
                " status_date = %(status_date)s, url = %(url)s, state_link = %(state_link)s,"
                " change_hash = %(change_hash)s, raw = %(raw)s, raw_source = %(raw_source)s,"
                " last_changed = %(d)s WHERE bill_id = %(bill_id)s",
                {**cols, "bill_id": bid, "change_hash": change_hash, "raw": jsonb(raw),
                 "raw_source": raw_source_, "d": snapshot_date})

    return {"bills": len(bills), "snapshots_inserted": inserted,
            "changed_since_prev": sum(1 for r in stored if r[2]), "new_bills": new, "updated_bills": changed}


# ---------------------------------------------------------------- raw files

def zip_entries(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT path, sha256, extra FROM raw_files WHERE source = 'legiscan' AND path LIKE '%%.zip'").fetchall()
    return [{"path": p, "sha256": s, **(e or {})} for p, s, e in rows]


def import_zip(conn, store: RawStore, zip_path) -> tuple[str, str]:
    m = ZIP_NAME_RE.search(str(zip_path))
    if not m:
        raise ValueError(f"can't read dataset_date from zip name: {zip_path}")
    dataset_date, sha = m.group(1), sha256_file(zip_path)
    for e in zip_entries(conn):
        if e["sha256"] == sha:
            return e["path"], dataset_date
    rel = f"{store.folder('legiscan', dataset_date)}/getDatasetRaw{dataset_date}.zip"
    shutil.copyfile(zip_path, store.root / rel)
    store.record(rel, "legiscan", "getDatasetRaw", dataset_date=dataset_date, dataset_hash=None)
    return rel, dataset_date


def legiscan_get(params: dict, timeout: int) -> requests.Response:
    """requests' errors include the URL (API key, access_key): re-raise without it."""
    try:
        resp = requests.get(LEGISCAN_URL, params=params, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise RuntimeError(f"LegiScan {params.get('op')} failed: {redact(e)}") from None
    return resp


def download(conn, store: RawStore, api_key: str) -> tuple[str, str]:
    listing = legiscan_get({"key": api_key, "op": "getDatasetList", "state": "US"}, timeout=60).json()
    if listing.get("status") != "OK":
        raise RuntimeError(f"getDatasetList failed: {redact(listing)}")
    ds = listing["datasetlist"][0]
    dataset_date, dataset_hash = ds["dataset_date"], ds["dataset_hash"]

    for e in zip_entries(conn):
        if e.get("dataset_hash") == dataset_hash or (
                e.get("dataset_hash") is None and e.get("dataset_date") == dataset_date):
            print(f"dataset {dataset_date} already downloaded: {e['path']}")
            return e["path"], dataset_date

    listing["datasetlist"] = [{k: v for k, v in d.items() if k != "access_key"} for d in listing["datasetlist"]]
    store.write_json("legiscan", dataset_date, "getDatasetList.json", listing, "legiscan")
    resp = legiscan_get({"key": api_key, "op": "getDatasetRaw", "id": ds["session_id"],
                         "access_key": ds["access_key"]}, timeout=300)
    rel = store.write_bytes("legiscan", dataset_date, f"getDatasetRaw{dataset_date}.zip", resp.content,
                            "legiscan", dataset_date=dataset_date, dataset_hash=dataset_hash,
                            session_id=ds["session_id"])
    return rel, dataset_date


# ---------------------------------------------------------------- run

def ingest(conn, zip_path=None, snapshot_date: dt.date | None = None, reload=False,
           run_tag=None, parent_id=None) -> dict:
    """Load one dataset (the given zip, or a download). Returns {status, details}."""
    snapshot_date = snapshot_date or dt.date.today()
    run_id = f"legiscan-{snapshot_date}"
    rid = start_run(conn, "legiscan", run_id, run_tag, parent_id)
    try:
        store = RawStore(run_id)
        if zip_path:
            zip_rel, dataset_date = import_zip(conn, store, zip_path)
        else:
            key = os.environ.get("LEGI_API_KEY")
            if not key:
                raise RuntimeError("LEGI_API_KEY is not set in .env")
            zip_rel, dataset_date = download(conn, store, key)

        details = {"dataset_date": dataset_date, "raw_folder": zip_rel.rsplit("/", 1)[0]}
        loaded_on = None if reload else conn.execute(
            "SELECT max(snapshot_date) FROM bill_snapshots WHERE dataset_date = %s AND snapshot_date <> %s",
            (dataset_date, snapshot_date)).fetchone()[0]
        if loaded_on:
            status, details["skipped"] = "skipped", f"dataset {dataset_date} already loaded on {loaded_on}"
        else:
            bills = [b for b in read_bills(store.root / zip_rel) if is_uas(b)]
            details["steps"] = write_snapshots(conn, bills, snapshot_date, dataset_date, zip_rel)
            status = "ok"
        conn.commit()
    except Exception as e:
        conn.rollback()
        finish_run(conn, rid, "failed", {}, [str(e)])
        raise
    finish_run(conn, rid, status, details)
    return {"status": status, **details}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--zip", type=Path, help="load an already-downloaded getDatasetRaw<date>.zip")
    src.add_argument("--download", action="store_true", help="fetch from the LegiScan API")
    p.add_argument("--snapshot-date", type=dt.date.fromisoformat, default=dt.date.today())
    p.add_argument("--reload", action="store_true", help="load even if an earlier run loaded this dataset")
    args = p.parse_args()
    with connect() as conn:
        print(json.dumps(ingest(conn, args.zip, args.snapshot_date, args.reload), indent=1, default=str))


if __name__ == "__main__":
    main()
