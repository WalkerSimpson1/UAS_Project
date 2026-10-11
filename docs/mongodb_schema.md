# MongoDB Schema Proposal

Status: proposal only. No code has changed. The notebooks still write to `UAS_Tracker.db`.
Revision 7 (2026-10-11). Regulations.gov loader: field mappings in 3.3 and 3.3a, its `runs` document in 8.5, and a rulemaking loader's `"partial"` is a warning for the weekly run (8.5).
Revision 6 (2026-10-11). Adds D17: `rulemakings` takes API documents from the Federal Register (and later Regulations.gov) beside the hand-entered rows, and `rulemaking_comments` from Regulations.gov (sections 3.3, 3.3a, 4, 7, 8.4, 8.5).
Revision 5 (2026-10-10). Adds D13–D16: an off-laptop backup that cut-over waits for (tentatively Azure Blob Storage), `uaspool/backups`, raw files outside the repo and out of git, and deferring the coverage-pull decision.

## 0. Decisions this revision is based on

| # | Decision | Effect on this design |
|---|---|---|
| D1 | Weekly legislation snapshots are **intentional** | `legislation_history` is **required** and holds one document per bill per weekly run. All 287 existing rows migrate into it. `legislation` holds the latest state per bill. |
| D2 | MongoDB runs **locally on this laptop, on a ZFS dataset** | Section 6 covers host layout, snapshots, and backups. The concrete layout is D9. |
| D3 | **Stop committing `UAS_Tracker.db`** to git | CSV exports stay in git as the readable history. Raw API files leave git too (D15). The DB and raw files are backed up by ZFS snapshots, `mongodump`, and an off-laptop copy (section 6.1). |
| D4 | **Dollar amounts stay floats** (BSON `double`) | Matches today's SQLite `REAL`. The int/real mix in `contracts.amount_usd` is normalized to `double`. |
| D5 | **Run SQLite and MongoDB side by side for two weeks** and compare | Section 9 covers the cut-over plan and comparison checks. |
| D6 | **Transactions are the primary record; awards are a lookup table** | `contract_transactions` is the system of record. `contracts` exists to describe the awards that transactions point to. Every transaction's `award_key` must resolve to a `contracts` document (section 3.4, section 8). |
| D7 | **Weekly run order: transactions → awards → backfill** | Transactions are pulled first. The award search runs next. Then any `award_key` seen in transactions but missing from `contracts` is fetched individually (section 8). |
| D8 | **Test the `award_unique_id` filter** against known `CONT_AWD_` values | Added to the parallel-run checks (section 9.1). The backfill depends on this filter, and section 8.3 gives a fallback if the test fails. |
| D9 | **ZFS pool `uaspool`**: dataset `uaspool/mongo` is MongoDB's `dbPath`, and dataset `uaspool/raw` holds the raw API files | Section 6 (datasets, snapshots) and section 7 (raw files now live on `uaspool/raw`). |
| D10 | **Compression: MongoDB's built-in** (WiredTiger). ZFS `compression=off` on `uaspool/mongo`, `lz4` on `uaspool/raw` | Section 6. No double compression of DB files. Raw JSON is compressed by ZFS, so no gzip step is needed. |
| D11 | **Fetch award-level PSCs** | `PSC` is a documented contract field of `spending_by_award`, so it is added to the field list of the award search (step 3) and backfill (step 4) with **no extra requests**. `contracts.psc_code` comes from the API for every award instead of being hardcoded `"1550"` (section 3.4.3). |
| D12 | **14-day transaction lookback** for late postings | Step 1 searches the past 14 days by `action_date`. The 7-day overlap with last week is harmless because writes are upserts. `weekly_rank()` filters to the latest 7 days (sections 3.4.2, 8.2). |
| D13 | **Off-laptop backup required; cut-over waits for it.** Until it exists, local `mongodump` only. *Tentative:* Azure Blob Storage, starting around Monday 2026-10-12. Neither the provider nor the date is confirmed | Section 6.1 (layers), 6.2 (tentative Azure layout and restore test), section 9 (cut-over gate), section 10 (open). |
| D14 | **Add dataset `uaspool/backups`** for `mongodump` archives | Section 6. |
| D15 | **Raw files: `uaspool/raw` mounted at `/uaspool/raw`, outside the repo. `raw/` added to `.gitignore`.** Git handles large, growing files badly; ZFS snapshots and the off-laptop backup cover them | Section 7. Raw files are no longer committed. `raw_source` paths are relative to `RAW_ROOT`. Existing LegiScan zips move out of the repo. |
| D16 | **Coverage pull (step 3b): decide after the parallel run** | Step 3b runs during both parallel weeks. Decision criteria are in section 9. |
| D17 | **`rulemakings` holds API documents beside the hand-entered rows.** Federal Register documents get `_id: "fr:<document_number>"`, Regulations.gov documents `"rg:<documentId>"`. A rule can appear in both sources as two documents | Section 3.3. Hand rows have no `source` field, and `Exported_Rulemakings.csv` and the migration count check read only those. Comments go in `rulemaking_comments` with `_id: "rgc:<commentId>"` (3.3a). |

Note: `docs/design_decisions.md` was referenced earlier but does not exist in the repo, on any branch, or in either worktree (`UAS_Project-automation`, `UAS_Project-migration`). The decisions above come from the requests that started each revision. If that file is written later, it should be checked against this table.

## 1. What exists today

`UAS_Tracker.db` in the repo root. Row counts are from 2026-10-10.

| SQLite table | Rows | Key today | Written by | Write mode |
|---|---|---|---|---|
| `legislation` | 287 | `id` autoincrement | `LegiScan2.ipynb` (+ hand entry) | plain `INSERT`, one row per bill per run |
| `rulemakings` | 0 | `id` autoincrement | hand entry only | n/a |
| `contracts` | 541 | `api_internal_id` (PK) | `USASpending1.ipynb` | `INSERT OR REPLACE` |
| `contract_transactions` | 39 | `transaction_id` (PK) | `USASpending2.ipynb` | `INSERT OR REPLACE` |
| `contracts_past` | 2,109 | none (unique in practice on `contract_award_unique_key`) | `USASpending_Past_Awards.ipynb` | `to_sql(replace)` |
| `contracts_past_transactions` | 6,346 | none (unique in practice on `contract_transaction_unique_key`) | `USASpending_Past_Transactions.ipynb` | `to_sql(replace)` |
| `contracts_weekly_transactions_all` | 14 | (`generated_internal_id`, `mod`) | `USASpenging_Past_Visualizations.ipynb` | `INSERT OR REPLACE` |

The current flow is **awards first**. `USASpending1` pulls awards, and `USASpending2` then pulls `/api/v2/transactions/` only for contracts whose `last_modified_date` falls in the past 7 days. Transactions on awards that `USASpending1` didn't return are never fetched. D6/D7 reverse this.

What the data shows:

- **`legislation` contains 4 weekly snapshots.** It has 287 rows, 72 distinct `bill_id`s, and runs on `date_added` 2026-09-11 (71 rows), 09-17 (72), 09-22 (72), and 09-28 (72). Bill `2157929` first appears on 09-17. (`bill_id`, `date_added`) is unique across all 287 rows, so it is a safe snapshot key. Four bills changed `change_hash` between snapshots: `2031156`, `2151755`, `2155311`, and `2157929`, for 77 distinct (bill, hash) versions. All rows are `state = 'US'` and `session_id = 2199`.
- All rows currently have `source_platform = 'LegiScan'`. CLAUDE.md allows hand-entered rows, so the design supports rows with no `bill_id`.
- **Half of this week's transactions have no award row.** 7 of the 14 `contracts_weekly_transactions_all` award keys are missing from `contracts`. All 7 are new FY2026 awards with no parent IDV (e.g. `CONT_AWD_19GT5026P0853_1900_-NONE-_-NONE-`, `CONT_AWD_70B06C26C00000158_7014_-NONE-_-NONE-`). These are exactly what the D7 backfill is for.
- `USASpending2` requests `/api/v2/transactions/` with `limit: 250` and reads one page only. An award with more than 250 transactions would be cut off silently. The new design pages until `hasNext` is false.
- `contracts.amount_usd` is a mix of SQLite `integer` (220 rows) and `real` (321 rows).
- `contracts.psc_code` is `"1550"` on all 541 rows. `USASpending1` doesn't request the `PSC` field, so this value comes from the search filter, not from the award itself.
- Dates are `TEXT` in two formats: `YYYY-MM-DD` and `YYYY-MM-DD HH:MM:SS`.
- Largest raw payloads: `legislation.legiscan_raw_json` is about 67 KB and `contracts.raw_json` is about 1.2 KB. Both are far below the 16 MB document limit.

## 2. Design principles

