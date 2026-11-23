"""Weekly USASpending load, PSC 1550 (Postgres; MongoDB version in git history).

    .venv/Scripts/python scripts/usaspending_loader.py [--run-date YYYY-MM-DD] [--feed-days N]
                                                       [--backfill-method award_unique_id|award_ids_exact]
                                                       [--no-coverage-pull]

Steps, each in its own transaction (a failed step is rolled back, the rest still run):
  1 weekly_feed     spending_by_transaction, last FEED_DAYS days -> contract_activity_feed
  2 txn_history     /transactions/ for each award in the feed -> contract_transactions
  3 award_search    spending_by_award, current FY (>= 120 days back) -> contract_awards
  3b coverage_pull  /transactions/ for awards modified in the past 7 days that step 2 skipped
  4 backfill        look up awards that transactions point to but contract_awards lacks
  5 integrity       count references still unresolved
Every response is saved under $RAW_ROOT/usaspending/ first. Re-running is safe.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time

import requests

from pg_common import RawStore, connect, finish_run, fiscal_year, jsonb, start_run, to_date, to_ts, upsert

API = "https://api.usaspending.gov/api/v2"
AWARD_TYPES = ["A", "B", "C", "D"]
FEED_DAYS = 14
AWARD_SEARCH_MIN_DAYS = 120   # DoD posts ~90 days late, so reach into the prior FY early on
COVERAGE_DAYS = 7
NEEDS_ATTENTION_ATTEMPTS = 4
AWARD_KEY_RE = re.compile(r"^CONT_(AWD|IDV)_")

AWARD_FIELDS = [
    "generated_internal_id", "Award ID", "Recipient Name", "Awarding Agency", "Funding Agency",
    "Description", "Base Obligation Date", "Last Modified Date", "Start Date", "End Date",
    "Award Amount", "Contract Award Type", "NAICS", "Total Outlays", "PSC",
]
TXN_SEARCH_FIELDS = [
    "Award ID", "Action Date", "Transaction Amount", "Transaction Description",
    "generated_internal_id", "Mod", "Recipient Name", "Recipient UEI", "Awarding Agency",
    "Award Type", "Action Type", "PSC",
]


# ---------------------------------------------------------------- values and keys

def to_float(v):
    if v is None or v == "":
        return None
    f = float(v)
    return None if f != f else f


def psc_code_from(v):
    """PSC comes back as a string or {code, description}."""
    if isinstance(v, dict):
        v = v.get("code")
    return None if v is None else str(v)


def valid_award_key(k) -> bool:
    return bool(k) and bool(AWARD_KEY_RE.match(k)) and k == k.upper()


def piid_from_award_key(k: str) -> str:
    """CONT_AWD_<PIID>_<agency>_<parent PIID>_<parent agency>; rsplit keeps a PIID with '_'."""
    return k.split("_", 2)[2].rsplit("_", 3)[0]


def award_row(r: dict) -> dict:
    naics = r.get("NAICS")
    return {
        "award_key": r["generated_internal_id"], "award_id": r.get("Award ID"),
        "recipient": r.get("Recipient Name"), "awarding_agency": r.get("Awarding Agency"),
        "funding_agency": r.get("Funding Agency"),
        "naics_code": naics.get("code") if isinstance(naics, dict) else naics,
        "psc_code": psc_code_from(r.get("PSC")), "description": r.get("Description"),
        "amount_usd": to_float(r.get("Award Amount")),
        "award_base_date": to_date(r.get("Base Obligation Date")),
        "last_modified_at": to_ts(r.get("Last Modified Date")), "raw": jsonb(r),
    }


# ---------------------------------------------------------------- API

class ApiError(Exception):
    pass


class Api:
    def __init__(self, pause=0.2, retries=3):
        self.session, self.pause, self.retries = requests.Session(), pause, retries

    def post(self, path, body, retries=None):
        """(status, JSON or text). Retries connection errors, 429 and 5xx."""
        retries = self.retries if retries is None else retries
        for attempt in range(retries + 1):
            time.sleep(self.pause)
            try:
                resp = self.session.post(f"{API}{path}", json=body, timeout=120)
            except requests.RequestException:
                if attempt == retries:
                    raise
                time.sleep(2 ** attempt * 5)
                continue
            if (resp.status_code == 429 or resp.status_code >= 500) and attempt < retries:
                time.sleep(2 ** attempt * 5)
                continue
            try:
                return resp.status_code, resp.json()
            except ValueError:
                return resp.status_code, resp.text

    def post_ok(self, path, body):
        status, payload = self.post(path, body)
        if status != 200 or not isinstance(payload, dict):
            raise ApiError(f"POST {path} -> {status}: {str(payload)[:500]}")
        return payload


# ---------------------------------------------------------------- run

class WeeklyRun:
    def __init__(self, conn, run_date: dt.date, feed_days=FEED_DAYS, run_tag=None, parent_id=None, api=None):
        self.conn, self.run_date, self.feed_days = conn, run_date, feed_days
        self.run_id = f"usaspending-{run_date}"
        self.store = RawStore(self.run_id)
        self.api = api or Api()
        self.steps: dict = {}
        self.errors: list[str] = []
        self.rid = start_run(conn, "usaspending", self.run_id, run_tag, parent_id)

    def raw(self, endpoint, name, payload, record_count=None):
        return self.store.write_json(f"usaspending/{endpoint}", self.run_date, name, payload,
                                     "usaspending", record_count)

    def paged(self, endpoint, path, body):
        """POST every page, saving each. Yields (row, raw_source)."""
        self.raw(endpoint, "request.json", body)
        page = 1
        while True:
            payload = self.api.post_ok(path, {**body, "page": page})
            results = payload.get("results") or []
            rel = self.raw(endpoint, f"page_{page:03d}.json", payload, len(results))
            for row in results:
                yield row, rel
            if not payload.get("page_metadata", {}).get("hasNext"):
                break
            page += 1

    def step(self, name, fn, *args):
        try:
            with self.conn.transaction():
                return fn(*args)
        except Exception as e:  # rolled back; later steps still run
            self.errors.append(f"{name}: {type(e).__name__}: {e}")
            self.steps.setdefault(name, {})["error"] = str(e)
            print(f"step {name} failed: {e}")
            return None

    # ------------------------------------------------------------ 1

    def weekly_feed(self) -> dict[str, str]:
        start = (self.run_date - dt.timedelta(days=self.feed_days)).isoformat()
        body = {"filters": {"award_type_codes": AWARD_TYPES, "psc_codes": ["1550"],
                            "time_period": [{"start_date": start, "end_date": self.run_date.isoformat()}]},
                "fields": TXN_SEARCH_FIELDS, "limit": 100, "sort": "Action Date"}
        rows, rejected = {}, 0
        for r, rel in self.paged("weekly_transactions", "/search/spending_by_transaction/", body):
            gid, mod = r.get("generated_internal_id"), r.get("Mod")
            if gid is None or mod is None or not valid_award_key(gid):
                rejected += 1
                continue
            action = to_date(r.get("Action Date"))
            rows[(gid, mod)] = {
                "award_key": gid, "mod": mod, "award_id": r.get("Award ID"), "action_date": action,
                "recipient_name": r.get("Recipient Name"), "recipient_uei": r.get("Recipient UEI"),
                "awarding_agency": r.get("Awarding Agency"), "award_type": r.get("Award Type"),
                "action_type": r.get("Action Type"), "transaction_description": r.get("Transaction Description"),
                "psc_code": psc_code_from(r.get("PSC")), "transaction_amount": to_float(r.get("Transaction Amount")),
                "late_posting": action is not None and self.run_date > action + dt.timedelta(days=7),
                "raw": jsonb(r), "raw_source": rel, "first_seen": self.run_date, "last_seen": self.run_date,
            }
        new = late = 0
        for row in rows.values():
            if upsert(self.conn, "contract_activity_feed", row, ("award_key", "mod"),
                      ("first_seen", "late_posting")):
                new += 1
                late += row["late_posting"]
        award_ids: dict[str, str] = {}
        for row in rows.values():
            award_ids.setdefault(row["award_key"], row["award_id"])
        self.steps["weekly_feed"] = {"start_date": start, "end_date": str(self.run_date), "rows": len(rows),
                                     "award_keys": len(award_ids), "new_rows": new, "late_postings": late,
                                     "rejected": rejected}
        return award_ids

    # ------------------------------------------------------------ 2 / 3b

    def transaction_history(self, award_keys, discovered_via, step_name) -> int:
        new, failed, invalid = 0, [], 0
        for award_key in sorted(award_keys):
            try:
                body = {"award_id": award_key, "sort": "action_date", "order": "asc", "limit": 5000}
                pages, page = [], 1
                while True:
                    payload = self.api.post_ok("/transactions/", {**body, "page": page})
                    pages.append(payload)
                    if not payload.get("page_metadata", {}).get("hasNext"):
                        break
                    page += 1
                results = [r for p in pages for r in (p.get("results") or [])]
                rel = self.raw("transactions", f"{award_key}.json", {"request": body, "pages": pages}, len(results))
            except Exception as e:
                failed.append({"award_key": award_key, "error": str(e)})
                continue
            for r in results:
                tid = r.get("id")
                if not tid or not tid.startswith("CONT_TX_"):
                    invalid += 1
                    continue
                new += upsert(self.conn, "contract_transactions", {
                    "transaction_id": tid, "txn_key": tid[len("CONT_TX_"):], "award_key": award_key,
                    "type": r.get("type_description"), "action_type": r.get("action_type_description"),
                    "action_date": to_date(r.get("action_date")), "description": r.get("description"),
                    "modification_number": r.get("modification_number"),
                    "federal_action_obligation": to_float(r.get("federal_action_obligation")),
                    "discovered_via": discovered_via, "raw": jsonb(r), "raw_source": rel,
                    "first_seen": self.run_date, "last_seen": self.run_date,
                }, ("transaction_id",), ("first_seen", "discovered_via"))
        self.steps[step_name] = {"awards": len(award_keys), "new_transactions": new,
                                 "failed": failed, "invalid_ids": invalid}
        if failed:
            self.errors.append(f"{step_name}: {len(failed)} awards failed")
        return new

    # ------------------------------------------------------------ 3

    def award_search(self) -> set[str]:
        fy_start = dt.date(fiscal_year(self.run_date) - 1, 10, 1)
        start = min(fy_start, self.run_date - dt.timedelta(days=AWARD_SEARCH_MIN_DAYS))
        period = {"start_date": start.isoformat(), "end_date": (self.run_date - dt.timedelta(days=1)).isoformat()}
        body = {"subawards": False, "limit": 100, "fields": AWARD_FIELDS,
                "filters": {"award_type_codes": AWARD_TYPES, "psc_codes": ["1550"], "time_period": [period]}}
        rows, rejected = {}, 0
        for r, rel in self.paged("awards", "/search/spending_by_award/", body):
            if not r.get("generated_internal_id"):
                rejected += 1
                continue
            rows[r["generated_internal_id"]] = {**award_row(r), "raw_source": rel}
        new = 0
        for row in rows.values():
            new += upsert(self.conn, "contract_awards", {
                **row, "source": "award_search", "lookup_status": "ok", "backfill": None,
                "first_seen": self.run_date, "last_seen": self.run_date,
            }, ("award_key",), ("first_seen",))
        self.steps["award_search"] = {"fiscal_year": fiscal_year(self.run_date), **period, "results": len(rows),
                                      "new_awards": new, "rejected": rejected,
                                      "psc_not_1550": sum(r["psc_code"] not in (None, "1550") for r in rows.values())}
        return set(rows)

    # ------------------------------------------------------------ 3b

    def coverage_pull(self, already_fetched: set[str]) -> None:
        since = self.run_date - dt.timedelta(days=COVERAGE_DAYS)
        keys = {k for (k,) in self.conn.execute(
            "SELECT award_key FROM contract_awards WHERE last_modified_at >= %s", (since,))} - already_fetched
        self.transaction_history(keys, "award_modified", "coverage_pull")

    # ------------------------------------------------------------ 4

    def lookup_award(self, award_key, piid, method):
        """Accept a result only if exactly one row has generated_internal_id == award_key,
        which makes the unquoted (fuzzy) PIID search safe as a last try."""
        tries = [("award_ids_exact", {"award_ids": [f'"{piid}"']}, 0),
                 ("award_ids_exact", {"award_ids": [piid]}, None)]  # quoted form often 503s
        if method == "award_unique_id":
            tries.insert(0, ("award_unique_id", {"award_unique_id": award_key}, None))
        attempts = []
        for m, extra, retries in tries:
            body = {"subawards": False, "limit": 100, "page": 1, "fields": AWARD_FIELDS,
                    "filters": {"award_type_codes": AWARD_TYPES, **extra}}
            status, payload = self.api.post("/search/spending_by_award/", body, retries=retries)
            attempts.append({"method": m, "request": body, "status": status, "response": payload})
            if status == 200 and isinstance(payload, dict):
                hits = [r for r in payload.get("results") or [] if r.get("generated_internal_id") == award_key]
                if len(hits) == 1:
                    return hits[0], m, attempts
        if all(a["status"] != 200 for a in attempts):
            raise ApiError(f"every lookup failed: {[a['status'] for a in attempts]}")
        return None, tries[-1][0], attempts

    def backfill(self, method: str) -> None:
        feed_ids = dict(self.conn.execute(
            "SELECT DISTINCT ON (award_key) award_key, award_id FROM contract_activity_feed").fetchall())
        missing = [k for (k,) in self.conn.execute(
            "SELECT k FROM (SELECT award_key k FROM contract_activity_feed UNION"
            " SELECT award_key FROM contract_transactions) refs"
            " WHERE NOT EXISTS (SELECT 1 FROM contract_awards a WHERE a.award_key = k AND a.lookup_status = 'ok')"
            " ORDER BY k")]
        old = {k: b for k, b in self.conn.execute(
            "SELECT award_key, backfill FROM contract_awards WHERE award_key = ANY(%s)", (missing,))}
        stats = {"method": method, "missing": len(missing), "resolved": 0, "pending": 0, "errors": 0}
        for award_key in missing:
            piid = feed_ids.get(award_key) or piid_from_award_key(award_key)
            try:
                hit, used, attempts = self.lookup_award(award_key, piid, method)
            except Exception as e:
                stats["errors"] += 1
                self.errors.append(f"backfill {award_key}: {e}")
                continue
            rel = self.raw("award_backfill", f"{award_key}.json", {"attempts": attempts}, 1 if hit else 0)
            prev = old.get(award_key) or {}
            same_day = prev.get("last_attempt") == str(self.run_date)
            bf = {"method": used, "first_attempt": prev.get("first_attempt", str(self.run_date)),
                  "last_attempt": str(self.run_date), "attempts": prev.get("attempts", 0) + (0 if same_day else 1),
                  "referenced_by": "contract_activity_feed" if award_key in feed_ids else "contract_transactions"}
            if hit:
                stats["resolved"] += 1
                row = {**award_row(hit), "raw_source": rel, "source": "backfill", "lookup_status": "ok"}
            else:
                stats["pending"] += 1
                row = {"award_key": award_key, "award_id": piid, "source": "backfill", "lookup_status": "pending"}
            upsert(self.conn, "contract_awards", {**row, "backfill": jsonb(bf), "first_seen": self.run_date,
                                                   "last_seen": self.run_date}, ("award_key",), ("first_seen",))
        stats["needs_attention"] = [k for (k,) in self.conn.execute(
            "SELECT award_key FROM contract_awards WHERE lookup_status = 'pending'"
            " AND (backfill->>'attempts')::int >= %s", (NEEDS_ATTENTION_ATTEMPTS,))]
        self.steps["backfill"] = stats

    # ------------------------------------------------------------ 5

    def integrity(self) -> None:
        unresolved = [k for (k,) in self.conn.execute(
            "SELECT k FROM (SELECT award_key k FROM contract_activity_feed UNION"
            " SELECT award_key FROM contract_transactions) refs"
            " WHERE NOT EXISTS (SELECT 1 FROM contract_awards a WHERE a.award_key = k) ORDER BY k")]
        pending = self.conn.execute("SELECT count(*) FROM contract_awards WHERE lookup_status = 'pending'").fetchone()[0]
        self.steps["integrity"] = {"unresolved_award_keys": len(unresolved), "unresolved": unresolved,
                                   "pending": pending}

    def finish(self) -> str:
        status = "partial" if self.errors else "ok"
        finish_run(self.conn, self.rid, status, {"steps": self.steps, "raw_folder": self.store.folders}, self.errors)
        return status


def weekly(conn, run_date=None, feed_days=FEED_DAYS, backfill_method="award_unique_id", coverage_pull=True,
           run_tag=None, parent_id=None) -> tuple[WeeklyRun, str]:
    run = WeeklyRun(conn, run_date or dt.date.today(), feed_days, run_tag, parent_id)
    feed = run.step("weekly_feed", run.weekly_feed) or {}
    run.step("txn_history", run.transaction_history, set(feed), "weekly_feed", "txn_history")
    run.step("award_search", run.award_search)
    if coverage_pull:
        run.step("coverage_pull", run.coverage_pull, set(feed))
    run.step("backfill", run.backfill, backfill_method)
    run.step("integrity", run.integrity)
    return run, run.finish()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--run-date", type=dt.date.fromisoformat, default=dt.date.today())
    p.add_argument("--feed-days", type=int, default=FEED_DAYS, help="weekly feed lookback (catch-up runs: more)")
    p.add_argument("--backfill-method", choices=["award_unique_id", "award_ids_exact"], default="award_unique_id")
    p.add_argument("--no-coverage-pull", action="store_true")
    args = p.parse_args()
    with connect() as conn:
        run, status = weekly(conn, args.run_date, args.feed_days, args.backfill_method, not args.no_coverage_pull)
    print(json.dumps({"run_id": run.run_id, "status": status, "errors": run.errors, "steps": run.steps},
                     indent=1, default=str))
    if status != "ok":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
