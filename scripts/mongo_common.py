"""Shared helpers for the MongoDB pipeline (docs/mongodb_schema.md).

Config comes from the repo's .env:
    MONGO_URI  (default mongodb://127.0.0.1:27017/)
    MONGO_DB   (default uas_tracker)
    RAW_ROOT   (default /uaspool/raw)
"""

import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

DEFAULT_DB = "uas_tracker"
SQLITE_PATH = REPO_ROOT / "UAS_Tracker.db"
LEGACY_ZIP_DIR = REPO_ROOT / "legiscan_getDatasetRaw_data"

AWARD_KEY_RE = re.compile(r"^CONT_(AWD|IDV)_")


def get_db(db_name=None):
    uri = os.getenv("MONGO_URI", "mongodb://127.0.0.1:27017/")
    client = MongoClient(uri, tz_aware=False)
    return client[db_name or os.getenv("MONGO_DB") or DEFAULT_DB]


def raw_root():
    return Path(os.getenv("RAW_ROOT", "/uaspool/raw"))


# ---------------------------------------------------------------- dates


def to_datetime(value):
    """Parse 'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS' (optionally with a UTC offset)
    or a date into a naive UTC datetime. Empty values return None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, float):  # pandas NaN
        return None
    s = str(value).strip()
    if not s:
        return None
    # bulk CSVs write offsets as "+00"; pad to "+00:00"
    if re.search(r"[+-]\d\d$", s):
        s += ":00"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def day(d=None):
    """Midnight datetime for a date (today by default). Used for first_seen /
    last_seen so a same-day re-run leaves documents unchanged."""
    d = d or date.today()
    return datetime(d.year, d.month, d.day)


def iso_day(dt):
    return dt.strftime("%Y-%m-%d") if dt else None


def days_ago(n, today=None):
    return (today or date.today()) - timedelta(days=n)


# ---------------------------------------------------------------- values


def to_float(value):
    """Money -> double (D4). Accepts numbers and numeric strings."""
    if value is None or value == "":
        return None
    f = float(value)
    return None if f != f else f  # NaN -> None


def psc_code_from(value):
    """The PSC field's shape is not documented (string or {code, description}).
    Return the code string, or None. Never filled from the search filter (D11)."""
    if value is None:
        return None
    if isinstance(value, dict):
        code = value.get("code")
        return str(code) if code is not None else None
    return str(value)


# ---------------------------------------------------------------- keys (section 5)


def txn_key_from_transaction_id(transaction_id):
    if transaction_id and transaction_id.startswith("CONT_TX_"):
        return transaction_id[len("CONT_TX_"):]
    return transaction_id


def valid_award_key(key):
    """Validation rule 1: CONT_(AWD|IDV)_ prefix and no lowercase letters."""
    return bool(key) and bool(AWARD_KEY_RE.match(key)) and key == key.upper()


def derive_award_key(txn_key):
    """<agency>_<parent agency>_<PIID>_<mod>_<parent PIID>_<txn#>
    -> CONT_AWD_<PIID>_<agency>_<parent PIID>_<parent agency>.
    Returns None if the key doesn't have six segments."""
    parts = txn_key.split("_") if txn_key else []
    if len(parts) != 6:
        return None
    agency, parent_agency, piid, _mod, parent_piid, _n = parts
    if parent_piid == "-NONE-":
        return f"CONT_AWD_{piid}_{agency}_-NONE-_-NONE-"
    return f"CONT_AWD_{piid}_{agency}_{parent_piid}_{parent_agency}"


def piid_from_award_key(award_key):
    """Second segment of CONT_AWD_<PIID>_<agency>_<parent PIID>_<parent agency>.
    rsplit so a PIID containing '_' survives."""
    body = award_key.split("_", 2)[2]
    return body.rsplit("_", 3)[0]


# ---------------------------------------------------------------- bulk writes


def bulk(collection, ops):
    """bulkWrite ordered=false; returns {inserted, modified, unchanged}."""
    if not ops:
        return {"inserted": 0, "modified": 0, "unchanged": 0}
    r = collection.bulk_write(ops, ordered=False)
    return {
        "inserted": r.upserted_count,
        "modified": r.modified_count,
        "unchanged": r.matched_count - r.modified_count,
    }


def add_counts(total, part):
    for k, v in part.items():
        total[k] = total.get(k, 0) + v
    return total


# ---------------------------------------------------------------- raw files (section 7)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class RawStore:
    """Write-once raw files under RAW_ROOT plus manifest.jsonl.

    Order per step: save raw file -> append manifest -> upsert into Mongo.
    One folder per endpoint per run: <endpoint>/<date>/, or <date>_2/ ... if
    a previous run already used that folder.
    """

    def __init__(self, run_id, root=None):
        self.root = Path(root) if root else raw_root()
        self.run_id = run_id
        self.manifest = self.root / "manifest.jsonl"
        self._folders = {}

    def folder(self, endpoint, folder_date):
        """Relative folder for this run, chosen once per endpoint."""
        if endpoint not in self._folders:
            base = f"{endpoint}/{folder_date}"
            rel, n = base, 1
            while (self.root / rel).exists():
                n += 1
                rel = f"{base}_{n}"
            (self.root / rel).mkdir(parents=True)
            self._folders[endpoint] = rel
        return self._folders[endpoint]

    def write_json(self, endpoint, folder_date, name, payload, source, record_count=None):
        rel = f"{self.folder(endpoint, folder_date)}/{name}"
        path = self.root / rel
        if path.exists():
            raise FileExistsError(f"raw files are write-once: {path}")
        with open(path, "x") as f:
            json.dump(payload, f, indent=1)
        self.record(rel, source, endpoint, record_count)
        return rel

    def record(self, rel, source, endpoint, record_count=None, **extra):
        entry = {
            "path": rel,
            "sha256": sha256_file(self.root / rel),
            "source": source,
            "endpoint": endpoint,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "record_count": record_count,
            "run_id": self.run_id,
            **extra,
        }
        with open(self.manifest, "a") as f:
            f.write(json.dumps(entry) + "\n")
        return entry

    def manifest_entries(self):
        if not self.manifest.exists():
            return []
        with open(self.manifest) as f:
            return [json.loads(line) for line in f if line.strip()]