1. **One collection per table, with the same field names**, plus the required `legislation_history`. Notebooks and CSV exports can switch over with few edits.
2. **Transactions are primary; awards are lookups (D6).** Data flows from transactions to awards: a transaction is never dropped because its award is unknown. Instead, the award is backfilled. Award documents may be partial (`lookup_status: "pending"`), but transaction documents are always complete as received.
3. **Natural `_id`s.** The upsert key is also the primary key, so duplicates cannot be stored.
4. **Raw payloads are embedded documents** (`raw`), not JSON strings, so fields inside them can be queried.
5. **Native types.** Dates become BSON `Date`. Money is `double` (D4). IDs stay strings (section 5.3).
6. **Raw files are the source of truth** (section 7). They live on `/uaspool/raw` with an off-laptop copy (D13), not in git (D15). MongoDB can be rebuilt from them, which matters more now that the DB is not in git (D3). `raw_source` paths are relative to `RAW_ROOT`.
7. Every API-fed document gets `first_seen` / `last_seen`. `date_added` is kept for CSV continuity.

Database name: `uas_tracker`.

## 3. Collections

### 3.1 `legislation_history` (required, one document per bill per weekly run)

This is the snapshot record (D1). It is append-only: a snapshot is never updated after the week it was taken.

```js
{
  _id: { bill_id: "2031156", snapshot_date: ISODate("2026-09-22") },
  bill_id: "2031156",
  snapshot_date: ISODate("2026-09-22"),   // run date (= SQLite date_added) for migrated rows
  dataset_date: ISODate("2026-09-20"),    // LegiScan dataset the snapshot came from (see 3.1a)
  session_id: 2199,
  state: "US",
  // tracker fields as they were in that snapshot:
  item_name, identifier, body, sequence, event_date, event_description,
  track, source_url, source_platform, notes,
  legiscan_change_hash: "3f9b822245a0a5307d1c1f396973217c",
  changed_since_prev: true,               // change_hash differs from this bill's previous snapshot
  raw: { /* full LegiScan bill object */ },
  raw_source: "legiscan/2026-09-20/getDatasetRaw2026-09-20.zip",
  date_added: "2026-09-22",               // original SQLite value, kept verbatim
  sqlite_id: 145                          // original legislation.id, migrated rows only
}
```

- The `_id` (`bill_id`, `snapshot_date`) makes re-running a week harmless: the same snapshot can't be stored twice. Running a week twice was the risk with the old plain `INSERT`. Weekly snapshots themselves are intended.
- `changed_since_prev` is computed at load time. It lets you query "what actually changed this week" without comparing hashes.
- Size: about 72 bills × ≤ 67 KB × 52 weeks ≈ 250 MB per year at most, and much less in practice. With WiredTiger compression (D10), this is not a concern.

#### 3.1a Snapshot date vs dataset date (migration detail)

Existing rows only record the run date (`date_added`), not which LegiScan zip they came from. The repo has zips for 2026-09-06, 09-13, 09-20, and 09-27, which pair with runs 09-11, 09-17, 09-22, and 09-28 (the latest zip on or before each run). Migration sets `dataset_date` to that zip date. It then **verifies** the pairing by checking that each row's `legiscan_change_hash` equals the hash for that `bill_id` inside the paired zip. Any mismatch is reported, `dataset_date` is left null for that row, and migration continues.

Going forward, `snapshot_date` and `dataset_date` are both recorded at load time. `dataset_date` comes from `getDatasetList`.

### 3.2 `legislation` (latest state, one document per bill)

This is the current view used for tracking and the CSV export. It is derived from the newest snapshot, plus hand-maintained fields.

```js
{
  _id: "legiscan:2031156",          // ObjectId for hand-entered rows (no bill_id)
  bill_id: "2031156",               // null for hand-entered rows
  state: "US",
  item_name, identifier, body, source_url, source_platform,
  legiscan_change_hash: "3f9b...",
  raw: { /* bill object from latest snapshot */ },
  raw_source: "legiscan/2026-09-27/getDatasetRaw2026-09-27.zip",
  latest_snapshot: ISODate("2026-09-28"),   // -> legislation_history._id.snapshot_date
  // hand-maintained (never overwritten by the pipeline):
  sequence, track, event_date, event_description, notes,
  date_added: "2026-09-11",         // = first_seen, for CSV continuity
  first_seen: ISODate("2026-09-11"),
  last_seen:  ISODate("2026-09-28"),
  last_changed: ISODate("2026-09-22")
}
```

**CSV export decision:** `Exported_SQL_Legislation_Table.csv` has been one row per snapshot (287 rows). To keep the git-log history identical during the parallel run (section 9), export it **from `legislation_history`**, sorted by `snapshot_date` then `bill_id`. An optional second file, `Exported_Legislation_Current.csv`, can come from `legislation`.

### 3.3 `rulemakings`

Two kinds of document share this collection (D17). Tell them apart by `source`.

**Hand-entered rows** (no `source` field). Same tracker shape as `legislation`, plus `statutory_deadline: ISODate | null`. `_id` is an ObjectId, and `identifier` has a unique partial index. These are the only rows in `Exported_Rulemakings.csv` and the only rows the migration compares against SQLite (`find({source: null})`).

**API documents** (`source` set), one per document per source, with no weekly snapshots. The prefix on `_id` names the source, so a rule published in the Federal Register and docketed on Regulations.gov is two documents. Join them on `docket_id`.

```js
// Federal Register (scripts/federal_register_loader.py)
{
  _id: "fr:2026-20531",              // "fr:" + document_number
  source: "federal_register",
  document_number: "2026-20531",
  title: "...", abstract: "...",
  document_type: "Notice",           // API `type`: display name ("Rule", "Proposed Rule", "Notice", "Presidential Document"), not the RULE/PRORULE search codes
  publication_date: "2026-10-07",    // ISO date strings, as the API sends them; null when absent
  effective_date: null,              // from the API field `effective_on` (the docs' `effective_date` is rejected as a field name)
  comments_close_on: null,
  agency_names: ["Justice Department"],
  docket_id: "CPCLO Order No. 010-2026",   // docket_ids[0] or null
  docket_ids: ["CPCLO Order No. 010-2026"],
  rin: null,                         // regulation_id_numbers[0] or null
  cfr_references: [],
  significant: null,                 // true / false, or null when the API doesn't say (most notices)
  html_url: "...", pdf_url: "...",
  matched_keywords: ["unmanned aircraft"],  // every search phrase that has returned this document; only grows
  first_seen: ISODate, last_seen: ISODate,  // midnight of the run date (mongo_common.day()), so a same-day re-run is a no-op
  raw: { ... }                       // the full document object from the response, unmodified
}
```

