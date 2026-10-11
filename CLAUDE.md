# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

An ETL pipeline that tracks U.S. UAS (drone) policy and federal spending. It pulls data from the LegiScan API, USASpending.gov, the Federal Register API, and the Regulations.gov API into two stores, which run side by side during a parallel run (`docs/mongodb_schema.md` section 9):

- **SQLite** `UAS_Tracker.db`, written by the Jupyter notebooks in `notebooks/`. It stays the system of record until cut-over.
- **MongoDB** (`uas_tracker` DB), written by the Python scripts in `scripts/`. It is the target design. `docs/mongodb_schema.md` is the spec (collections, indexes, decisions D1–D17) and the scripts cite its section numbers.

There is no test suite or linter. Dependencies are pinned in `requirements.txt`, installed into `.venv/`.

## Setup

- `.env` (gitignored) holds `LEGI_API_KEY`, `REGULATIONS_API_KEY` (sent only in the `X-Api-Key` header; preflight requires it), `MONGO_URI` (default `mongodb://127.0.0.1:27017/`), `MONGO_DB` (default `uas_tracker`), and `RAW_ROOT` (default `/uaspool/raw`). The USASpending API needs no key. Never print or log these values.
- ZFS pool `uaspool`: `uaspool/mongo` is mongod's `dbPath`, `uaspool/raw` (mounted at `/uaspool/raw`, outside the repo) holds write-once raw API responses, and `uaspool/backups` holds `mongodump` archives.
- `mongod` runs as a system service, bound to 127.0.0.1.
- `systemd/install.sh` is a one-time setup that you run as the pipeline user. It sets `zfs allow` snapshot/rollback/mount, a sudoers rule for `systemctl stop|start mongod` and `/usr/local/sbin/uas-zfs-prune`, and linger, and it installs the user units. It does not delegate `zfs destroy`. Pruning goes only through `uas-zfs-prune`, which accepts `@weekly-*` names only.
- `./dev.sh` opens or attaches a tmux session `uas` with Claude Code, Neovim, and `mongosh` connected to the DB. It reads `MONGO_URI` from the environment or `.env` and keeps it off the command line.

## Weekly run

`weekly_run.sh` runs from the user timer `uas-weekly.timer` (Sundays at 19:00, `Persistent=true`). To run it by hand, use `systemctl --user start uas-weekly.service`, or run `./weekly_run.sh --dry-run` to print the state-changing commands without running them. Logs and per-run state are in `~/.local/state/uas-weekly/` (`logs/`, `runs/<tag>/`). Steps:

1. Preflight checks (venv, nbconvert, `.env`, ZFS datasets, mongod, sudo rules, an unfinished earlier run), then a wait for the network.
2. An atomic ZFS snapshot `uaspool/mongo` + `uaspool/raw` `@weekly-<date>-pre`, plus a backup of `UAS_Tracker.db` and the CSV exports.
3. The notebooks run through `jupyter nbconvert --execute`. The executed copies go to the run dir and `notebooks/` is not modified. The order is LegiScan1, LegiScan2, USASpending1, USASpending2, USASpenging_Past_Visualizations. LegiScan2 is skipped with a warning when the newest zip is already loaded, because it uses a plain `INSERT` and would duplicate rows.
4. `scripts/weekly_ingest.py`, which runs `legiscan_loader`, `usaspending_loader`, and then the rulemaking loaders (`federal_register_loader`, `regulations_loader`). Each one logs to the `runs` collection. A rulemaking loader's `"partial"` (normal under the Regulations.gov rate limit) makes `weekly_ingest.py` exit 2, which is a warning, not a rollback.
5. Count validation: no collection may shrink, and the required collections must be non-empty.
6. If any of steps 1–5 fails, the script stops mongod, rolls both datasets back to `-pre`, restores the SQLite DB and CSVs, and starts mongod. If they pass, it takes a `-post` snapshot, writes a `mongodump` to `/uaspool/backups`, and prunes snapshots and dumps (keeping 12 weekly and 12 monthly). The off-laptop copy (D13) is still a stub.

