# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Jupyter-notebook ETL pipeline that tracks U.S. UAS (drone) policy and federal spending. Data from the LegiScan API and USASpending.gov is pulled, filtered, and loaded into a single SQLite database, `UAS_Tracker.db`, which is committed to git. CSV exports of key tables are also committed so that table history can be read in the git log. There is no build system, test suite, linter, or requirements file. All code lives in `notebooks/`.

## Running

- Run notebooks from inside `notebooks/`. Every path is relative to that directory (`../UAS_Tracker.db`, `../legiscan_getDatasetRaw_data/...`).
- Inspect the DB with `sqlite3 UAS_Tracker.db .schema` (or Python's `sqlite3` module).
- The LegiScan notebooks need `LEGI_API_KEY` in a `.env` file, loaded with `python-dotenv`. The USASpending API needs no key.
- Libraries used: pandas, numpy, requests, python-dotenv, matplotlib, seaborn.

## Pipelines

**LegiScan (weekly; the federal "US" dataset refreshes every Sunday)**
1. `LegiScan1.ipynb`: calls `getDatasetList` to get the current `access_key`/`session_id`, then calls `getDatasetRaw` and saves `legiscan_getDatasetRaw_data/getDatasetRaw<dataset_date>.zip`.
2. `LegiScan2.ipynb`: reads the bill JSONs in that zip, filters them by keywords (`uas`, `drone`, `unmanned`, `uav`) in the title or description, and INSERTs the matches into `legislation`. It then exports `Exported_SQL_Legislation_Table.csv`. **The zip filename is hardcoded in the cells marked "CHANGE ZIP FILE". Update it to the newest zip before running.** This uses a plain `INSERT` with no dedup, so re-running it adds duplicate rows.

**USASpending: current FY contracts (PSC code 1550, award types A–D)**
1. `USASpending1.ipynb`: pages through `/api/v2/search/spending_by_award/` from 2025-10-01 to yesterday and runs `INSERT OR REPLACE` into `contracts` (key: `api_internal_id` = `generated_internal_id`). It then exports `Exported_SQL_Contracts_Table.csv`.
2. `USASpending2.ipynb`: for each contract with `last_modified_date` in the past 7 days, pulls `/api/v2/transactions/` into `contract_transactions`.

**USASpending: historical backfill (one-time)**
- `USASpending_Past_Awards.ipynb` / `USASpending_Past_Transactions.ipynb`: load the bulk-download CSVs in `usa_spending_old_data/{base_awards,transactions}/psc1550_fy20XX[_t].csv` (FY2020–2025) into `contracts_past` / `contracts_past_transactions` via `to_sql(if_exists='replace')`. Awards are deduped by `contract_award_unique_key`, keeping the latest action.

**Analysis**
- `USASpenging_Past_Visualizations.ipynb` (note the filename typo): computes fiscal-week/month obligation distributions from `contracts_past_transactions`, competition metrics, and a `weekly_rank()` percentile of the latest week against a ±2-week historical window. It also pulls the past 7 days from `/api/v2/search/spending_by_transaction/` into `contracts_weekly_transactions_all` (PK `generated_internal_id, mod`).
- Fiscal year convention: the FY starts Oct 1, and FY = calendar year + 1 for Oct–Dec.

## Database notes

- `legislation` and `rulemakings` share a tracker schema (`item_name`, `identifier`, `body`, `event_date`, `track`, ...). Rows are also added by hand from congress.gov / Federal Register email alerts, so not every row comes from LegiScan. `rulemakings` is populated only by hand.
- The `contracts*` tables from the live API use different column names from the `contracts_past*` tables from the bulk CSVs (e.g. `api_internal_id` vs `contract_award_unique_key`). Map between them carefully when joining.
- Raw API payloads are kept in `legislation.legiscan_raw_json` and `contracts.raw_json`.

## Repo conventions

- Weekly runs are committed as "Ran ... notebooks for weekly update <date>", and the commits include the updated `.db`, the CSV exports, and the new raw zip.
