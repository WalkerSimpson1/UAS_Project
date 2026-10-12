# UAS Project | Weekly API -> MongoDB Data Pipeline and Analysis  
![UAS Project Logo](docs/images/logo.png)

**Table of Contents** <br> 
- [Data Sources](#data-sources)
- [Repository Layout](#repository-layout)
- [How it works](#how-it-works)
- [Live Database](#live-database)
- [My Building Process](#my-building-process)
- [Whats Planned Next](#whats-planned-next)
- [Data Safety](#data-safety)
<br> <br>
## Data Sources

To track federal UAS policy, I am using a combination of the LegiScan API and multiple government-based sources. More info on how I query each source can be found in [How it works](#how-it-works).

### LegiScan API

I am using my free tier of LegiScan's Public API to source legislative data. The API covers both federal and state level legislative data, and I am using only the data on US Congress.

**Simplified pipeline:**

1. Query the **`getDatasetList`** endpoint for the dataset_hash and the access_key to the getDatasetRaw endpoint. LegiScan terms require that I download data from getDatasetRaw only when the dataset_hash had changed.
2. Query **`getDatasetRaw`** for a compressed zip of JSON files (one file per bill). Each bill contains a change_hash. Every UAS bill is saved as a weekly snapshot in the **legislation_history** dataset. A bill's entry in the **legislation** dataset is only updated when its change_hash differs from last week's.

**Code:** The code for my LegiScan API request and MongoDB ingest can be found in [legiscan_loader.py](https://github.com/WalkerSimpson1/UAS_Project/blob/main/scripts/legiscan_loader.py).

### USASpending API

I use multiple endpoints from the USASpending API to compile contract data. Specifically, I am gathering data on contracts with the Product Service Code (psc) of 1550. This is the code for Unmanned Aircraft. The USASpending API requires no account or API Key. It must be noted that contract data comes from the Federal Procurement Data System, which is subject to delays of different times.

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

I use the Regulations API to gather docket comments. Dockets are spaces dedicated to a rule, from proposed rulemaking -> interim rule -> final rule. Through a rulemakings life cycle members of the public comment their opinions on its docket. This is a great source to determine the stance of different key actors - such as business owners, manufacturers, hobbyists, etc.

The Regulations.gov API is special because it is the only one that depends on another API's output, this being the Federal Register API. This API does require an account, which is free, and returns JSON. The API key comes from [api.data.gov](https://api.data.gov/signup/).

**Endpoints (all GET):**

1. **`/documents?filter[docketId]=<docket>`** -- Used to link the regulations.gov dockets to Federal Register documents by frDocNum.
2. **`/comments?filter[commentOnId]=<objectId>&sort=-lastModifiedDate`** -- This endpoint lists the comments on an individual document. It writes to the rulemaking_comments dataset with the title, posted date, and agency. The docketid is copied from the parent document.
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
│   └── USASpenging_Past_Visualizations.ipynb   # Read-only obligation/competition charts
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

## Live Database

## My Building Process

## Whats Planned Next

## Data Safety
