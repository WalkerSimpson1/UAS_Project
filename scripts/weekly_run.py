"""Weekly pipeline on this laptop (replaces weekly_run.sh + weekly_ingest.py; docs/DECISIONS.md).

    .venv/Scripts/python scripts/weekly_run.py [--force] [--dry-run] [--no-ai] [--publish]

Task Scheduler starts this daily and at logon. It exits at once if a weekly
run succeeded in the past 6 days (unless --force), so a laptop that was off
on Sunday catches up the next time it's on. Windows widen to cover the gap.

Steps:
  1 preflight   Postgres reachable, API keys set, network up
  2 backup      pg_dump -> data/backups/pre-<date>.dump
  3 loaders     legiscan, usaspending, federal_register, regulations_gov (each logs to runs)
  4 sync        scripts/sync_policies.py: new/changed source rows -> policies, events, review queue
  4b drafts     scripts/summarize.py: claude -p drafts for up to 15 new policies (warnings only)
  5 validate    no source table shrank
  5b dashboard  scripts/export_site.py -> site/data/*.json, export_public_db.py -> site/data/uas_public.db;
                --publish commits and pushes site/data
  6 backup      pg_dump -> data/backups/uas_tracker_<date>.dump, prune old dumps
A failure in 3-5 keeps the data (every write is an upsert) and the pre dump;
the log names the restore command. Exit: 0 ok, 2 warnings, 1 failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import socket
import subprocess
import sys
import traceback

import federal_register_loader
import legiscan_loader
import regulations_loader
import usaspending_loader
from pg_common import REPO, connect, finish_run, redact, start_run

BACKUPS = REPO / "data" / "backups"
LOGS = REPO / "data" / "logs"
SKIP_IF_RUN_WITHIN_DAYS = 6
KEEP_WEEKLY = 12
SOURCE_TABLES = ["bills", "bill_snapshots", "regulatory_documents", "regulatory_comments", "contract_awards",
                 "contract_transactions", "contract_activity_feed", "past_awards", "past_transactions",
                 "raw_files"]


class Log:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(path, "a", encoding="utf-8")

    def __call__(self, *parts):
        line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} " + redact(" ".join(str(p) for p in parts))
        print(line, flush=True)
        self.f.write(line + "\n")
        self.f.flush()


def compose(*args, stdout=None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, stdout=stdout, stderr=subprocess.PIPE,
                          check=True)


def pg_dump(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".part")
    with open(tmp, "wb") as f:
        compose("exec", "-T", "postgres", "sh", "-c", 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc', stdout=f)
    tmp.replace(path)


def prune_backups(log) -> None:
    dumps = sorted(BACKUPS.glob("uas_tracker_*.dump"))
    for old in dumps[:-KEEP_WEEKLY]:
        old.unlink()
        log("pruned", old.name)
    pres = sorted(BACKUPS.glob("pre-*.dump"))
    for old in pres[:-2]:
        old.unlink()


def publish(log, today) -> None:
    """Commit only site/data and push the current branch. Anything else stays uncommitted."""
    def git(*a):
        return subprocess.run(["git", *a], cwd=REPO, capture_output=True, text=True, check=True).stdout
    git("add", "--", "site/data")
    if not git("diff", "--cached", "--name-only", "--", "site/data").strip():
        log("publish: no dashboard changes")
        return
    git("commit", "-m", f"Weekly dashboard data {today}", "--", "site/data")
    git("push")
    log("publish: pushed dashboard data")


def counts(conn) -> dict[str, int]:
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in SOURCE_TABLES}


def last_weekly(conn) -> dt.date | None:
    r = conn.execute("SELECT max(started_at)::date FROM runs WHERE pipeline IN ('weekly', 'weekly_ingest')"
                     " AND status IN ('ok', 'partial')").fetchone()
    return r[0]


def wait_for_postgres(log) -> None:
    try:
        compose("up", "-d", "--wait", "postgres")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        err = getattr(e, "stderr", b"") or b""
        raise RuntimeError(f"docker compose up failed (is Docker Desktop running?): {err.decode()[:300]}")
    log("postgres up")


def network_ok() -> bool:
    for host in ("api.usaspending.gov", "www.federalregister.gov"):
        try:
            socket.create_connection((host, 443), timeout=10).close()
        except OSError:
            return False
    return True


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--force", action="store_true", help="run even if a weekly run succeeded recently")
    p.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    p.add_argument("--no-ai", action="store_true", help="skip the claude -p drafts")
    p.add_argument("--publish", action="store_true", help="commit and push site/data (deploys the dashboard)")
    args = p.parse_args()
    today = dt.date.today()
    log = Log(LOGS / f"weekly-{today}.log")

    # ---- 1 preflight
    try:
        wait_for_postgres(log)
        conn = connect()
    except Exception as e:
        log("preflight failed:", e)
        return 1
    last = last_weekly(conn)
    if last and (today - last).days <= SKIP_IF_RUN_WITHIN_DAYS and not args.force:
        log(f"last weekly run {last}; nothing to do")
        return 0
    gap = (today - last).days if last else 7
    feed_days = max(usaspending_loader.FEED_DAYS, gap + 7)
    fr_start = min(today - dt.timedelta(days=federal_register_loader.WINDOW_DAYS),
                   (last or today) - dt.timedelta(days=2))
    log(f"last weekly run {last}, gap {gap} days: feed_days={feed_days}, rulemakings from {fr_start}")
    missing = [k for k in ("LEGI_API_KEY", "REGULATIONS_API_KEY") if not os.environ.get(k)]
    if missing:
        log("preflight failed: not set in .env:", ", ".join(missing))
        return 1
    if not network_ok():
        log("preflight failed: no network")
        return 1
    if args.dry_run:
        return 0

    run_tag = f"weekly-{today}"
    parent = start_run(conn, "weekly", run_tag, run_tag)
    errors: list[str] = []
    warnings: list[str] = []
    children: dict[str, str] = {}

    def fail(name, e):
        errors.append(f"{name}: {type(e).__name__}: {e}")
        log(f"{name} FAILED:", e)
        log(traceback.format_exc())
        conn.rollback()

    # ---- 2 backup
    pre = BACKUPS / f"pre-{today}.dump"
    try:
        pg_dump(pre)
        log("pre-run backup", pre.name)
    except Exception as e:
        fail("backup", e)
        finish_run(conn, parent, "failed", {"children": children}, errors)
        return 1
    before = counts(conn)

    # ---- 3 loaders (independent: one failing doesn't stop the rest)
    try:
        r = legiscan_loader.ingest(conn, None, today, run_tag=run_tag, parent_id=parent)
        children["legiscan"] = r["status"]
        log("legiscan:", r["status"], r.get("steps") or r.get("skipped"))
    except Exception as e:
        fail("legiscan", e)
    try:
        run, status = usaspending_loader.weekly(conn, today, feed_days, run_tag=run_tag, parent_id=parent)
        children["usaspending"] = status
        (warnings if status == "partial" else []).extend(f"usaspending {e}" for e in run.errors)
        log("usaspending:", status, {k: {x: y for x, y in v.items() if isinstance(y, (int, str))}
                                     for k, v in run.steps.items()})
    except Exception as e:
        fail("usaspending", e)
    for name, loader in (("federal_register", federal_register_loader), ("regulations_gov", regulations_loader)):
        try:
            r = loader.ingest(conn, fr_start, today, run_date=today, run_tag=run_tag, parent_id=parent)
            children[name] = r["status"]
            if r["status"] == "partial":
                warnings.append(f"{name} partial (rate limit or a failed docket)")
            log(f"{name}:", r["status"], f"requests={r.get('requests')} new={r.get('new')}")
        except Exception as e:
            fail(name, e)

    # ---- 4 sync policies
    try:
        import sync_policies
        s = sync_policies.sync(conn, today, run_id=parent)
        log("sync_policies:", s)
    except Exception as e:
        fail("sync_policies", e)

    # ---- 4b AI drafts (claude -p on the subscription); problems are warnings
    if not args.no_ai:
        try:
            import summarize
            s = summarize.summarize(conn, limit=summarize.DEFAULT_LIMIT, log=log)
            log("summarize:", s)
            if s["failed"]:
                warnings.append(f"summarize: {s['failed']} drafts failed")
        except Exception as e:
            conn.rollback()
            warnings.append(f"summarize: {type(e).__name__}: {e}")
            log("summarize skipped:", e)

    # ---- 5 validate
    after = counts(conn)
    shrank = {t: (before[t], after[t]) for t in SOURCE_TABLES if after[t] < before[t]}
    for t in SOURCE_TABLES:
        log(f"count {t}: {before[t]} -> {after[t]}")
    if shrank:
        errors.append(f"tables shrank: {shrank}")
    for t in ("bills", "contract_awards"):
        if after[t] == 0:
            errors.append(f"{t} is empty")

    status = "failed" if errors else ("partial" if warnings else "ok")
    finish_run(conn, parent, status, {"children": children, "feed_days": feed_days, "fr_start": str(fr_start),
                                      "counts_before": before, "counts_after": after, "warnings": warnings}, errors)

    # ---- 5b dashboard data, then (with --publish) commit and push site/data
    if status != "failed":
        try:
            import export_public_db
            import export_site
            log("export_site:", export_site.export(conn))
            log("export_public_db:", export_public_db.export(conn))
            if args.publish:
                publish(log, today)
        except Exception as e:
            warnings.append(f"dashboard: {type(e).__name__}: {e}")
            log("dashboard step failed (warning):", e)
            status = "partial"

    # ---- 6 backup
    if status != "failed":
        try:
            pg_dump(BACKUPS / f"uas_tracker_{today}.dump")
            prune_backups(log)
            log("backup", f"uas_tracker_{today}.dump")
        except Exception as e:
            log("post-run backup failed (warning):", e)
            status = "partial" if status == "ok" else status
    else:
        log("FAILED. Data from successful steps is kept. To undo the whole run:")
        log(f"  docker compose exec -T postgres sh -c 'pg_restore -U \"$POSTGRES_USER\" -d \"$POSTGRES_DB\""
            f" --clean --if-exists' < data/backups/{pre.name}")
    log(f"weekly run {run_tag}: {status}", *(f"\n  {e}" for e in errors + warnings))
    conn.close()
    return {"ok": 0, "partial": 2, "failed": 1}[status]


if __name__ == "__main__":
    sys.exit(main())