- **Date fields are ISO strings, not BSON Dates.** This is a deliberate exception to principle 5. Both APIs send strings, and each loader stores them exactly as sent. Federal Register dates are date-only (`"2026-10-07"`). Regulations.gov dates are UTC timestamps (`"2026-10-07T03:59:59Z"`, which is Oct 6 Eastern), so they are not truncated to a day. Strings from the two sources still sort together, but a date-only value and a UTC timestamp for the same day can differ by the Eastern offset. `first_seen` / `last_seen` are BSON Dates, as everywhere else.
- **Weekly search:** one `GET /documents.json` per phrase (`unmanned aircraft`, `unmanned aerial`, `drone`, `counter-UAS`, `UAS`, `advanced air mobility`) over `publication_date` from 9 days back through today, `per_page=1000`, `order=newest`. The 2-day overlap with last week's window re-upserts the same `_id`s. Documents are de-duplicated by `document_number` across phrases.
- **Multi-word phrases are sent in double quotes** (`"unmanned aircraft"`, `"unmanned aerial"`, `"advanced air mobility"`) for an exact-phrase match. Unquoted, the API matches documents that contain all the words anywhere: over 2026-07-01 to 2026-10-11, `advanced air mobility` returned 11 documents against 2 quoted, and `unmanned aircraft` 39 against 30. `drone`, `counter-UAS` and `UAS` are sent as-is (quoting `counter-UAS` returned the same 2 documents). `matched_keywords` stores the phrases without quotes. Single words still catch documents that mention drones only in passing, so an exclusion pass is expected later.
- **Upsert:** `$set` the fields above and `last_seen`, `$addToSet` the run's matching phrases into `matched_keywords`, and `$setOnInsert` `first_seen`.
- **Regulations.gov** (`rg:<documentId>`, `source: "regulations_gov"`, `scripts/regulations_loader.py`) fills the same common fields from its own response. It chooses which dockets to load by reading Federal Register documents (`source: "federal_register"`) by `last_seen` (the run date) and `publication_date` (the Federal Register loader's window), which is what the `{ source: 1, last_seen: -1 }` index serves. A weekly run therefore follows exactly the documents the Federal Register loader just upserted. Mapping from one `/v4/documents` list item:

```js
{
  _id: "rg:FAA-2026-9077-0003",      // "rg:" + documentId (the item's id)
  source: "regulations_gov",
  document_id: "FAA-2026-9077-0003",
  object_id: "09000064b956b741",     // attributes.objectId: what /v4/comments filters on
  title: "...",
  document_type: "Notice",           // attributes.documentType ("Rule", "Proposed Rule", "Notice", "Other", ...)
  publication_date: "2026-09-16T04:00:00Z",   // attributes.postedDate
  comment_start_date: "2026-09-16T04:00:00Z", // attributes.commentStartDate
  comments_close_on: "2026-10-07T03:59:59Z",  // attributes.commentEndDate
  open_for_comment: false,           // attributes.openForComment
  agency_id: "FAA",
  agency_names: ["FAA"],             // the list has only the acronym, not the FR's full agency name
  docket_id: "FAA-2026-9077", docket_ids: ["FAA-2026-9077"],
  rin: null,                         // not in the list response
  fr_document_number: "2026-18984",  // the FR document whose docket led here: the one matching
                                     // attributes.frDocNum, else the only FR document on that docket, else null
  first_seen: ISODate, last_seen: ISODate,
  raw: { ... }                       // the list item, unmodified
}
```

### 3.3a `rulemaking_comments`

Public comments from the Regulations.gov API (`scripts/regulations_loader.py`), one document per comment. Comments are kept out of `rulemakings` so that collection stays one document per regulatory action, and so the weekly count check can tell one new rule from one rule plus 40,000 comments.

```js
{
  _id: "rgc:FAA-2026-9077-0004",       // "rgc:" + commentId
  source: "regulations_gov",
  comment_id: "FAA-2026-9077-0004",
  comment_on_id: "09000064b956b741",   // the commented-on document's attributes.objectId (not its documentId); /v4/comments filters on this
  docket_id: "FAA-2026-9077",          // from the commented-on document (the list item has no docketId); joins to rulemakings.docket_id
  agency_id: "FAA",
  title: "Comment from ...",
  posted_date: "2026-10-07T04:00:00Z", // the API's ISO string, UTC
  // From GET /v4/comments/{id}, once fetched:
  organization: "...",                 // null for individuals
  submitter_name: null,                // firstName + lastName, null when neither is public
  comment_text: "See attached file(s)",  // may be truncated by the API for long submissions; a short value is not an error
  raw_detail: { ... },                 // the detail response's data object
  detail_fetched_at: ISODate | unset,  // set once the details have been fetched
  first_seen: ISODate, last_seen: ISODate,
  raw: { ... }                         // the list item, unmodified
}
```

- **Two-stage load.** The `/v4/comments` list items have no organization, submitter name, comment text or `docketId`. Those come only from `GET /v4/comments/{id}`, one request per comment, and the API allows 1000 requests per hour. Each run fetches up to `--max-comment-details` (default 300) details, newest first. The rest keep `detail_fetched_at` unset and are fetched in later runs, using the `{ detail_fetched_at: 1, posted_date: -1 }` index. A comment still waiting for its details is not an error.
- **Paging:** `meta.pageNumber` is 1-based and paging runs while `meta.hasNextPage` is true. The API stops at 20 pages × 250 results, and `runs` records `page_cap_hit` when a list reaches that limit.

### 3.4 Live contract data: transactions first, awards as lookup (D6)

How the three live collections relate:

```
contracts_weekly_transactions_all   ──discovers award_keys──┐
   (spending_by_transaction, past 14 days)                  │
                                                            ▼
contract_transactions  ◄── PRIMARY RECORD ── /api/v2/transactions/ per award_key
   │ award_key (must resolve)
   ▼
contracts  ◄── LOOKUP ── spending_by_award (FY search)  +  backfill by award_unique_id
```

#### 3.4.1 `contract_transactions` (primary record, from `/api/v2/transactions/`)

One document per transaction. This is what reports, sums, and the CSV transaction history are built on.

```js
{
  _id: "CONT_TX_9700_9700_W900KK26FA047_0_W900KK22D0010_0",   // = response "id" = transaction_id
  transaction_id: "CONT_TX_...",
  txn_key: "9700_9700_W900KK26FA047_0_W900KK22D0010_0",       // _id without "CONT_TX_", join key (section 5)
  api_internal_id: "CONT_AWD_W900KK26FA047_9700_W900KK22D0010_9700",
  award_key: "CONT_AWD_W900KK26FA047_9700_W900KK22D0010_9700", // -> contracts._id (required to resolve)
  type, action_type, action_date: ISODate, description,
  modification_number: "0",           // docs say string, example shows null; store null as-is
  federal_action_obligation: 1234.5,  // double, nullable per docs
  discovered_via: "weekly_feed" | "award_modified" | "backfill",  // why this award's history was pulled
  date_added: "2026-09-20",
  raw: { /* TransactionResult */ },
  raw_source: "usaspending/transactions/2026-10-11/CONT_AWD_W900KK26FA047_....json",
  first_seen, last_seen
}
```

- **Which awards get a transaction pull:** the union of (a) every `award_key` returned by this run's step 1 feed (14-day window, D12), and (b) every `contracts` document whose `last_modified_date` is in the past 7 days. (b) keeps today's `USASpending2` coverage, so the parallel-run comparison is a strict superset (section 9). Keep (b) after cut-over only if it keeps finding transactions that (a) missed. `runs` records how many came from each.
- The full history of each selected award is pulled, paging until `hasNext` is false. This also picks up corrections to older modifications.

#### 3.4.2 `contracts_weekly_transactions_all` (weekly discovery feed, from `spending_by_transaction`)

The transaction search with the project's filters (PSC 1550, award types A–D, **`action_date` in the past 14 days**, D12). It is pulled **first** each week. It is both the list of awards with recent activity and the input to `weekly_rank()`.

**Why 14 days:** per `spending_by_transaction.md`, `time_period` filters on the transaction's `action_date`. An agency can post a transaction to USASpending days after its action date. A 7-day window misses anything posted more than 7 days late, because by the next run its action date has already left the window. With 14 days, each action date is searched in two consecutive weekly runs. The overlap just re-upserts the same `{generated_internal_id, mod}` keys and updates `last_seen`.

**`weekly_rank()` must filter to the latest 7 days by `action_date`.** The collection now holds 14 days of recent rows (plus all earlier weeks), and the ranking compares one week against the ±2-week historical window. A row first seen in the second week of its window (`first_seen` later than `action_date` + 7 days) is a late posting. `runs` counts these (section 8.5), which shows over time whether 14 days is enough.

```js
{
  _id: { generated_internal_id: "CONT_AWD_...", mod: "0" },
  award_key: "CONT_AWD_...",          // -> contracts._id (required to resolve)
  award_id, action_date: ISODate, date_added, recipient_name, recipient_uei,
  awarding_agency, award_type, action_type, transaction_description,
  psc_code: "1550",                   // transaction-level "PSC" field, now requested (D11)
  transaction_amount: 40000000.0,     // API returns a STRING ("40000000.00"); parse to double
  late_posting: false,                // true if first_seen > action_date + 7 days
  raw: { /* TransactionResponse row */ },
  raw_source: "usaspending/weekly_transactions/2026-10-11/page_001.json",
  first_seen, last_seen
}
```

Per `spending_by_transaction.md`, **every** response field is typed `string, nullable`, including `Transaction Amount` and `internal_id` (which is a number in `spending_by_award`). The loader must parse amounts to `double`, and must reject rows where `generated_internal_id` or `Mod` is null, since both are part of `_id`. This feed has no transaction ID, so it is not merged into `contract_transactions`. The `/transactions/` pull for the same award provides the keyed record.

#### 3.4.3 `contracts` (lookup table, one document per award)

`contracts` no longer means "awards returned by the FY search". It means **every award that any live transaction points to**, plus the FY search results.

```js
{
  _id: "CONT_AWD_W91ZRS26FC010_9700_47QSMS25D008P_4732",   // = generated_internal_id = award_key
  api_internal_id: "CONT_AWD_W91ZRS26FC010_9700_47QSMS25D008P_4732",  // kept for existing code / CSV
  award_key: "CONT_AWD_W91ZRS26FC010_9700_47QSMS25D008P_4732",
  award_id: "W91ZRS26FC010",          // "Award ID" = PIID
  recipient, awarding_agency, naics_code, description, notes,
  psc_code: "1550",                   // award-level, from the API's "PSC" field (D11)
  amount_usd: 123456.78,              // double
  award_base_date: ISODate("2026-05-22"),
  last_modified_date: ISODate("2026-08-07T07:03:05Z"),
  source: "award_search" | "backfill",
  lookup_status: "ok" | "pending",    // "pending" = referenced by a transaction, not yet resolved
  backfill: {                         // present only when source = "backfill" or status = "pending"
    method: "award_unique_id" | "award_ids_exact",
    first_attempt: ISODate, last_attempt: ISODate, attempts: 1,
    referenced_by: "contracts_weekly_transactions_all" | "contract_transactions"
  },
  raw: { internal_id, generated_internal_id, "Award ID", ... },
  raw_source: "usaspending/awards/2026-10-11/page_003.json",   // or .../award_backfill/...
  first_seen, last_seen
}
```

- **Pending stubs.** If backfill can't resolve a key, the loader writes a stub: `_id`, `award_key`, `award_id` (taken from the transaction), `lookup_status: "pending"`, and `backfill.*`. Transactions then always have something to join to, and the key is retried every week. A stub is upgraded in place when the lookup succeeds.
- **`psc_code` (D11).** `spending_by_award.md` lists `PSC` as a field available for contracts (award types A–D). Both the award search (step 3) and backfill (step 4) add `"PSC"` to their `fields` list, so every `contracts` document gets the award's own PSC with no extra requests.
  - The docs don't show the shape of the `PSC` value: it could be a plain code string or an object with code and description. The loader stores the API value unchanged in `raw.PSC` and sets `psc_code` to the code string. Week 1 of the parallel run records which shape is returned (section 9.1).
  - A backfilled award may legitimately have a PSC other than 1550: its transaction was coded 1550, but the award as a whole may not be. Possibly the same applies to some award-search results, depending on whether the `psc_codes` filter matches at award or transaction level. The docs don't say. Section 9.2 reports how many `contracts` have `psc_code != "1550"`. This is a finding, not a failure.
  - If `PSC` comes back null for an award, `psc_code` stays null. It is never filled in from the search filter.
- **Who wins on conflict:** if an award arrives from both the FY search and backfill, the FY search result overwrites the fields (`source: "award_search"`). `first_seen` and `notes` are preserved.
- From `spending_by_award.md`: `generated_internal_id` is marked **optional**, and `internal_id` is a **number**. Rows with no `generated_internal_id` are rejected and logged. `internal_id` is kept only inside `raw`. Surrogate IDs are deprecated (`transactions.md`: "Generated award identifiers are preferred as they are effectively permanent").

### 3.5 `contracts_past` (bulk CSV backfill, FY2020–2025)

```js
{
  _id: "<contract_award_unique_key>",
  contract_award_unique_key: "...",
  award_key: "...",                   // = contract_award_unique_key
  award_kind: "AWD" | "IDV",          // parsed from prefix; 116 of 2,109 rows are CONT_IDV_
  // ...the remaining 15 columns unchanged; dates -> ISODate, amounts -> double
  source_file: "usa_spending_old_data/base_awards/psc1550_fy2023.csv"
}
```

This is a frozen historical lookup for `contracts_past_transactions`. It is **not** used as a source for the live backfill, because its columns differ from `spending_by_award` and it stops at FY2025. A live transaction whose award is only in `contracts_past` still gets a live `contracts` document through backfill.

### 3.6 `contracts_past_transactions`

```js
{
  _id: "<contract_transaction_unique_key>",
  contract_transaction_unique_key: "12C2_12C2_12024B20F0119_0_12024B20A0002_0",
  txn_key: "12C2_12C2_12024B20F0119_0_12024B20A0002_0",   // = contract_transaction_unique_key
  contract_award_unique_key: "CONT_AWD_12024B20F0119_12C2_12024B20A0002_12C2",
  award_key: "CONT_AWD_12024B20F0119_12C2_12024B20A0002_12C2",
  // ...the remaining 27 columns unchanged
  action_date_fiscal_year: 2023,
  source_file: "usa_spending_old_data/transactions/psc1550_fy2023_t.csv"
}
```

Historical transactions are also primary relative to `contracts_past`. The 2 award keys that have transactions but no `contracts_past` row are kept and reported, never dropped.

## 4. Indexes

`_id` is indexed (and unique) automatically.

| Collection | Index | Purpose |
|---|---|---|
| `legislation_history` | `{ bill_id: 1, snapshot_date: -1 }` | Timeline per bill, previous-snapshot lookup when computing `changed_since_prev` |
| `legislation_history` | `{ snapshot_date: -1, changed_since_prev: 1 }` | "What changed this week" |
| `legislation_history` | `{ bill_id: 1, legiscan_change_hash: 1 }` | Distinct versions of a bill |
| `legislation` | `{ bill_id: 1 }` unique, partial (`bill_id: {$type: "string"}`) | One current doc per LegiScan bill; hand rows exempt |
| `legislation` | `{ track: 1, event_date: -1 }` | Tracker views |
| `legislation` | text index on `item_name`, `raw.description` | Keyword search |
| `rulemakings` | `{ identifier: 1 }` unique, partial | No duplicate hand entries |
| `rulemakings` | `{ source: 1, last_seen: -1 }` | Per-source views, "seen this run", and the Regulations.gov loader's docket input query. The `source` prefix also serves hand-row filters (`source: null`), so there is no separate `{ source: 1 }` index |
| `rulemakings` | `{ docket_id: 1 }` | Join Federal Register to Regulations.gov documents and comments |
| `rulemakings` | `{ publication_date: -1 }` | Newest documents, date-range reports |
| `rulemakings` | `{ comments_close_on: 1 }` | Open comment periods |
| `rulemaking_comments` | `{ comment_on_id: 1 }` | Comments on a document |
| `rulemaking_comments` | `{ docket_id: 1 }` | Comments per docket |
| `rulemaking_comments` | `{ detail_fetched_at: 1, posted_date: -1 }` | Comments still waiting for details (`detail_fetched_at` unset), newest first (3.3a) |
| `contract_transactions` | `{ award_key: 1, action_date: 1 }` | Transactions per award; distinct `award_key`s for the backfill check |
| `contract_transactions` | `{ action_date: -1 }` | Weekly and FY reporting on the primary record |
| `contract_transactions` | `{ txn_key: 1 }` unique | Cross-source transaction join (section 5) |
| `contracts_weekly_transactions_all` | `{ action_date: -1 }`, `{ award_key: 1 }` | Latest-week comparison; discovery list for transaction pull and backfill |
| `contracts` | `{ last_modified_date: -1 }` | Transaction-pull selector (b) in 3.4.1 |
| `contracts` | `{ lookup_status: 1 }` partial (`lookup_status: "pending"`) | Weekly retry of unresolved awards; stays tiny |
| `contracts` | `{ award_base_date: -1 }` | FY / date-range reports |
| `contracts_past` | `{ award_kind: 1 }` | Exclude IDVs when comparing to live contracts |
| `contracts_past_transactions` | `{ action_date_fiscal_year: 1, action_date: 1 }` | Fiscal-week / fiscal-month distributions, `weekly_rank()` |
| `contracts_past_transactions` | `{ award_key: 1 }` | Join to awards |
| `contracts_past_transactions` | `{ txn_key: 1 }` unique | Cross-source transaction join |

`contracts` and `contracts_past` need no `award_key` index because `award_key == _id`. The backfill check (transaction `award_key`s not in `contracts`) is an anti-join on `contracts._id`, which is already indexed.

## 5. Joining live API awards to bulk CSV awards

### 5.1 What the API docs say

- The docs define `generated_internal_id` only as a string. `spending_by_award.md` describes it as "generated unique award id" (for the sub-award variant), and `transactions.md` calls it "a 'generated' natural award id… effectively permanent." The docs do **not** describe its structure.
- `contract_award_unique_key` / `contract_transaction_unique_key` **do not appear anywhere** in `docs/api_documentation/`. They are bulk-download (Custom Award Data) column names, not search-API fields.
- `spending_by_award` and `spending_by_transaction` both list an `award_unique_id` filter (`optional, string`) with no description. The name suggests it takes this same key. Section 9.1 tests it.
- `spending_by_award` also has `award_ids` (array of strings). By default it does fuzzy full-text matching. **Wrapping an ID in double quotes forces an exact match.** It matches PIIDs (`Award ID`), not generated keys.
- In `spending_by_award`, `award_type_codes` is **required** and `time_period` is optional.

So the docs alone can't confirm a match. I checked the actual data in `UAS_Tracker.db` instead.

### 5.2 What the data shows (verified 2026-10-10)

**Award keys: they are the same identifier.**

| Check | Result |
|---|---|
| Format `CONT_AWD_<PIID>_<agency>_<parent PIID>_<parent agency>` | 541 / 541 live `api_internal_id`; 1,993 / 2,109 bulk keys (the other 116 are `CONT_IDV_<PIID>_<agency>`) |
| Exact string matches, live ∩ bulk | **164** |
| Additional matches after upper-casing | 0 (both sides are already uppercase) |
| PIIDs in both sets whose full keys differ | **0** of 164 |
| `-NONE-` placeholder for missing parent | Used identically on both sides |

`generated_internal_id` equals `contract_award_unique_key` for contract awards. **Join on exact string equality with no transformation.**

Why only 164 overlap: the live pull covers FY2026 activity (any award with an action since 2025-10-01, including base dates back to 2010). The bulk files cover FY2020–2025 actions. The overlap is the set of awards active in both periods. Live search uses award types A–D, so it never returns `CONT_IDV_` keys. Those 116 bulk rows have no live counterpart by design.

**Transaction keys: same identifier plus a fixed prefix.**

| | Example |
|---|---|
| live `transaction_id` | `CONT_TX_9700_9700_W900KK26FA047_0_W900KK22D0010_0` |
| bulk `contract_transaction_unique_key` | `12C2_12C2_12024B20F0119_0_12024B20A0002_0` |

`transaction_id == "CONT_TX_" + contract_transaction_unique_key`. Exact matching finds 0 overlaps, but **2** after adding the prefix. Those are the only live transactions dated before FY2026 (live `action_date` ranges 2025-09-18 to 2026-09-26, and the bulk data ends 2025-09-30).

**Transaction → award key derivation** (bulk `contract_transaction_unique_key` is `<agency>_<parent agency>_<PIID>_<mod>_<parent PIID>_<txn#>`):

```
award_key = "CONT_AWD_" + PIID + "_" + agency + "_" + parentPIID + "_" + parentAgency
          (parentPIID == "-NONE-"  →  "..._-NONE-_-NONE-")
```

This reproduces `contract_award_unique_key` for **all** 6,346 bulk transactions (0 mismatches, excluding IDV parents). It is used as a load-time validation check, and to confirm that a backfilled award matches the transaction that referenced it (section 8.3).

**Referential integrity today:**
- `contract_transactions.api_internal_id` → `contracts`: 39 / 39 resolve. This is guaranteed today because transactions are only pulled for known contracts.
- `contracts_weekly_transactions_all.generated_internal_id` → `contracts`: **7 / 14 resolve.** The 7 that don't are all new FY2026 awards with `-NONE-` parents, absent from `contracts_past` too. Under D7 they are backfilled. Section 9 checks this reaches 14 / 14, minus any `pending`.
- 2 bulk transaction award keys have no row in `contracts_past`. Report them during migration rather than dropping them.

### 5.3 Proposed join

Add a **normalized `award_key`** to every contract collection and join on it. A `txn_key` is added for transactions.

| Collection | `award_key` = | `txn_key` = |
|---|---|---|
| `contract_transactions` | `api_internal_id` | `transaction_id` without the `CONT_TX_` prefix |
| `contracts_weekly_transactions_all` | `generated_internal_id` | n/a (API returns no transaction key; key is `(award, mod)`) |
| `contracts` | `generated_internal_id` (= `_id`) | n/a |
| `contracts_past_transactions` | `contract_award_unique_key` | `contract_transaction_unique_key` |
| `contracts_past` | `contract_award_unique_key` (= `_id`) | n/a |

The value is copied unchanged today, because the formats already match. Having one field name means:
- `$lookup` and `$unionWith` across live and past data use `award_key` everywhere, with no per-collection field mapping.
- If USASpending ever changes either format, the fix lives in the loader, not in every query.

Typical queries start from transactions (D6):
- **All transactions with award details:** `contract_transactions` `$unionWith` `contracts_past_transactions` → dedupe on `txn_key` (handles the 2 overlapping late-FY2025 transactions) → `$lookup` `contracts` on `award_key`, falling back to `contracts_past` when there's no live match.
- **This week:** `contracts_weekly_transactions_all` → `$lookup` `contracts` on `award_key`. Every row resolves, either to a full award or to a `pending` stub.
- **Weekly rank vs history:** `contracts_weekly_transactions_all` is matched to `contracts_past_transactions` by date window, not by key. No change is needed.

Load-time validation:
1. `award_key` matches `^CONT_(AWD|IDV)_` and contains no lowercase letters.
2. For `contracts_past_transactions`, the derived award key (formula above) equals `contract_award_unique_key`. Log any mismatch.
3. `txn_key` does not start with `CONT_TX_`.
4. After each weekly run: every distinct `award_key` in `contract_transactions` and `contracts_weekly_transactions_all` exists in `contracts._id`. Count the `pending` ones separately.

## 6. Hosting: local `mongod` on ZFS (D2, D9, D10, D13, D14)

```
uaspool                        # existing pool
uaspool/mongo                  # mongod storage.dbPath (data + WiredTiger journal together)
                               #   compression=off  (WiredTiger compresses, D10)
uaspool/raw                    # raw API files (section 7)
                               #   compression=lz4, mountpoint=/uaspool/raw  (outside the repo, D15)
uaspool/backups                # mongodump archives (D14)
                               #   compression=off  (archives are already gzip'd)
```

**`uaspool/mongo` properties:**
- `compression=off` (D10). WiredTiger's own block compression handles the data. Keep MongoDB's default compressor (`snappy`) unless a later benchmark favors `zstd`, which is set per collection via `storage.wiredTiger.collectionConfig.blockCompressor`. Index prefix compression (default on) also stays.
- `atime=off`.
- `recordsize`: the default 128K is fine at this data size (under 1 GB). Smaller record sizes only matter for heavy random-write workloads, which this pipeline doesn't have. If it is ever tuned, benchmark first.
- `logbias=latency` (default) is fine.

**`uaspool/raw` properties:**
- `compression=lz4` (D10). The raw data is mostly JSON plus the LegiScan zips. The zips are already compressed, and lz4 detects that and stores them as-is at almost no CPU cost.
- `mountpoint=/uaspool/raw` (D15).
- `atime=off`.
- The files are write-once (section 7), so snapshots of this dataset stay small.

**`uaspool/backups` properties:**
- `compression=off`. `mongodump --gzip` output doesn't compress further, so lz4 would only spend CPU detecting that. (lz4 would also be harmless.)
- `atime=off`.
- It is not snapshotted. The archives are themselves the backups, and they are pruned by the retention rules below.

**Consistency:** the WiredTiger journal stays on `uaspool/mongo` with the data files (the default under `dbPath`). A ZFS snapshot is atomic, so a snapshot of `uaspool/mongo` is crash-consistent and restorable without stopping `mongod`. A run touches both `uaspool/mongo` and `uaspool/raw`, so snapshot them together in one command (`zfs snapshot uaspool/mongo@<name> uaspool/raw@<name>`). The DB state and the raw files that produced it then match exactly.

**Bind and access:** `net.bindIp: 127.0.0.1`. Enable authorization with one user for the notebooks. Put the credentials in `.env` (already gitignored) as `MONGO_URI`, next to `LEGI_API_KEY`, along with `RAW_ROOT=/uaspool/raw` (section 7).

### 6.1 Backups (replacing "the DB is in git", D3)

| Layer | What | Where | When | Status |
|---|---|---|---|---|
| 1. ZFS snapshots | `uaspool/mongo` + `uaspool/raw`, same name | on the pool | before and after each weekly run (`weekly-2026-10-11-pre` / `-post`) | from the first Mongo run |
| 2. `mongodump --archive --gzip` | whole `uas_tracker` DB | `uaspool/backups/uas_tracker_<date>.archive.gz` | weekly, right after the `-post` snapshot | from the first Mongo run (D13: local-only until the off-laptop copy exists) |
| 3. Off-laptop copy | dump archives + raw files | *tentatively* Azure Blob Storage (6.2) | weekly, after layer 2 | **not yet set up**. Tentatively around Monday 2026-10-12 (D13, open) |

- **Retention:** keep weekly snapshots and dumps for 12 weeks, and monthly ones (first run of each month) for 12 months. Raw files are never pruned, locally or off-laptop, because they are the source of truth (section 7).
- **Restoring from layers 1–2:** use `zfs rollback` for a bad run, or `mongorestore --archive --gzip` into a fresh `dbPath` for anything else.
- **Rebuild of last resort:** raw files (on `uaspool/raw` and the off-laptop copy) plus the bulk CSVs in the repo can regenerate everything except hand-entered fields. The `rulemakings` and current-`legislation` CSV exports committed to git (see "Git changes" below) cover those.

**Interim risk until layer 3 exists (D13):** until then, every copy of the MongoDB data and the raw files is on one laptop and one pool. That's acceptable only because **SQLite remains the system of record during the parallel run**, and its CSV exports are still committed and pushed. This is why cut-over waits for the off-laptop backup (section 9).

### 6.2 Off-laptop copy: tentative Azure Blob Storage design (D13, open)

**Tentative.** Azure is the likely provider but isn't confirmed, and neither is the start date. Everything in this subsection is a proposal to use if Azure is confirmed. The **restore verification at the end applies whatever the provider is.** Account name, region, and auth are open (section 10).

```
<storage account>
  uas-backups/                      # container, Cool tier
    mongodump/uas_tracker_2026-10-25.archive.gz
  uas-raw/                          # container, Cool tier
    legiscan/2026-09-27/getDatasetRaw2026-09-27.zip     # same relative paths as /uaspool/raw
    usaspending/...
    manifest.jsonl
```

- **Upload tool:** `azcopy`.
  - Raw files: `azcopy sync` **from the `-post` ZFS snapshot** (`/uaspool/raw/.zfs/snapshot/weekly-<date>-post/`), not the live directory, so the upload is a consistent copy of that run. Files are write-once, so each sync only uploads that week's new files. Never pass `--delete-destination`.
  - Dumps: `azcopy copy` of the new archive.
- **Auth:** a SAS token or service principal scoped to these two containers. It needs create/write/list only, **no delete**, so a compromised or buggy job can't remove backups. Keep it in `.env` (gitignored) or the OS keyring, never in the repo.
- **Protection:** enable blob soft delete (e.g. 30 days) and, if the account allows it, a time-based immutability policy on `uas-backups`.
- **Retention in Azure:** a lifecycle management rule mirrors the local schedule for `uas-backups/mongodump/` (12 weekly, 12 monthly). `uas-raw/` is kept forever. It can move to the Archive tier after about 180 days if cost matters, at the price of slow retrieval.
- **Verification (required before cut-over):**
  1. Download the latest dump from the off-laptop copy.
  2. `mongorestore` it into a scratch `mongod` (temporary `dbPath`, e.g. a throwaway `uaspool/restore-test` dataset, destroyed afterwards).
  3. Compare document counts per collection, and the `runs` log, against the live DB.
  4. Spot-check that a few `raw_source` paths in the restored data exist in the off-laptop raw copy (`uas-raw` if Azure).

### 6.3 Git changes

**Now (D15):** add `raw/` to `.gitignore`. (Done in this revision. The raw files live outside the repo, so this only guards against an accidental local `raw/` directory.) Note that `raw/` with a trailing slash matches directories only. If a `raw` **symlink** to `/uaspool/raw` is ever added to the repo, change the pattern to `/raw`, or git will try to commit the symlink.

**At implementation time:**
- Move `legiscan_getDatasetRaw_data/*.zip` to `/uaspool/raw/legiscan/<date>/` and `git rm -r legiscan_getDatasetRaw_data/`. The four existing zips remain in git history. No new raw files are committed.
- Update `LegiScan1`/`LegiScan2` (and the USASpending notebooks) to read `RAW_ROOT` from `.env` instead of `../legiscan_getDatasetRaw_data/`.
- During the parallel run, the `.db` **is no longer committed** (D3), but it stays on disk for comparison.
- At cut-over (section 9), add `UAS_Tracker.db` to `.gitignore` and `git rm --cached UAS_Tracker.db`. The file's history remains in past commits.
- Update CLAUDE.md:
  - "Pipelines": new run order (D7) and 14-day lookback (D12).
  - "Running": `MONGO_URI` and `RAW_ROOT` in `.env`.
  - "Repo conventions": weekly commits contain **CSV exports only**, with no DB and no raw zips. Raw files are on `/uaspool/raw` and in the off-laptop copy. MongoDB is on `uaspool/mongo`.
- Because hand-entered data no longer reaches git through the `.db`, add committed exports `Exported_Rulemakings.csv` and the current-view `Exported_Legislation_Current.csv` (section 3.2).
- Add `Exported_Contract_Transactions.csv` (from the primary record, sorted by `action_date`, `transaction_id`). Under D6 the transaction history is what should be readable in the git log. `Exported_SQL_Contracts_Table.csv` continues, filtered to `lookup_status: "ok"`. Its `psc_code` column now carries the API value (D11), so expect a one-time diff in the first Mongo-generated export wherever an award's PSC isn't 1550.

## 7. Raw API responses in dated folders (`/uaspool/raw`, D9, D15)

Raw files live on the `uaspool/raw` dataset, mounted at **`/uaspool/raw`, outside the repo**, and are **not committed to git** (D15). Git handles large, growing binary and JSON trees poorly. History and off-site copies come from ZFS snapshots (6.1) and the off-laptop copy (6.2).

Code finds the folder through `RAW_ROOT` in `.env`. Every `raw_source` and manifest `path` is stored **relative to `RAW_ROOT`** (e.g. `legiscan/2026-09-27/getDatasetRaw2026-09-27.zip`). If the mountpoint moves, or files are restored from the off-laptop copy to a different directory, no documents need rewriting.

```
/uaspool/raw/                        # RAW_ROOT = uaspool/raw dataset
  legiscan/
    2026-09-27/                      # LegiScan dataset_date (the Sunday refresh)
      getDatasetList.json
      getDatasetRaw2026-09-27.zip
  usaspending/
    weekly_transactions/2026-10-11/  # step 1 (14-day lookback)
      request.json
      page_001.json ...
    transactions/2026-10-11/         # step 2, one file per award (all pages combined, or _p2, _p3 ...)
      CONT_AWD_W91ZRS26FC010_....json
    awards/2026-10-11/               # step 3
      request.json
      page_001.json ...
    award_backfill/2026-10-11/       # step 4, one request+response per missing award_key
      CONT_AWD_19GT5026P0853_1900_-NONE-_-NONE-.json
    award_unique_id_test/2026-10-11/ # parallel-run test, section 9.1
      case_1_award.json ...
  federal_register/
    2026-10-11/                      # run date; one file per search phrase per page (3.3)
      unmanned_aircraft_p1.json
      counter_uas_p1.json ...
  regulations_gov/
    2026-10-11/                      # run date (3.3, 3.3a)
      <docket_id>_docs_p<n>.json     # documents in a docket
      <object_id>_comments_p<n>.json # comment list for one document
      <comment_id>_detail.json       # one comment's details
  manifest.jsonl                     # path (relative), sha256, source, endpoint, fetched_at, record_count, run_id
```

- **Folder date:** LegiScan uses `dataset_date`. USASpending uses the run date. All steps of one weekly run share one date folder per endpoint.
- **Write-once:** never overwrite. A same-day re-run writes `<date>_2/`, or skips if the request hash is already in `manifest.jsonl`. LegiScan also requires using `dataset_hash` to avoid repeat downloads: the Crash Course says "failure to do so will result in suspended access". `LegiScan1` should compare `dataset_hash` against the manifest before calling `getDatasetRaw`.
- **Order:** save raw file → append to manifest → upsert into Mongo, for each step. Failed loads can be replayed from disk.
- **Compression:** files are written as plain `.json`. lz4 on the dataset handles storage (D10).
- **Overlap:** with the 14-day lookback (D12), consecutive `weekly_transactions/` folders contain some of the same rows. That's expected. Each folder is exactly what the API returned on that date.
- **`manifest.jsonl` integrity:** with git gone, the per-file `sha256` is how a file restored from the off-laptop copy or a snapshot is checked. The restore verification (6.2) checks a sample.
- **Migration:** create `uaspool/raw`, then move `legiscan_getDatasetRaw_data/*.zip` → `/uaspool/raw/legiscan/<date>/` (6.3). Leave `usa_spending_old_data/` in the repo where it is. It is a one-time bulk download, not a weekly API response, and it is already committed.

## 8. Weekly run and dedup

All loads use `bulkWrite` with `ordered: false`. Every write is keyed on `_id`, so re-running a week is idempotent.

### 8.1 LegiScan

For each keyword-matched bill in the week's dataset:

1. **Snapshot** into `legislation_history`:
   ```
   filter: { _id: { bill_id, snapshot_date: <run date> } }
   update: $setOnInsert: { ...all snapshot fields, changed_since_prev }
   upsert: true
   ```
   Because every field is written with `$setOnInsert`, a re-run on the same day changes nothing. `changed_since_prev` compares `change_hash` with the newest earlier snapshot for that bill.

2. **Current view** in `legislation`:
   ```
   filter: { _id: "legiscan:<bill_id>" }
   update:
     $setOnInsert: { first_seen, date_added }
     $set:         { last_seen, latest_snapshot }
     -- only if change_hash differs from stored:
     $set:         { raw, item_name, identifier, body, source_url, legiscan_change_hash, raw_source, last_changed }
   upsert: true
   ```
   The hand-maintained fields (`track`, `notes`, `event_*`, `sequence`) are never in `$set`.

**Migrating existing rows:** all 287 rows → `legislation_history`, keyed on (`bill_id`, `date_added`), with `sqlite_id` kept. Verify `dataset_date` per section 3.1a. Then build `legislation` from the newest snapshot per bill (72 docs). Hand-maintained fields come from that same newest row. If an older snapshot has a non-null hand field that the newest lacks, report it instead of silently dropping it. Today all such fields are null.

### 8.2 USASpending: run order (D7)

One weekly run, one `run_id`, steps in this order. Each step saves raw files before loading (section 7).

| Step | What | Endpoint | Writes | Upsert key |
|---|---|---|---|---|
| **1. Weekly transaction feed** | All PSC 1550, type A–D transactions with `action_date` in the **past 14 days** (D12); `fields` adds `PSC` | `POST /api/v2/search/spending_by_transaction/` (paged) | `contracts_weekly_transactions_all` | `{generated_internal_id, mod}` |
| **2. Transaction history** | Full history for each award in the step 1 feed | `POST /api/v2/transactions/` (paged until `hasNext` is false) | `contract_transactions` (`discovered_via: "weekly_feed"`) | `transaction_id` |
| **3. Award search** | FY awards, same filters as `USASpending1` today (2025-10-01 → yesterday); `fields` adds `PSC` (D11) | `POST /api/v2/search/spending_by_award/` (paged) | `contracts` (`source: "award_search"`, `lookup_status: "ok"`) | `generated_internal_id` |
| **3b. Coverage pull** | Transaction history for awards whose `last_modified_date` is in the past 7 days and that step 2 didn't already fetch (today's `USASpending2` rule) | `POST /api/v2/transactions/` | `contract_transactions` (`discovered_via: "award_modified"`) | `transaction_id` |
| **4. Award backfill** | Every distinct `award_key` in `contract_transactions` ∪ `contracts_weekly_transactions_all` that is missing from `contracts`, or present with `lookup_status: "pending"` | `spending_by_award` by key (8.3) | `contracts` (`source: "backfill"`) or a `pending` stub | `generated_internal_id` |
| **5. Integrity check** | Validation rule 4 (section 5.3) | none | `runs` | n/a |

