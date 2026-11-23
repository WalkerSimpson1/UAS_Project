"""One-off backfills for data the weekly run doesn't reach (docs/DECISIONS.md P19).

    .venv/Scripts/python scripts/backfill.py fr --start 2025-01-01 --end 2026-09-26
    .venv/Scripts/python scripts/backfill.py regs --start 2025-01-01 --end 2026-09-26 [--max-details 200]
    .venv/Scripts/python scripts/backfill.py contracts --fy 2026

fr         Federal Register search in 3-month chunks (the API caps one search's results).
regs       Regulations.gov dockets of the FR documents the fr step saw today. Comment
           details beyond --max-details are left for the weekly runs (300 per run).
contracts  USASpending bulk download (Advanced Search "download", PSC 1550, contracts
           and IDVs) for a fiscal year, upserted into past_awards / past_transactions.
           Re-run every few months: DoD posts ~90 days late, so a recent FY keeps growing.

Then run scripts/sync_policies.py. Documents posted 30+ days before they were first seen
become policies and sources without events, so a backfill doesn't flood the dashboard.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import time
import zipfile

import requests

import federal_register_loader
import regulations_loader
from pg_common import RAW_ROOT, connect, finish_run, jsonb, start_run, to_date

API = "https://api.usaspending.gov/api/v2"
CONTRACT_TYPES = ["A", "B", "C", "D"]
IDV_TYPES = ["IDV_A", "IDV_B", "IDV_B_A", "IDV_B_B", "IDV_B_C", "IDV_C", "IDV_D", "IDV_E"]


def chunks(start: dt.date, end: dt.date, months=3):
    s = start
    while s <= end:
        y, m = divmod(s.month - 1 + months, 12)
        nxt = dt.date(s.year + y, m + 1, 1)
        yield s, min(nxt - dt.timedelta(days=1), end)
        s = nxt


def backfill_fr(conn, start, end) -> None:
    for s, e in chunks(start, end):
        r = federal_register_loader.ingest(conn, s, e, run_tag="backfill")
        print(f"FR {s}..{e}: returned {r['steps']['search']['returned']}, kept {r['upserted']}, new {r['new']}")


def backfill_regs(conn, start, end, max_details) -> None:
    r = regulations_loader.ingest(conn, start, end, run_tag="backfill", max_details=max_details)
    print(json.dumps({k: r[k] for k in ("status", "requests", "new", "steps")}, indent=1, default=str))


# ---------------------------------------------------------------- contracts bulk

def download(kind: str, fy: int, folder) -> bytes:
    """Request an Advanced Search download, wait for it, save the zip under RAW_ROOT."""
    body = {"filters": {"psc_codes": ["1550"], "award_type_codes": CONTRACT_TYPES + IDV_TYPES,
                        "time_period": [{"start_date": f"{fy - 1}-10-01", "end_date": f"{fy}-09-30"}]},
            "file_format": "csv"}
    resp = requests.post(f"{API}/download/{kind}/", json=body, timeout=120)
    if resp.status_code != 200:
        raise RuntimeError(f"download/{kind} -> {resp.status_code}: {resp.text[:500]}")
    job = resp.json()
    print(f"{kind}: requested {job['file_name']}")
    for _ in range(180):  # up to 30 minutes
        time.sleep(10)
        st = requests.get(f"{API}/download/status/", params={"file_name": job["file_name"]}, timeout=60).json()
        if st.get("status") == "finished":
            break
        if st.get("status") == "failed":
            raise RuntimeError(f"download failed: {st.get('message')}")
    else:
        raise TimeoutError(f"{kind} download not ready after 30 minutes")
    content = requests.get(job["file_url"], timeout=600).content
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{kind}_request.json").write_text(json.dumps(body, indent=1), encoding="utf-8")
    (folder / f"{kind}.zip").write_bytes(content)
    return content


def contract_rows(content: bytes):
    """Rows of the Contracts_Prime* CSVs in a download zip (subaward and assistance files are skipped)."""
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        for name in z.namelist():
            if name.startswith("Contracts_Prime") and name.endswith(".csv"):
                with z.open(name) as f:
                    yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def nn(v):
    return v if v not in ("", None) else None


def backfill_contracts(conn, fy: int) -> None:
    folder = RAW_ROOT / "usaspending" / "bulk" / f"FY{fy}_{dt.date.today()}"
    rid = start_run(conn, "usaspending", f"usaspending-bulk-FY{fy}-{dt.date.today()}", "backfill")
    src = f"usaspending/bulk/{folder.name}"
    awards = {}
    for r in contract_rows(download("awards", fy, folder)):
        k = r["contract_award_unique_key"]
        if k not in awards or (r.get("award_latest_action_date") or "") >= (awards[k].get("award_latest_action_date") or ""):
            awards[k] = r
    for k, r in awards.items():
        conn.execute(
            "INSERT INTO past_awards (award_key, award_kind, recipient_name, recipient_uei, awarding_agency_name,"
            " awarding_sub_agency_name, total_obligated_amount, award_base_action_date, award_latest_action_date,"
            " description, row, source_file) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (award_key) DO UPDATE SET award_kind = EXCLUDED.award_kind,"
            " recipient_name = EXCLUDED.recipient_name, recipient_uei = EXCLUDED.recipient_uei,"
            " awarding_agency_name = EXCLUDED.awarding_agency_name,"
            " awarding_sub_agency_name = EXCLUDED.awarding_sub_agency_name,"
            " total_obligated_amount = EXCLUDED.total_obligated_amount,"
            " award_base_action_date = EXCLUDED.award_base_action_date,"
            " award_latest_action_date = EXCLUDED.award_latest_action_date, description = EXCLUDED.description,"
            " row = EXCLUDED.row, source_file = EXCLUDED.source_file",
            (k, k.split("_")[1] if k.startswith("CONT_") else None, nn(r.get("recipient_name")),
             nn(r.get("recipient_uei")), nn(r.get("awarding_agency_name")), nn(r.get("awarding_sub_agency_name")),
             num(r.get("total_obligated_amount")), to_date(r.get("award_base_action_date")),
             to_date(r.get("award_latest_action_date")), nn(r.get("prime_award_base_transaction_description")),
             jsonb(r), f"{src}/awards.zip"))
    txns = 0
    for r in contract_rows(download("transactions", fy, folder)):
        txns += 1
        conn.execute(
            "INSERT INTO past_transactions (txn_key, award_key, action_date, action_date_fiscal_year,"
            " federal_action_obligation, recipient_name, awarding_agency_name, extent_competed, type_of_set_aside,"
            " country_of_product_or_service_origin, row, source_file) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (txn_key) DO UPDATE SET award_key = EXCLUDED.award_key, action_date = EXCLUDED.action_date,"
            " action_date_fiscal_year = EXCLUDED.action_date_fiscal_year,"
            " federal_action_obligation = EXCLUDED.federal_action_obligation,"
            " recipient_name = EXCLUDED.recipient_name, awarding_agency_name = EXCLUDED.awarding_agency_name,"
            " extent_competed = EXCLUDED.extent_competed, type_of_set_aside = EXCLUDED.type_of_set_aside,"
            " country_of_product_or_service_origin = EXCLUDED.country_of_product_or_service_origin,"
            " row = EXCLUDED.row, source_file = EXCLUDED.source_file",
            (r["contract_transaction_unique_key"], r["contract_award_unique_key"], to_date(r.get("action_date")),
             int(r["action_date_fiscal_year"]) if r.get("action_date_fiscal_year") else fy,
             num(r.get("federal_action_obligation")), nn(r.get("recipient_name")), nn(r.get("awarding_agency_name")),
             nn(r.get("extent_competed")), nn(r.get("type_of_set_aside")),
             nn(r.get("country_of_product_or_service_origin")), jsonb(r), f"{src}/transactions.zip"))
    conn.commit()
    details = {"fiscal_year": fy, "awards": len(awards), "transactions": txns, "folder": src}
    finish_run(conn, rid, "ok", details)
    print(details)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("fr", "regs"):
        s = sub.add_parser(name)
        s.add_argument("--start", type=dt.date.fromisoformat, required=True)
        s.add_argument("--end", type=dt.date.fromisoformat, required=True)
        if name == "regs":
            s.add_argument("--max-details", type=int, default=200)
    c = sub.add_parser("contracts")
    c.add_argument("--fy", type=int, required=True)
    args = p.parse_args()
    with connect() as conn:
        if args.cmd == "fr":
            backfill_fr(conn, args.start, args.end)
        elif args.cmd == "regs":
            backfill_regs(conn, args.start, args.end, args.max_details)
        else:
            backfill_contracts(conn, args.fy)


if __name__ == "__main__":
    main()
