# UAS Project | Weekly API -> MongoDB Data Pipeline and Analysis  
![UAS Project Logo](docs/images/logo.png)

**Table of Contents** <br> 
- [Data Sources](#data-sources)
  - [LegiScan API](#legiscan-api)
  - [USASpending API](#usaspending-api)
  - [Federal Register API](#federal-register-api)
  - [Regulations API](#regulations-api)
- [Repository Layout](#repository-layout)
- [How it works](#how-it-works)
  - [Overview / Architecture](#overview--architecture)
  - [Scheduling](#scheduling)
  - [Pre-run checks](#pre-run-checks)
  - [Snapshots and rollback](#snapshots-and-rollback)
  - [Ingest](#ingest)
  - [Data Model](#data-model)
  - [Validation](#validation)
  - [Outputs](#outputs)
- [Live Database](#live-database)
- [My Building Process](#my-building-process)
- [What's Planned Next](#whats-planned-next)
- [Data Safety](#data-safety)
  - [Backups and Recovery](#backups-and-recovery)
  - [Secrets and Access](#secrets-and-access)
  - [API Terms](#api-terms)
<br> <br>
## Data Sources

To track federal UAS policy, I am using a combination of the LegiScan API and multiple government-based sources. More info on how I use each source can be found in [How it works](#how-it-works).

### LegiScan API

I am using my free tier of LegiScan's Public API to source legislative data. The API covers both federal and state level legislative data, and I am using only the data on US Congress.

**Simplified pipeline:**

1. Query the **`getDatasetList`** endpoint for the dataset_hash and the access_key to the getDatasetRaw endpoint. LegiScan terms require that I download data from getDatasetRaw only when the dataset_hash has changed.
2. Query **`getDatasetRaw`** for a compressed zip of JSON files (one file per bill). Each bill contains a change_hash. Every UAS bill is saved as a weekly snapshot in the **legislation_history** dataset. A bill's entry in the **legislation** dataset is only updated when its change_hash differs from last week's.

**Code:** The code for my LegiScan API request and MongoDB ingest can be found in [legiscan_loader.py](https://github.com/WalkerSimpson1/UAS_Project/blob/main/scripts/legiscan_loader.py).

### USASpending API

I use multiple endpoints from the USASpending API to compile contract data. Specifically, I am gathering data on contracts with the Product Service Code (PSC) of 1550. This is the code for Unmanned Aircraft. The USASpending API requires no account or API key. It must be noted that contract data comes from the Federal Procurement Data System, which is subject to delays of different times.

**Endpoints (all POST):**

1. **`/search/spending_by_transaction/`** -- For individual transactions within contracts. This endpoint discovers which contracts had activity in the past week.
2. **`/search/spending_by_award/`** -- For whole contracts. This endpoint gives contract level details, such as recipient, agencies, total amounts, NAICS code, and dates.
3. **`/transactions/`** -- This returns one contract's transaction history. I use it to fetch the history of contracts found in step 1. I also use it to fetch history of awards whose last modified date is within the last week, but did not have a new transaction recorded.

**Code:** The code for my USASpending requests and MongoDB ingest can be found in [usaspending_loader.py](https://github.com/WalkerSimpson1/UAS_Project/blob/main/scripts/usaspending_loader.py).

### Federal Register API

I use the Federal Register API to gather last week's new rules, proposed rules, and notices. This API requires no account or API key, and returns JSON.

**Endpoint (GET):**

1. **`/api/v1/documents.json`** -- The most important params are my six keywords: ("unmanned aircraft", "unmanned aerial", "drone", "counter-UAS", "UAS", and "advanced air mobility"). Different keywords can return the same data points, so results with matching ids are dropped.

**Code:** The code for my Federal Register requests and MongoDB ingest can be found at [federal_register_loader.py](https://github.com/WalkerSimpson1/UAS_Project/blob/main/scripts/federal_register_loader.py).

### Regulations API

I use the Regulations API to gather docket comments. Dockets are spaces dedicated to a rule, from proposed rulemaking -> interim rule -> final rule. Through a rulemaking's life cycle, members of the public comment their opinions on its docket. This is a great source to determine the stance of different key actors - such as business owners, manufacturers, hobbyists, etc.

The Regulations.gov API is special because it is the only one that depends on another API's output, this being the Federal Register API. This API does require an account, which is free, and returns JSON. The API key comes from [api.data.gov](https://api.data.gov/signup/).

**Endpoints (all GET):**

1. **`/documents?filter[docketId]=<docket>`** -- Used to link the regulations.gov dockets to Federal Register documents by frDocNum.
2. **`/comments?filter[commentOnId]=<objectId>&sort=-lastModifiedDate`** -- This endpoint lists the comments on an individual document. It writes to the rulemaking_comments dataset with the title, posted date, and agency. The docketId is copied from the parent document.
3. **`/comments/{commentId}`** -- This endpoint gets the detail for an individual comment. It writes the organization, submitter_name, and comment_text. It's limited to 300 per weekly run, newest first, across all dockets.

**Code:** The code for my Regulations API request and MongoDB ingest can be found at [regulations_loader.py](https://github.com/WalkerSimpson1/UAS_Project/blob/main/scripts/regulations_loader.py).

## Repository Layout

```text
UAS_Project/
├── weekly_run.sh                  # Weekly entry point: snapshot → ingest → validate → export → dump → Atlas
├── dev.sh                         # tmux dev session (Claude Code, Neovim, mongosh)
├── requirements.txt               # Pinned Python dependencies (installed into .venv/)
├── CLAUDE.md                      # Guidance for Claude Code
├── README.md
├── Exported_SQL_Contracts_Table.csv     # Weekly CSV exports (written by export_csvs.py)
├── Exported_SQL_Legislation_Table.csv
│
├── scripts/                       # ETL pipeline (run from repo root with .venv/bin/python)
│   ├── weekly_ingest.py           # Runs all loaders in order, logs to `runs`
│   ├── legiscan_loader.py         # LegiScan → legislation_history, legislation
│   ├── usaspending_loader.py      # USASpending → contracts, contract_transactions,
│   │                              #   contracts_weekly_transactions_all
│   ├── federal_register_loader.py # Federal Register → rulemakings (fr:)
│   ├── regulations_loader.py      # Regulations.gov → rulemakings (rg:), rulemaking_comments
│   ├── backfill_rulemakings.py    # Wider-window rulemaking backfill (manual)
│   ├── export_csvs.py             # MongoDB → committed CSV exports
│   ├── mongo_common.py            # Shared config, RawStore, parsing helpers
│   ├── mongo_indexes.py           # Index definitions
│   ├── load_contracts_past.py     # One-time: FY2020–2025 bulk CSVs → contracts_past*
│   └── migrate_sqlite_to_mongo.py # One-time: SQLite → MongoDB migration
│
├── analysis/
│   └── USASpending_Past_Visualizations.ipynb   # Read-only obligation/competition charts
│
├── docs/
│   ├── mongodb_schema.md          # Schema spec: collections, indexes, decisions D1–D20
│   └── api_documentation/         # Reference copies of each source's API docs
│       ├── LegiScan_API_User_Manual.md
│       ├── LegiScan_API_Crash_Course.md
│       ├── Federal_Register_API_v1.md
│       ├── Regulations_gov_API_v4.md
│       ├── spending_by_award.md
│       ├── spending_by_award_count.md
│       ├── spending_by_transaction.md
│       └── transactions.md
│
├── systemd/                       # Scheduling and privilege setup
│   ├── install.sh                 # One-time setup (zfs allow, sudoers, linger, units)
│   ├── uas-weekly.service
│   ├── uas-weekly.timer           # Sundays 19:00, Persistent=true
│   └── uas-zfs-prune              # Prunes @weekly-* snapshots only
│
└── usa_spending_old_data/         # USASpending bulk downloads, PSC 1550, FY2020–2025
    ├── base_awards/               # psc1550_fy20XX.csv
    └── transactions/              # psc1550_fy20XX_t.csv
```

Outside the repo (ZFS pool `uaspool`):

```text
/uaspool/
├── mongo/                         # mongod dbPath
├── raw/                           # Write-once raw API responses ($RAW_ROOT)
│   ├── legiscan/<dataset_date>/
│   ├── usaspending/
│   ├── federal_register/<run_date>/
│   └── regulations_gov/<run_date>/
└── backups/                       # mongodump archives + UAS_Tracker-final.db
```

## How it works
### Overview / Architecture
Data is gathered from multiple endpoints of the LegiScan API, the USASpending.gov API, the FederalRegister.gov API, and the Regulations.gov API. <br>
↓ <br>
This data feeds into one local MongoDB database on my ThinkPad X1 Gen 7 running Ubuntu 26.04.1 LTS, which is then used to create the CSV exports, backups, and the cloud database on MongoDB Atlas. <br>
<br>
I chose MongoDB because I am covering MongoDB in my Databases class at LSE, and because I have a MongoDB certification learning course with the GitHub Student Developer Pack. **I am thinking of switching to PostgreSQL**, more info available in [What's Planned Next](#whats-planned-next).

### Scheduling
The weekly run (`weekly_run.sh`, which calls the ingest scripts) runs via systemd. Each run uses two files in systemd, plus a one-time setup script.

**`uas-weekly.timer`:**

```ini
OnCalendar=Sun *-*-* 19:00:00
Persistent=true
Unit=uas-weekly.service
```

**`uas-weekly.service`:**

```ini
Type=oneshot
ExecStart=%h/UAS_Project/weekly_run.sh
SuccessExitStatus=2
Nice=10
IOSchedulingClass=idle
TimeoutStartSec=4h
TimeoutStopSec=10min
```

These two files are user units: they live in `systemd/user/`, so they do **not** run as root. Two things allow this:

1. **Linger** (`loginctl enable-linger`) -- Linger keeps my user systemd units running at boot without a login, so the timer can fire.
2. **Permission for 3 root-level commands:**
   - a. `zfs allow -u walker snapshot,rollback,mount` on `uaspool/mongo` and `uaspool/raw` -- This allows the script to take and roll back ZFS snapshots.
   - b. `uas-zfs-prune` sudoers rule (`NAME_RE='^uaspool/(mongo|raw)@weekly-[0-9]{4}-[0-9]{2}-[0-9]{2}(_[0-9]+)?-(pre|post)$'`) -- This allows specific snapshots to get deleted.
   - c. sudoers rule `walker ALL=(root) NOPASSWD: /usr/bin/systemctl stop mongod, /usr/bin/systemctl start mongod, /usr/local/sbin/uas-zfs-prune` -- This allows the script to start and stop mongod, and to run `uas-zfs-prune`.

**One-time setup command:** `systemd/install.sh` (**run once as the pipeline user**)
- This sets up the scheduling, including linger and the limited root-level command permissions.

**Useful Commands:**
1. **Next run time** -- `systemctl --user list-timers uas-weekly.timer`
2. **Run now** -- `systemctl --user start uas-weekly.service`
3. **Status of last run** -- `systemctl --user status uas-weekly.service`
4. **View systemd's log** -- `journalctl --user -u uas-weekly.service`

### Pre-run checks
Before any run takes a snapshot or changes any data, `weekly_run.sh` checks that everything a run depends on is in its place. Most of these checks are in `preflight()` (`weekly_run.sh:158`).

The script runs in the order: <br>
run lock -> preflight -> wait for network -> pick run tag -> snapshot

- **Run lock** makes sure two runs can't overlap. If the timer fires while the pipeline runs by hand, the second run exits right away.
- **Preflight** makes sure key files exist: the venv, `.env`, and the ZFS datasets. It also checks that mongod is reachable, the sudo rules work, and no earlier run was left unfinished.
- **Wait for network** retries resolving `api.legiscan.com` and `api.usaspending.gov` up to 30 times, 10 seconds apart.
- **Pick run tag** picks the name for the run's snapshots. If the date already has a `-pre` snapshot (from a manual run), it adds a `_2` suffix, and so on.

### Snapshots and rollback
A ZFS snapshot is a read-only image of a dataset at a point in time. ZFS never overwrites data in place, rather it writes changes to new blocks.

I have two datasets snapshotted together per run:
1. **`uaspool/mongo@weekly-<date>-pre`** -- This is MongoDB's data directory.
2. **`uaspool/raw@weekly-<date>-pre`** -- This holds the saved API responses and the `manifest.jsonl` that indexes them.

The LegiScan loader uses the manifest to decide if the dataset has already been downloaded or not, so the two snapshots have to stay consistent with each other.

A `-pre` snapshot is taken right before ingest, and a `-post` is taken right after validation passes.

---

`ROLLBACK_ARMED` is a flag that is on between `-pre` and the end of validation, and undoes everything if a step fails while it is on.
A rollback does this (`rollback()`, `weekly_run.sh:83`):

```bash
sudo -n systemctl stop mongod                      # 1
zfs rollback uaspool/mongo@weekly-<date>-pre       # 2
zfs rollback uaspool/raw@weekly-<date>-pre         # 3
sudo -n systemctl start mongod                     # 4
touch ~/.local/state/uas-weekly/resolved/<tag>     # 5 (only if 1–4 all succeeded)
```

This stops mongod, rolls back the database and raw files, starts mongod again, and marks the run as resolved.

### Ingest
`weekly_run.sh` takes the `-pre` snapshots, and then calls `scripts/weekly_ingest.py`. This script runs the four loader scripts: `legiscan_loader.py`, `usaspending_loader.py`, `federal_register_loader.py`, and `regulations_loader.py`. Each loader saves the raw response to `/uaspool/raw` first, then upserts into MongoDB. Each loader writes its own `runs` document, and `weekly_ingest.py` writes a parent document linking the four.

### Data Model
Everything lives in my `uas_tracker` MongoDB database.

Per policy area:

**Legislation:** `legislation_history` is an append-style dataset, getting a new photo of every bill weekly and putting it on top of old photos. Comparatively, the `legislation` dataset updates a bill's API fields when it has a new `change_hash`, signalling there was an update.

**Rules:** The `rulemakings` dataset holds Federal Register and Regulations.gov documents. Public comments are in `rulemaking_comments`.

**Contracts:** `contract_transactions` is the main dataset. `contracts` is a lookup of awards, and `contracts_weekly_transactions_all` is the 14-day discovery feed. `contracts_past` is from manually downloading CSVs of FY2020–2025 PSC 1550 data.

### Validation
Before ingest, `run_ingest()` counts the documents in every collection and saves the counts to `runs/<tag>/counts_before.txt`. After ingest, it counts again into `counts_after.txt`. `validate_counts()` in `weekly_run.sh` compares the two and checks that:
1. **Nothing shrank** -- each collection has at least as many documents as before.
2. **Required collections are not empty** -- these collections must hold at least one document in total, even if this week added nothing (`legislation_history`, `legislation`, `contracts`, `contract_transactions`, `contracts_weekly_transactions_all`, and `runs`).
3. **Ingest wrote at least one new log document.**

If any rule fails, the datasets roll back to `-pre`. If all three pass, `ROLLBACK_ARMED` turns off and the `-post` snapshot is taken.

### Outputs
Once validation passes, `weekly_run.sh` runs four steps that produce data outputs in different formats and prune old ones.
1. **CSV exports:** `export_csvs.py` writes...
   - `Exported_SQL_Legislation_Table.csv`: one row per bill snapshot
   - `Exported_Legislation_Current.csv`: the latest state of each bill
   - `Exported_SQL_Contracts_Table.csv`: awards with `lookup_status: "ok"`
   - `Exported_Contract_Transactions.csv`: every transaction
2. **mongodump:** The whole database is dumped to `/uaspool/backups/uas_tracker_<date>.archive.gz`.
3. **Prune:** Keeps everything from the last 12 weeks plus the first run of each month for 12 months. Snapshots are deleted only through `uas-zfs-prune`.
4. **MongoDB Atlas:** `mongorestore --drop` loads the mongodump into MongoDB Atlas, so the cloud copy mirrors my local database.

## Live Database
**The Database on MongoDB Atlas updates weekly, not in real time**

Every week, when the scripts run, weekly_run.sh makes a mongodump backup, and restores it to Atlas with mongorestore --drop (--drop deletes each old backup collection before loading the new one).

### Guide to connecting  
<details>
<summary><b>Connect to the live database (read-only)</b></summary>

**Connection string:** `mongodb+srv://uas-policy-database.gg108jz.mongodb.net/uas_tracker` <br>
**Username:** `viewer` · **Password:** `viewer` 

**mongosh:**
```bash
mongosh "mongodb+srv://viewer:viewer@uas-policy-database.gg108jz.mongodb.net/uas_tracker"
```

**Compass:** paste the connection string above with `viewer:viewer@` after `mongodb+srv://`.

**Example queries:**
```javascript
db.legislation.countDocuments()
db.rulemakings.find({ source: "federal_register" }, { raw: 0 }).limit(5)
```

**Main collections:** `legislation`, `contract_transactions`, `contracts`, `rulemakings`, `rulemaking_comments`

</details>

## My Building Process
Before automating the data collection and migrating to MongoDB in early October 2026, I had built this project manually in Python notebooks and SQLite/DB Browser.

**Currently my building workflow consists of...** 
1. Editing CLAUDE.md in **Neovim (with the LazyVim distribution)** on the context and goals of my new feature.
2. Opening multiple Claude agents in **herdr**, often in individual git worktrees.
3. Prompting the Claude agents on the individual sections each handles. 
4. Reviewing the new code and mongod compatibility in a **tmux** session with one pane in Neovim, another running mongod, and another with Claude Code in the main branch to refer to what's in main. 
<br> <br>
**Wanted changes to my workflow**
1. I want to compile a set of the best/ideal prompts to use for specific situations with Claude Code.
2. I have installed the Claude Code Vibewise plugin and "Court of Claude" skill, which aim to reduce learning losses and AI agreeableness. Have yet to use them for serious building.
3. Using yazi to navigate API response and mongod files.

## What's Planned Next
**Apart from keeping up with the weekly updates and familiarizing myself more with these new tools...**
1. Quantitative text-based analysis of docket comments from regulations.gov
2. More advanced historical analysis, specifically forms of regression with contracts/transactions and linking contract locations to congressional district/state sponsor and co-sponsor likelihood.
3. Potentially moving from MongoDB to PostgreSQL. More info on why I chose MongoDB is in the Methodology section of the substack, however it was mainly because it is the cloud-based software covered in my Databases class at LSE, and because I have a MongoDB certification learning course with my LSE GitHub Education Account. PostgreSQL might be a better fit for business applications though.

## Data Safety
### Backups and Recovery
My data is kept in three places: ZFS snapshots on my laptop, `mongodump` archives in `/uaspool/backups`, and the MongoDB Atlas copy. More info in [Snapshots and rollback](#snapshots-and-rollback) and [Outputs](#outputs).

1. **Raw files are write-once** -- Every API response is saved to `/uaspool/raw` before it is loaded, so MongoDB can be rebuilt from them.
2. **Re-running is safe** -- Every write is an upsert keyed on a real ID, so a re-run can't create duplicates.
3. **Known gap** -- The raw files do not have an off-laptop copy yet. Atlas only holds the documents.

### Secrets and Access
1. **API keys** -- My keys and connection strings live in a gitignored `.env` and are never printed or logged. Errors go through `redact()` first.
2. **Localhost only** -- mongod only listens on 127.0.0.1, not the network.
3. **Least privilege** -- The pipeline runs as my user, not root. The sudo rules only allow starting and stopping mongod and running `uas-zfs-prune`, which only deletes `@weekly-*` snapshots.
4. **Kept out of git** -- The database and raw API responses are not committed. Only the CSV exports are.

### API Terms
1. **LegiScan** -- Their terms require that I only download the dataset when the dataset_hash has changed.
2. **Regulations.gov** -- The API allows 1000 requests per hour, so I fetch at most 300 comment details per run and stop early if the rate limit runs low.