Notes:
- Step 3b exists so Mongo covers at least what `USASpending2` covers today. It runs after step 3 because it needs fresh `last_modified_date` values. It doesn't change D7's ordering: transactions are still pulled first, and awards only look them up.
- Step 4 runs last, so it catches award keys from both step 1 and step 2. Step 2 can surface no new keys, since it fetches by award, but step 1 can.
- **14-day overlap (D12):** awards whose transactions fall in both this run's and last run's window get step 2 again. That re-fetches their history, which is cheap (one request per award per page) and picks up any corrections. All writes are upserts, so nothing is duplicated.
- On any step failure, later steps still run against what was loaded, and `runs` marks the run `partial`. Re-running is safe because all writes are upserts.

### 8.3 Award backfill (step 4)

For each missing `award_key`:

1. **Primary: `award_unique_id`** (only if the test in 9.1 passed):
   ```
   POST /api/v2/search/spending_by_award/
   filters: { award_type_codes: ["A","B","C","D"], award_unique_id: "<award_key>" }
   fields:  same list as step 3, including "PSC" (D11)
   ```
   No `psc_codes` filter and no `time_period`. The goal is the award a known transaction belongs to, whatever its award-level PSC or base date. If 9.1 shows `time_period` is required, use `2007-10-01` → today.
