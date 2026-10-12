# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An ETL pipeline that tracks U.S. UAS (drone) policy and federal spending. It pulls data from the LegiScan API, USASpending.gov, the Federal Register API, and the Regulations.gov API into **MongoDB** (`uas_tracker` DB), written by the Python scripts in `scripts/`. `docs/mongodb_schema.md` is the spec (collections, indexes, decisions D1–D20) and the scripts cite its section numbers.

Cut-over from SQLite happened on 2026-10-11, after one parallel week instead of the two in section 9 (D18). MongoDB is the system of record. The final SQLite DB is archived at `/uaspool/backups/UAS_Tracker-final.db`, `UAS_Tracker.db` is gitignored and untracked, and the SQLite notebooks are deleted (they remain in git history).

There is no test suite or linter. Dependencies are pinned in `requirements.txt`, installed into `.venv/`.

## Setup

- `.env` (gitignored) holds `LEGI_API_KEY`, `REGULATIONS_API_KEY` (sent only in the `X-Api-Key` header; preflight requires it), `MONGO_URI` (default `mongodb://127.0.0.1:27017/`), `MONGO_DB` (default `uas_tracker`), `RAW_ROOT` (default `/uaspool/raw`), and `ATLAS_URI` (the MongoDB Atlas connection string for the off-laptop copy). The USASpending API needs no key. Never print or log these values.
- ZFS pool `uaspool`: `uaspool/mongo` is mongod's `dbPath`, `uaspool/raw` (mounted at `/uaspool/raw`, outside the repo) holds write-once raw API responses, including the LegiScan zips under `legiscan/<dataset_date>/`, and `uaspool/backups` holds `mongodump` archives.
- `mongod` runs as a system service, bound to 127.0.0.1.
- `systemd/install.sh` is a one-time setup that you run as the pipeline user. It sets `zfs allow` snapshot/rollback/mount, a sudoers rule for `systemctl stop|start mongod` and `/usr/local/sbin/uas-zfs-prune`, and linger, and it installs the user units. It does not delegate `zfs destroy`. Pruning goes only through `uas-zfs-prune`, which accepts `@weekly-*` names only.
- `./dev.sh` opens or attaches a tmux session `uas` with Claude Code, Neovim, and `mongosh` connected to the DB. It reads `MONGO_URI` from the environment or `.env` and keeps it off the command line.

## Weekly run

`weekly_run.sh` runs from the user timer `uas-weekly.timer` (Sundays at 19:00, `Persistent=true`). To run it by hand, use `systemctl --user start uas-weekly.service`, or run `./weekly_run.sh --dry-run` to print the state-changing commands without running them. Logs and per-run state are in `~/.local/state/uas-weekly/` (`logs/`, `runs/<tag>/`). Steps:

1. Preflight checks (venv, `.env`, ZFS datasets, mongod, sudo rules, an unfinished earlier run), then a wait for the network.
2. An atomic ZFS snapshot `uaspool/mongo` + `uaspool/raw` `@weekly-<date>-pre`.
3. `scripts/weekly_ingest.py --legiscan-download`, which runs `legiscan_loader` (it downloads the dataset only when LegiScan has a new one), `usaspending_loader`, and then the rulemaking loaders (`federal_register_loader`, `regulations_loader`). Each one logs to the `runs` collection. A rulemaking loader's `"partial"` (normal under the Regulations.gov rate limit) makes `weekly_ingest.py` exit 2, which is a warning, not a rollback.
4. Count validation: no collection may shrink, and the required collections must be non-empty.
5. If any of steps 1–4 fails, the script stops mongod, rolls both datasets back to `-pre`, and starts mongod. If they pass, it takes a `-post` snapshot, writes the CSV exports to the repo root (`export_csvs.py`), writes a `mongodump` to `/uaspool/backups`, and prunes snapshots and dumps (keeping 12 weekly and 12 monthly). A failure after validation is reported (exit 1) but not rolled back. Last, it restores that dump to MongoDB Atlas (`mongorestore --drop`, D20), so Atlas mirrors the local DB. A missing `ATLAS_URI` or a failed restore is a warning (exit 2), not a failure. Atlas holds the documents only; the raw files have no off-laptop copy yet.