Exit codes: 0 means ok, 1 means failed, and 2 means completed with warnings (the unit treats 2 as success). After a run that died without cleanup, preflight refuses to start and prints the rollback commands. Use `touch ~/.local/state/uas-weekly/resolved/<tag>` once the run is resolved.

## Code layout

- `notebooks/` (SQLite): run them from inside `notebooks/`, because their paths are relative (`../UAS_Tracker.db`). LegiScan1 downloads `legiscan_getDatasetRaw_data/getDatasetRaw<date>.zip`, and LegiScan2 loads the newest zip there. USASpending1/2 cover current-FY contracts (PSC 1550, award types A–D). `USASpending_Past_*` are one-time backfills from `usa_spending_old_data/`.
- `scripts/` (MongoDB): run them from the repo root with `.venv/bin/python`. `mongo_common.py` holds the shared config and helpers. Every loader saves the raw response under `$RAW_ROOT` before loading it, and its writes are upserts or `$setOnInsert`, so re-running a loader is safe.
  - `weekly_ingest.py`: the weekly entry point.
  - `legiscan_loader.py` loads `legislation_history` (one snapshot per bill per week) and `legislation` (latest state).
  - `usaspending_loader.py`: transactions first (14-day lookback), then the award search, the coverage pull, the award backfill, and an integrity check (D6, D7).
  - `federal_register_loader.py` searches six UAS keyword phrases over a 9-day `publication_date` window and upserts into `rulemakings` with `_id` `fr:<document_number>` (D17). `--start/--end` override the window.
  - `regulations_loader.py` loads the Regulations.gov dockets of the Federal Register documents seen this run into `rulemakings` (`rg:<documentId>`) and their comments into `rulemaking_comments` (`rgc:<commentId>`). Comment details cost one request per comment (1000 requests/hour), so at most `--max-comment-details` (300) are fetched per run and the rest wait for later runs. `--docket ID` loads one docket for testing.
  - `backfill_rulemakings.py` runs the rulemaking loaders over a wider window (default 14 days). It runs outside `weekly_run.sh`, so it requires `uaspool/mongo` and `uaspool/raw` `@pre-rulemakings-backfill` snapshots to exist first.
  - `mongo_indexes.py`, `migrate_sqlite_to_mongo.py`, `load_contracts_past.py`: setup and one-time loads.
  - `export_csvs.py --out-dir DIR`: CSV exports from MongoDB. Until cut-over, point it at a directory other than the repo root so it does not overwrite the notebooks' CSVs.

## Data notes

- Fiscal year: the FY starts Oct 1, and FY = calendar year + 1 for Oct–Dec.
- `legislation` and `rulemakings` include rows entered by hand from congress.gov and Federal Register alerts. In `rulemakings`, hand rows have no `source` field, and API documents have `source` and a prefixed `_id` (`fr:`, or `rg:` for Regulations.gov). `Exported_Rulemakings.csv` holds hand rows only.
- The live-API `contracts*` tables and the bulk-CSV `contracts_past*` tables use different keys (`api_internal_id`/`award_key` vs `contract_award_unique_key`). See schema doc section 5 before joining them.

## Repo conventions

- Weekly commits ("Ran ... notebooks for weekly update <date>") contain **only the CSV exports**. Do not commit `UAS_Tracker.db` (D3) or raw zips/API responses (D15). Raw data lives on `/uaspool/raw`, and backups come from ZFS snapshots and `mongodump`.
- At cut-over (schema doc sections 6.3, 9), `UAS_Tracker.db` gets gitignored and untracked, `legiscan_getDatasetRaw_data/` moves to `/uaspool/raw/legiscan/`, the loaders switch to `--legiscan-download`, and `export_csvs.py` writes to the repo root. Update this file when that happens.