2. **Fallback: exact PIID** (if 9.1 failed, or (1) returns 0 rows):
   ```
   filters: { award_type_codes: [...], award_ids: ["\"<PIID>\""] }   // quoted = exact match, per docs
   ```
   PIIDs can repeat across agencies, so keep only the result whose `generated_internal_id == award_key` exactly. Take the PIID from the transaction's `award_id` / `Award ID`, or from the key's second segment.
3. **Accept** only if exactly one result has `generated_internal_id == award_key`. Upsert it as a normal `contracts` document with `source: "backfill"`, `lookup_status: "ok"`, `backfill.method` set, and `psc_code` from the response's `PSC` field (D11). It may differ from 1550.
4. **Otherwise** upsert or keep the `pending` stub (`$setOnInsert` stub fields, `$inc: { "backfill.attempts": 1 }`, `$set: { "backfill.last_attempt" }`). It is retried next week. After 4 failed weeks, list it under "needs attention" in the run summary. It is not deleted.

Write rules for `contracts` across steps 3 and 4:

| Incoming | Existing | Result |
|---|---|---|
| award search (step 3) | none / pending / backfill | `$set` all API fields, `source: "award_search"`, `lookup_status: "ok"`; keep `first_seen`, `notes` |
| backfill hit (step 4) | none / pending | `$set` all API fields, `source: "backfill"`, `lookup_status: "ok"`; keep `first_seen`, `notes` |
| backfill hit (step 4) | award_search | not possible: step 4 only looks up keys missing or pending |
| backfill miss (step 4) | none | insert `pending` stub |
| backfill miss (step 4) | pending | increment attempts only |

