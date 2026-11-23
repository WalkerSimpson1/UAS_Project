"""Shared config and helpers for the Postgres scripts (docs/DECISIONS.md P1-P4).

Settings come from .env at the repo root (see .env.example). Never print them.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb, set_json_dumps

REPO = Path(__file__).resolve().parent.parent
load_dotenv(REPO / ".env")

EASTERN = ZoneInfo("America/New_York")
RAW_ROOT = (REPO / os.environ.get("RAW_ROOT", "./data/raw")).resolve()


def _default(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    return str(o)  # e.g. bson ObjectId


set_json_dumps(lambda obj: json.dumps(obj, default=_default))


def connect(**kw) -> psycopg.Connection:
    url = os.environ.get("DATABASE_URL")
    if not url or "<password>" in url:
        raise SystemExit("DATABASE_URL is not set in .env")
    return psycopg.connect(url, **kw)


def jsonb(v):
    return None if v is None else Jsonb(v)


def to_date(v) -> dt.date | None:
    """Mongo UTC-midnight datetime, 'YYYY-MM-DD' string, or None -> date."""
    if v is None or v == "":
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:  # e.g. LegiScan '0000-00-00'
        return None


def to_ts(v) -> dt.datetime | None:
    """Datetime (naive = UTC) or ISO string -> aware datetime."""
    if v is None or v == "":
        return None
    if isinstance(v, str):
        v = dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
    if v.tzinfo is None:
        v = v.replace(tzinfo=dt.timezone.utc)
    return v


def eastern_midnight(v) -> dt.datetime | None:
    """A Federal Register date ('2026-10-30') as midnight US/Eastern (001_core.sql)."""
    d = to_date(v)
    return None if d is None else dt.datetime.combine(d, dt.time(), EASTERN)


# ---------------------------------------------------------------- secrets

_SECRET_PARAM_RE = re.compile(r"(?<![\w])(key|access_key|api_key)=[^&\s'\"]+")


def redact(text) -> str:
    """Strip API keys from a message (requests puts query params in error URLs)."""
    text = _SECRET_PARAM_RE.sub(r"\1=<redacted>", str(text))
    for name in ("LEGI_API_KEY", "REGULATIONS_API_KEY"):
        if os.environ.get(name):
            text = text.replace(os.environ[name], "<redacted>")
    return text


# ---------------------------------------------------------------- raw files (P2)

def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class RawStore:
    """Write-once raw files under RAW_ROOT, indexed in raw_files.

    Order per step: save the file, record it, then load it. Records go through
    a separate autocommit connection, so a loader's rollback never drops the
    row for a file that is already on disk. One folder per endpoint per run:
    <endpoint>/<date>/, or <date>_2/ ... if an earlier run used that folder.
    """

    def __init__(self, run_id: str, root: Path = RAW_ROOT):
        self.run_id, self.root = run_id, Path(root)
        self._conn: psycopg.Connection | None = None
        self._folders: dict[str, str] = {}

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = connect(autocommit=True)
        return self._conn

    @property
    def folders(self) -> dict[str, str]:
        return dict(self._folders)

    def folder(self, endpoint: str, folder_date) -> str:
        if endpoint not in self._folders:
            base = f"{endpoint}/{folder_date}"
            rel, n = base, 1
            while (self.root / rel).exists():
                n += 1
                rel = f"{base}_{n}"
            (self.root / rel).mkdir(parents=True)
            self._folders[endpoint] = rel
        return self._folders[endpoint]

    def write_json(self, endpoint, folder_date, name, payload, source, record_count=None) -> str:
        rel = f"{self.folder(endpoint, folder_date)}/{name}"
        with open(self.root / rel, "x", encoding="utf-8") as f:  # "x": write-once
            json.dump(payload, f, indent=1)
        self.record(rel, source, endpoint, record_count)
        return rel

    def write_bytes(self, endpoint, folder_date, name, content: bytes, source, **extra) -> str:
        rel = f"{self.folder(endpoint, folder_date)}/{name}"
        with open(self.root / rel, "xb") as f:
            f.write(content)
        self.record(rel, source, endpoint, **extra)
        return rel

    def record(self, rel, source, endpoint, record_count=None, **extra) -> None:
        self.conn.execute(
            "INSERT INTO raw_files (path, sha256, source, endpoint, fetched_at, record_count, run_id, extra)"
            " VALUES (%s, %s, %s, %s, now(), %s, %s, %s)",
            (rel, sha256_file(self.root / rel), source, endpoint, record_count, self.run_id, Jsonb(extra)))


# ---------------------------------------------------------------- run log

def start_run(conn, pipeline: str, run_id: str, run_tag=None, parent_id=None) -> int:
    rid = conn.execute(
        "INSERT INTO runs (run_id, pipeline, run_tag, parent_id) VALUES (%s, %s, %s, %s) RETURNING id",
        (run_id, pipeline, run_tag, parent_id)).fetchone()[0]
    conn.commit()
    return rid


def finish_run(conn, rid: int, status: str, details: dict, errors=()) -> None:
    conn.execute(
        "UPDATE runs SET finished_at = now(), status = %s, details = %s, errors = %s WHERE id = %s",
        (status, Jsonb(details), [redact(e) for e in errors], rid))
    conn.commit()


def fiscal_year(d: dt.date | None = None) -> int:
    """FY starts Oct 1: 2026-10-01 is FY2027."""
    d = d or dt.date.today()
    return d.year + (d.month >= 10)


# ---------------------------------------------------------------- writes

def upsert(conn, table: str, row: dict, key: tuple[str, ...], insert_only: tuple[str, ...] = (),
           keep_raw_source: bool = True) -> bool:
    """INSERT ... ON CONFLICT (key) DO UPDATE every column except key and
    insert_only (e.g. first_seen). Raw files are write-once, so a re-run saves a
    new file: with keep_raw_source, an unchanged raw payload keeps its old
    raw_source and the row is left as it was. Returns True if inserted."""
    cols = list(row)
    sets = []
    for c in cols:
        if c in key or c in insert_only:
            continue
        if c == "raw_source" and keep_raw_source and "raw" in row:
            sets.append(f"raw_source = CASE WHEN t.raw IS NOT DISTINCT FROM EXCLUDED.raw"
                        f" AND t.raw_source IS NOT NULL THEN t.raw_source ELSE EXCLUDED.raw_source END")
        else:
            sets.append(f"{c} = EXCLUDED.{c}")
    sql = (f"INSERT INTO {table} AS t ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
           f" ON CONFLICT ({', '.join(key)}) DO "
           + (f"UPDATE SET {', '.join(sets)}" if sets else "NOTHING")
           + " RETURNING (xmax = 0)")
    r = conn.execute(sql, [row[c] for c in cols]).fetchone()
    return bool(r and r[0])


def fy_start(d: dt.date | None = None) -> dt.date:
    """Oct 1 that starts the fiscal year containing d."""
    return dt.date(fiscal_year(d) - 1, 10, 1)


def read_sql(conn, sql: str, args=()):
    """Query -> pandas DataFrame (for the notebooks)."""
    import pandas as pd
    cur = conn.execute(sql, args)
    return pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])