Exit codes: 0 means ok, 1 means failed, and 2 means completed with warnings (the unit treats 2 as success). After a run that died without cleanup, preflight refuses to start and prints the rollback commands. Use `touch ~/.local/state/uas-weekly/resolved/<tag>` once the run is resolved.

## Code layout

- `analysis/USASpenging_Past_Visualizations.ipynb`: fiscal-week/month obligation charts, `weekly_rank()` and competition trends, read from MongoDB (`contracts_past_transactions`, `contracts_weekly_transactions_all`) through `mongo_common.get_db()`. Run it from inside `analysis/`. It only reads; `weekly_run.sh` does not run it.
- `scripts/` (MongoDB): run them from the repo root with `.venv/bin/python`. `mongo_common.py` holds the shared config and helpers. Every loader saves the raw response under `$RAW_ROOT` before loading it, and its writes are upserts or `$setOnInsert`, so re-running a loader is safe.
  - `weekly_ingest.py`: the weekly entry point.
  - `legiscan_loader.py` loads `legislation_history` (one snapshot per bill per week) and `legislation` (latest state).
  - `usaspending_loader.py`: transactions first (14-day lookback), then the award search, the coverage pull, the award backfill, and an integrity check (D6, D7). The coverage pull (step 3b) is kept for now and flagged for review (D19).
  - `federal_register_loader.py` searches six UAS keyword phrases over a 9-day `publication_date` window and upserts into `rulemakings` with `_id` `fr:<document_number>` (D17). `--start/--end` override the window.
  - `regulations_loader.py` loads the Regulations.gov dockets of the Federal Register documents seen this run into `rulemakings` (`rg:<documentId>`) and their comments into `rulemaking_comments` (`rgc:<commentId>`). Comment details cost one request per comment (1000 requests/hour), so at most `--max-comment-details` (300) are fetched per run and the rest wait for later runs. `--docket ID` loads one docket for testing.
  - `backfill_rulemakings.py` runs the rulemaking loaders over a wider window (default 14 days). It runs outside `weekly_run.sh`, so it requires `uaspool/mongo` and `uaspool/raw` `@pre-rulemakings-backfill` snapshots to exist first.
  - `mongo_indexes.py`, `migrate_sqlite_to_mongo.py`, `load_contracts_past.py`: setup and one-time loads.
  - `export_csvs.py --out-dir DIR`: CSV exports from MongoDB. `weekly_run.sh` passes the repo root, and those files are the committed exports.

## Data notes

- Fiscal year: the FY starts Oct 1, and FY = calendar year + 1 for Oct–Dec.
- There are no hand-entered rows: every document in `legislation` and `rulemakings` comes from an API. The code still supports hand rows (in `rulemakings`, a hand row would have no `source` field), but none exist, and the hand-maintained fields (`sequence`, `track`, `event_date`, `event_description`, `notes`) are all null. API documents in `rulemakings` have `source` and a prefixed `_id` (`fr:`, or `rg:` for Regulations.gov). `Exported_Rulemakings.csv` exports hand rows only, so it has no data rows.
- The live-API `contracts*` tables and the bulk-CSV `contracts_past*` tables use different keys (`api_internal_id`/`award_key` vs `contract_award_unique_key`). See schema doc section 5 before joining them.

## Repo conventions

- Weekly commits ("Weekly run <date> exports") contain **only the CSV exports** that `export_csvs.py` writes: `Exported_SQL_Legislation_Table.csv`, `Exported_SQL_Contracts_Table.csv`, `Exported_Contract_Transactions.csv`, `Exported_Legislation_Current.csv`, `Exported_Rulemakings.csv`. Do not commit `UAS_Tracker.db` (D3) or raw zips/API responses (D15). Raw data lives on `/uaspool/raw`, and backups come from ZFS snapshots and `mongodump`.