`notes` is never set by the pipeline. This also fixes today's `INSERT OR REPLACE`, which wipes `notes`.

### 8.4 Other collections

| Collection | Filter | Update |
|---|---|---|
| `contract_transactions` | `_id: transaction_id` | `$set` API fields + `raw` + `last_seen`; `$setOnInsert` `first_seen`, `discovered_via` (first reason wins) |
| `contracts_weekly_transactions_all` | `_id: {generated_internal_id, mod}` | same pattern |
| `contracts_past*` | `_id` | one-time: `drop()` + `insert_many()` after the existing pandas dedup |
| `rulemakings` (API documents) | `_id: "fr:" + document_number` (Regulations.gov: `"rg:" + documentId`) | `$set` fields + `raw` + `last_seen`; `$addToSet` `matched_keywords` (Federal Register only); `$setOnInsert` `first_seen` |
| `rulemaking_comments` | `_id: "rgc:" + commentId` | `$set` list fields + `raw` + `last_seen`; `$setOnInsert` `first_seen`. The detail fetch later `$set`s the detail fields and `detail_fetched_at` |

### 8.5 Run log: `runs`

```js
{ _id: ObjectId, run_id: "usaspending-2026-10-11", pipeline: "legiscan" | "usaspending",
  started_at, finished_at, status: "ok" | "partial", dataset_date, raw_folder,
  zfs_snapshot_pre: "weekly-2026-10-11-pre",   // same name on uaspool/mongo and uaspool/raw
  steps: {
    weekly_feed:   { window_days: 14, rows: 14, award_keys: 14, new_rows: 9, late_postings: 1 },
    txn_history:   { awards: 14, inserted: 20, modified: 2, unchanged: 17 },
    award_search:  { inserted: 3, modified: 40, unchanged: 498, rejected: 0, psc_not_1550: 0, psc_null: 0 },
    coverage_pull: { awards: 6, new_txns_not_in_feed: 1 },
    backfill:      { missing: 7, resolved_unique_id: 7, resolved_award_ids: 0, pending: 0, psc_not_1550: 0 },
    integrity:     { unresolved_award_keys: 0 }
  } }

{ _id: ObjectId, run_id: "federal_register-2026-10-11", pipeline: "federal_register", run_tag,
  started_at, finished_at, status: "ok" | "failed", raw_folder: "federal_register/2026-10-11",
  window_start: "2026-10-02", window_end: "2026-10-11",   // publication_date window
  requests: 6, upserted: 41, new: 3,                       // upserted = documents written, new = first seen this run
  steps: { search: { documents_returned: 52,               // before de-duplication across phrases
                     per_keyword: { "unmanned aircraft": 3, "drone": 4, ... },
                     inserted: 3, modified: 38, unchanged: 0 } },
  errors: [] }

{ _id: ObjectId, run_id: "regulations_gov-2026-10-11", pipeline: "regulations_gov", run_tag,
  started_at, finished_at, raw_folder: "regulations_gov/2026-10-11",
  status: "ok" | "partial" | "failed",
  // partial: a docket failed (e.g. 429 retries ran out; errors lists it), or comment-detail
  //          fetches stopped because X-Ratelimit-Remaining ran low. A warning, not a failure.
  // failed:  the run itself crashed (the exception is re-raised).
  window_start: "2026-10-02", window_end: "2026-10-11",   // FR publication_date window; null with --docket
  docket: null,                                            // set when run with --docket
  requests: 5, upserted: 4, new: 4,   // upserted / new count documents and comments together
  steps: { dockets: { processed, docket_ids },
           documents: { inserted, modified, unchanged },
           comments:  { inserted, modified, unchanged },
           comment_details: { fetched, modified, pending, stopped_on_rate_budget },  // pending alone is not an error
           requests, backoffs_429: { count, wait_s, events }, page_cap_hit: [] },
  errors: [] }
```

