# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A U.S. drone (UAS) policy tracker and a government-affairs portfolio piece. A weekly pipeline pulls LegiScan, USASpending.gov (PSC 1550), the Federal Register and Regulations.gov into **PostgreSQL** (Docker, this Windows laptop), turns new and changed items into **policies** sorted into loose **tracks**, drafts a first-pass analysis of each with `claude -p`, and publishes a static dashboard (`site/`, GitHub Pages). The published analyses live on Substack (format: `docs/format.md`).

Decisions for this setup are P1+ in `docs/DECISIONS.md`. The MongoDB era (D1–D21) is in `docs/mongodb_schema.md`, kept as history; its code is in git history (branch `main` before the `postgres-docker` work).

There is no test suite or linter. Dependencies are pinned in `requirements.txt`, installed into `.venv/` (`.venv/Scripts/python` on Windows).

## Setup

- `.env` (gitignored, see `.env.example`): `LEGI_API_KEY`, `REGULATIONS_API_KEY` (sent only in the `X-Api-Key` header), `POSTGRES_*`, `DATABASE_URL`, `RAW_ROOT` (default `./data/raw`). Never print or log these values; `pg_common.redact()` strips keys from error text.
- `docker compose up -d postgres` — Postgres 18, `127.0.0.1:5432`, data in the `pgdata` volume. Docker Desktop should start at login.
- `.venv/Scripts/python scripts/db_migrate.py` applies `db/migrations/*.sql` in order (each records itself in `schema_migrations`). Add a new numbered file; never edit an applied one.
- `data/` is gitignored: `raw/` (write-once API responses, indexed in `raw_files`), `backups/` (pg_dump), `logs/`, `ai/` (cwd for `claude -p`), `import/` (the one-time mongodump).
- Git Bash mangles container paths (`/migrations/...`); use `MSYS_NO_PATHCONV=1` for `docker compose exec`.

## Weekly run

`scripts/weekly_run.py` runs from the Task Scheduler task "UAS weekly" (`scripts/install_task.ps1`: daily 19:00 + at logon, catch-up after missed starts). It exits at once if a weekly run succeeded in the past 6 days, otherwise widens the lookback windows to cover the gap. Steps: preflight → `pre-<date>.dump` → the four loaders (each logs a `runs` row; parent row `pipeline='weekly'`) → `sync_policies` → `summarize` (≤15 drafts, warnings only) → no-table-shrank check → `export_site` (+ commit/push of `site/data` with `--publish`) → `uas_tracker_<date>.dump` (keeps 12). Exit 0 ok, 2 warnings, 1 failed. Logs: `data/logs/weekly-<date>.log`. A failure keeps successful steps' data (all writes are upserts) and logs the `pg_restore` command for the pre dump.

## Code layout (`scripts/`, run from the repo root)

- `pg_common.py`: config, `connect()`, date helpers, `RawStore` (raw file + `raw_files` row via its own autocommit connection), `start_run`/`finish_run`, `upsert()` (keeps an unchanged row's `raw_source`), `read_sql()`.
- Loaders (source tables only, each safe to re-run): `legiscan_loader.py` (bills, bill_snapshots; keyword filter + `WATCH_BILLS` + `WATCH_TITLE_RE` for the NDAA and DoD appropriations), `usaspending_loader.py` (contract_activity_feed, contract_transactions, contract_awards; PSC 1550 plus a counter-UAS `keyword_search` across all PSC codes; steps run in their own transactions), `federal_register_loader.py` (keeps a document if a keyword is in its title/abstract, or if it's from DoD/BIS/OFAC/USTR: `is_relevant`), `regulations_loader.py` (dockets of this run's FR documents; comment details capped per run by the 1000/hour limit).
- `sync_policies.py`: source rows → `policies` / `policy_sources` (companion bills by title; documents by docket/RIN/FR number), `policy_events` (unique per policy/date/type/source), `analyses` rows, rule-based `policy_tracks`.
- `summarize.py`: `claude -p --tools "" --json-schema ...` with a per-kind fact sheet; drafts only policies in an `active` track (`--all` for the rest); writes `analyses.ai_*`, sets `ai_drafted`, adds `assigned_by='ai'` track suggestions. Never overwrites human fields.
- `export_public_db.py`: `site/data/uas_public.db` + `metadata.json` (recommended queries, shown with copy/run buttons on `site/index.html#query`).
- `backfill.py`: one-off FR / Regulations.gov / bulk-contracts backfills (P19).
- `export_site.py`: `site/data/*.json` for `site/index.html`, a one-page weekly analytics dashboard (P25: headline numbers, obligations by FY, policies by lever, top recipients) plus the query section. The tracker itself is the Substack; the site has no policy lists. Deletes stale JSON from older layouts.
- `migrate_mongo_to_pg.py`: the one-time import (done 2026-11-23, P9).
- `analysis/USASpending_Past_Visualizations.ipynb` reads Postgres via `pg_common.read_sql`.

## Data notes

- Fiscal year starts Oct 1 (`pg_common.fiscal_year`).
- Postgres regex word boundary is `\y`, not `\b` (track_rules).
- Tracks: one `active` track is the published focus; `draft` tracks are a watch list; `archived` ones are kept with rules off.
- Levers (P24): each policy gets one `policies.lever` (funding, procurement, trade, foreign-bans, other) from `lever_rules` via the SQL function `assign_levers()`, run in `sync_policies`. Tune rules with a new migration; `lever_set_by = 'human'` pins a hand-set lever.
- Themes (P27): story tags, many per policy, in `policy_themes` from `theme_rules` via `assign_themes()` (also in `sync_policies`). Rule tags follow the rules; `assigned_by = 'human'` rows are kept (`excluded = true` vetoes a rule tag).
- `contract_awards.source = 'keyword_search'` rows are counter-UAS awards outside PSC 1550; don't add them to 1550 totals.
- `past_*` tables are the bulk downloads, FY2020 on (FY2026+ via `backfill.py contracts`); the live `contract_*` tables only hold transactions on awards the pipeline touched, so don't sum them as a market total.
- Human workflow lives in the analysis tables: review `review_queue`, edit `analyses` (status → `in_review` / `published` / `not_relevant`, `substack_url`), confirm or change `policy_tracks`. `not_relevant` policies drop off the dashboard.

## Repo conventions

- Commit code and `site/` (the dashboard data commits are "Weekly dashboard data <date>"). Never commit `.env`, `data/`, or dumps.
- Log routine design choices in `docs/DECISIONS.md` instead of asking.