The `weekly_ingest` document copies `window_start`, `window_end`, `requests`, `upserted`, and `new` into `children.federal_register` and `children.regulations_gov`. A rulemaking loader's `"partial"` is a warning: the `weekly_ingest` document gets status `"partial"` and exits 2, and `weekly_run.sh` keeps the data and finishes with a warning (exit 2) instead of rolling back. Any `"failed"` child, and USASpending's `"partial"` (its step errors go into `errors`), still fail the run. `backfill_rulemakings.py` uses the same rule.

## 9. Two-week parallel run (D5)

**Weeks:** the LegiScan refreshes of Sunday 2026-10-11 and Sunday 2026-10-18.

**Cut-over gate:** cut over at the next weekly run after **both** of these are true:
1. The parallel-run checks (9.2) pass for two consecutive weeks.
2. **The off-laptop backup is working (D13):** at least one weekly dump and raw sync has been uploaded, and the restore test in 6.2 has passed. The provider (tentatively Azure) and its start date are open.

If both are done by the 2026-10-25 run, cut over then. Otherwise keep running in parallel. Extra parallel weeks are cheap, and SQLite remains the system of record until the gate passes.

Each week:
1. Run the existing notebooks unchanged (SQLite). **Don't commit the `.db`** (D3). Commit CSV exports as usual.
2. Run the Mongo pipeline, including step 3b (D16). LegiScan loads from the **same zip** as SQLite, so legislation comparisons are exact. The USASpending requests are **no longer identical**: Mongo asks for the extra `PSC` field (D11), uses a 14-day window instead of 7 (D12), and makes the step 2/4 calls SQLite never makes. Run the Mongo pipeline **within the same hour** as the SQLite notebooks to keep API drift small. Any USASpending difference must then be explained by a `Last Modified Date` later than the SQLite pull, or it counts as a failure.
3. Run the `award_unique_id` test (9.1). In week 1, do this **before** step 4, because it decides the backfill method.
4. Compare (9.2), and record results in the `runs` collection and in a short note per week.
5. Investigate any failure before the next week. A failure in week 2 extends the parallel run by one week.

### 9.1 `award_unique_id` filter test (D8)

The docs list `award_unique_id` with no description. The test confirms whether it filters by `generated_internal_id`, and makes sure it isn't silently ignored (an ignored filter would return the whole unfiltered result set).

Cases, all with `award_type_codes: ["A","B","C","D"]` and no other filters unless stated:

| # | Input `award_unique_id` | Why this one | Expected |
|---|---|---|---|
| 1 | `CONT_AWD_W91ZRS26FC010_9700_47QSMS25D008P_4732` | In `contracts`, has parent IDV | exactly 1 row, `generated_internal_id` equal |
| 2 | `CONT_AWD_W91QVN26PA017_9700_-NONE-_-NONE-` | In `contracts`, `-NONE-` parent (the shape of all 7 current backfill cases) | exactly 1 row, equal |
| 3 | `CONT_AWD_W911QY19F0431_9700_W911QY18D0192_9700` | Only in `contracts_past` (FY2019 order), checks for an implicit date window | exactly 1 row, equal |
| 4 | `CONT_AWD_19GT5026P0853_1900_-NONE-_-NONE-` | One of the 7 real backfill targets | exactly 1 row, equal |
| 5 | `CONT_AWD_DOESNOTEXIST_0000_-NONE-_-NONE-` | Negative control | **0 rows**. Any rows mean the filter is being ignored |
| 6 | `cont_awd_w91qvn26pa017_9700_-none-_-none-` | Case sensitivity | Record the result; the loader always sends uppercase anyway |
| 7 | Case 3 with `time_period` 2025-10-01 → today | Does `time_period` exclude older awards? | Record the result; decides whether backfill omits `time_period` |
| 8 | Case 1 against `spending_by_transaction` | Same filter on the transaction endpoint | Only rows with that `generated_internal_id` |

Also record:
- whether the API **rejects** a request with no `time_period` (cases 1–6);
- the **shape of the `PSC` value** in the case 1–4 responses (request `fields` including `"PSC"`). Is it a plain code string, or an object with code and description? The loader's `psc_code` extraction is written against this (D11).

**Pass:** cases 1–5 and 8 all meet the expected results in both weeks. Then backfill uses `award_unique_id` as the primary method.
**Fail:** backfill uses the quoted `award_ids` fallback (8.3, step 2) as its primary method. Run cases 1–4 again with that method to confirm it resolves them. Note the outcome in section 10.

Raw requests and responses are saved under `$RAW_ROOT/usaspending/award_unique_id_test/<date>/`. Results go into `runs` as `steps.award_unique_id_test: { passed: true, cases: [...] }`.

### 9.2 Comparison checks

| Check | SQLite | MongoDB | Pass if |
|---|---|---|---|
| `award_unique_id` filter | n/a | 9.1 cases | 9.1 pass criteria (or fallback confirmed) |
| Legislation snapshots | rows per `date_added` | `legislation_history` count per `snapshot_date` | equal, and (`bill_id`, `change_hash`) sets identical |
| Legislation CSV | `Exported_SQL_Legislation_Table.csv` | export from `legislation_history` | byte-identical after sorting by (date, bill_id) and normalizing dates |
| Weekly feed | `contracts_weekly_transactions_all` keys, `SUM(transaction_amount)` (7-day window) | same collection, **restricted to SQLite's 7-day `action_date` window** | identical keys, sum within $0.01. Mongo rows in days 8–14 are reported separately, along with how many are `late_posting` |
| `weekly_rank()` input | n/a | row count used by `weekly_rank()` | uses only the latest 7 days (D12), so it matches the SQLite input row count |
| Transactions (primary) | `contract_transactions` keys | `contract_transactions` keys | Mongo ⊇ SQLite. Every extra key's `discovered_via` is `weekly_feed`, i.e. an award SQLite didn't fetch |
| Transaction amounts | `SUM(federal_action_obligation)` over shared keys | same | within $0.01 |
| Awards from search | `contracts` set of `api_internal_id` | `contracts` where `source: "award_search"` | identical sets |
| Award PSC (D11) | `psc_code` (always `"1550"`) | `psc_code` from API | every doc has a non-null code or is counted in `psc_null`. Report `psc_not_1550` for award-search and backfill docs separately. This is a finding, not a pass/fail. A non-zero award-search count means the `psc_codes` filter matches at transaction level |
| Award amounts | `SUM(amount_usd)` | `$sum` over `source: "award_search"` | within $0.01 |
| Backfill | n/a | `contracts` where `source: "backfill"` | equals the set of transaction `award_key`s not in the award search, minus `pending` |
| Referential integrity | weekly feed → `contracts` (7 / 14 today) | every transaction `award_key` → `contracts` | Mongo 100% resolve; `pending` count reported and ideally 0 |
| Re-run idempotence | n/a | run the whole Mongo pipeline twice | second run: 0 inserted, 0 modified, 0 new backfill attempts on resolved keys |
| Analysis | `weekly_rank()` output on SQLite | same function on Mongo data | same percentile |

**After cut-over:** the notebooks read from Mongo only. Stop running SQLite writes. Do the `.gitignore` / `git rm --cached UAS_Tracker.db` step (6.3). Keep the final `UAS_Tracker.db` as an archive in `uaspool/backups/sqlite_final/UAS_Tracker_<date>.db`, and upload it once to the off-laptop copy (`uas-backups/sqlite_final/` if Azure).

**Coverage pull decision (D16), made at cut-over:** across all parallel-run weeks, look at `coverage_pull.new_txns_not_in_feed` in `runs`.
- **Drop step 3b** if it is 0 every week. The 14-day feed is then catching everything the `last_modified_date` rule would.
- **Keep it** if it is ever non-zero. Before deciding, check those transactions' `action_date`s: if they are all more than 14 days old, widening the lookback (D12) is an alternative to keeping 3b.
- Record the decision in this doc as the next free D number (D17 is taken by `rulemakings`).

## 10. Remaining open questions

These stay open until you confirm them. Nothing elsewhere in the doc depends on a guessed answer.

1. **Off-laptop backup provider and start date (D13):** tentatively Azure Blob Storage, starting around Monday 2026-10-12. Neither is confirmed. Cut-over waits for this either way.
2. **Azure details, if Azure is confirmed:** storage account name and region, auth method (SAS vs service principal), and whether an immutability policy is available on the account (6.2).
3. **`award_unique_id` filter outcome:** pending your report from the section 9.1 test. It decides the primary backfill method (8.3).
4. **`PSC` value shape:** pending your report from the section 9.1 responses. It decides how the loader extracts `psc_code` (3.4.3).
