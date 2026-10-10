# MongoDB Schema Proposal

Status: proposal only. No code has changed. The notebooks still write to `UAS_Tracker.db`.

## 1. What exists today

The pipeline writes to one SQLite file, `UAS_Tracker.db`, in the repo root. Row counts are from 2026-10-10.

| SQLite table | Rows | Key today | Written by | Write mode |
|---|---|---|---|---|
| `legislation` | 287 | `id` autoincrement | `LegiScan2.ipynb` (+ hand entry) | plain `INSERT`, **no dedup** |
| `rulemakings` | 0 | `id` autoincrement | hand entry only | n/a |
| `contracts` | 541 | `api_internal_id` (PK) | `USASpending1.ipynb` | `INSERT OR REPLACE` |
| `contract_transactions` | 39 | `transaction_id` (PK) | `USASpending2.ipynb` | `INSERT OR REPLACE` |
| `contracts_past` | 2,109 | none (unique in practice on `contract_award_unique_key`) | `USASpending_Past_Awards.ipynb` | `to_sql(replace)` |
| `contracts_past_transactions` | 6,346 | none (unique in practice on `contract_transaction_unique_key`) | `USASpending_Past_Transactions.ipynb` | `to_sql(replace)` |
| `contracts_weekly_transactions_all` | 14 | (`generated_internal_id`, `mod`) | `USASpenging_Past_Visualizations.ipynb` | `INSERT OR REPLACE` |

What the data shows:

- **`legislation` duplicates every week.** The table has 287 rows but only 72 distinct `bill_id`s. It holds 4 runs (`date_added` 2026-09-11, 09-17, 09-22, 09-28) of about 72 bills each. Only 77 distinct (`bill_id`, `legiscan_change_hash`) pairs exist, so about 5 bills actually changed across those runs. The rest are re-inserts of unchanged bills.
- All 287 `legislation` rows have `source_platform = 'LegiScan'` and `state = 'US'`. No hand-entered rows exist yet, but CLAUDE.md says they are allowed, so the design has to support rows with no `bill_id`.
- `contracts.amount_usd` is a mix of SQLite `integer` (220 rows) and `real` (321 rows).
- Dates are stored as `TEXT` in two formats: `YYYY-MM-DD` and `YYYY-MM-DD HH:MM:SS` (`last_modified_date`).
- The largest raw payloads are `legislation.legiscan_raw_json` at about 67 KB and `contracts.raw_json` at about 1.2 KB. Both are far below MongoDB's 16 MB document limit.

## 2. Design principles

1. **One collection per table, with the same field names.** Notebooks and CSV exports can switch over with few edits. Renaming fields is out of scope for this proposal.
2. **Every collection gets a natural `_id`** where the source provides one. The upsert key then also serves as the primary key, and duplicates cannot be stored.
3. **Store raw payloads as embedded documents, not JSON strings.** `legiscan_raw_json` becomes a `raw` sub-document, and `raw_json` becomes `raw`. Fields inside them become queryable, for example `raw.sponsors.name`.
4. **Use native types.** Dates become BSON `Date`. Money uses `Decimal128`, or `double` if exact cents don't matter. This also fixes the int/real mix.
5. **Keep raw files on disk as the source of truth.** The database can always be rebuilt from the dated folders (section 5).
6. Add two bookkeeping fields to every collection: `first_seen` (set on insert) and `last_seen` (set on every run). These replace the overloaded `date_added`. Keep `date_added` too for CSV continuity.

Database name: `uas_tracker`.

## 3. Collections

### 3.1 `legislation`

```js
{
  _id: "legiscan:1234567",          // "legiscan:<bill_id>" for LegiScan rows; ObjectId for hand-entered rows
  bill_id: "1234567",               // null for hand-entered rows
  state: "US",
  item_name: "...",                 // bill title
  identifier: "HB96",
  body: null,
  sequence: null,
  event_date: ISODate | null,
  event_description: null,
  track: null,
  source_url: "...",
  source_platform: "LegiScan",      // or "congress.gov" for hand entries
  notes: null,
  legiscan_change_hash: "abc123...",
  raw: { /* full LegiScan bill object: bill_id, change_hash, history[], sponsors[], texts[], ... */ },
  raw_source: "legiscan/2026-09-27/getDatasetRaw2026-09-27.zip",  // where `raw` came from
  date_added: "2026-09-11",         // kept for CSV continuity, equal to first_seen
  first_seen: ISODate("2026-09-11"),
  last_seen:  ISODate("2026-09-28"),
  last_changed: ISODate("2026-09-22")  // last run where change_hash differed
}
```

**Why one document per bill instead of one per run:** the current table stores the same bill again every week, which is the duplication problem. If change history matters, store it in a separate collection (below) instead of repeating the full bill.

#### 3.1a `legislation_history` (optional, new)

This collection records a version only when `change_hash` changes. Today that would be about 77 documents instead of 287.

```js
{
  _id: { bill_id: "1234567", change_hash: "abc123..." },
  dataset_date: ISODate("2026-09-22"),
  raw: { /* bill object at that version */ }
}
```

If this feels like extra scope, skip it. The raw zips in `raw/legiscan/` already keep every version.

### 3.2 `rulemakings`

Same tracker shape as `legislation`, plus `statutory_deadline: ISODate | null`. All entries are hand-entered, so `_id` is an ObjectId. Add a unique index on `identifier` (for example, a Federal Register docket or RIN) so the same hand entry can't be added twice.

### 3.3 `contracts` (current FY, live API)

```js
{
  _id: "CONT_AWD_W91ZRS26FC010_9700_47QSMS25D008P_4732",   // = api_internal_id = generated_internal_id
  award_id: "W91ZRS26FC010",
  recipient: "...",
  awarding_agency: "...",
  amount_usd: NumberDecimal("123456.78"),
  psc_code: "1550",
  naics_code: "...",
  description: "...",
  award_base_date: ISODate("2026-05-22"),
  last_modified_date: ISODate("2026-08-07T07:03:05Z"),
  notes: null,
  raw: { internal_id, generated_internal_id, "Award ID", "Recipient Name", ... },  // spending_by_award row
  raw_source: "usaspending/awards/2026-10-05/page_003.json",
  first_seen: ISODate, last_seen: ISODate
}
```

Keep `api_internal_id` as a duplicate field as well. Existing notebook code and the CSV export can then read it unchanged.

### 3.4 `contract_transactions` (live API)

```js
{
  _id: "CONT_TX_9700_9700_W900KK26FA047_0_W900KK22D0010_0",  // = transaction_id
  api_internal_id: "CONT_AWD_W900KK26FA047_9700_W900KK22D0010_9700",  // -> contracts._id
  type: "...", action_type: "...",
  action_date: ISODate,
  description: "...",
  modification_number: "0",
  federal_action_obligation: NumberDecimal("..."),
  date_added: "2026-09-20",
  raw: { /* /api/v2/transactions/ result row */ },
  first_seen: ISODate, last_seen: ISODate
}
```

Transactions stay in a **separate collection** rather than being embedded in `contracts`. This matches the current table split. It also avoids unbounded arrays on long-running IDIQ awards with many modifications.

### 3.5 `contracts_past` (bulk CSV backfill)

```js
{
  _id: "<contract_award_unique_key>",
  contract_award_unique_key: "...",
  // ...the remaining 15 columns unchanged; dates -> ISODate, amounts -> Decimal128
  source_file: "usa_spending_old_data/base_awards/psc1550_fy2023.csv"
}
```

### 3.6 `contracts_past_transactions` (bulk CSV backfill)

```js
{
  _id: "<contract_transaction_unique_key>",
  contract_award_unique_key: "...",   // -> contracts_past._id
  // ...the remaining 27 columns unchanged
  action_date_fiscal_year: 2023,
  source_file: "usa_spending_old_data/transactions/psc1550_fy2023_t.csv"
}
```

### 3.7 `contracts_weekly_transactions_all`

```js
{
  _id: { generated_internal_id: "CONT_AWD_...", mod: "0" },  // same composite PK as SQLite
  award_id, action_date: ISODate, date_added, transaction_amount, transaction_description,
  recipient_name, recipient_uei, awarding_agency, award_type, action_type,
  raw: { /* spending_by_transaction row */ },
  first_seen, last_seen
}
```

### Cross-collection key mapping (unchanged from SQLite)

| Live API (`contracts*`) | Bulk CSV (`contracts_past*`) |
|---|---|
| `api_internal_id` / `generated_internal_id` (`CONT_AWD_...`) | `contract_award_unique_key` |
| `transaction_id` (`CONT_TX_...`) | `contract_transaction_unique_key` |
| `award_id` (PIID) | embedded in `contract_award_unique_key`; `contracts_past_transactions.parent_award_id_piid` is the *parent* IDV, not the same field |

Verify that `CONT_AWD_...` and `contract_award_unique_key` use the same format before relying on them for `$lookup` joins. If they don't, add a normalized `award_key` field to both sides.

## 4. Indexes

`_id` is indexed (and unique) automatically. Add only indexes that a current query or upsert needs:

| Collection | Index | Purpose |
|---|---|---|
| `legislation` | `{ bill_id: 1 }` unique, **partial** (`bill_id: {$type: "string"}`) | Prevents duplicate LegiScan bills while allowing hand rows with no `bill_id` |
| `legislation` | `{ track: 1, event_date: -1 }` | Tracker views |
| `legislation` | text index on `item_name`, `raw.description` | Keyword search (replaces the Python `uas/drone/unmanned/uav` filter at query time) |
| `legislation_history` | `{ "_id.bill_id": 1, dataset_date: -1 }` | Version timeline per bill |
| `rulemakings` | `{ identifier: 1 }` unique, partial | Prevents duplicate hand entries |
| `contracts` | `{ last_modified_date: -1 }` | `USASpending2` selects contracts modified in the past 7 days |
| `contracts` | `{ award_base_date: -1 }` | FY / date-range reports |
| `contract_transactions` | `{ api_internal_id: 1, action_date: 1 }` | Transactions for one award |
| `contracts_past_transactions` | `{ action_date_fiscal_year: 1, action_date: 1 }` | Fiscal-week and fiscal-month distributions, `weekly_rank()` window |
| `contracts_past_transactions` | `{ contract_award_unique_key: 1 }` | Join to awards |
| `contracts_weekly_transactions_all` | `{ action_date: -1 }` | Latest-week comparison |

## 5. Raw API responses in dated folders

Today only LegiScan zips are saved to disk. USASpending responses are kept only in the `raw_json` column, or not saved at all for transactions. Proposed layout:

```
raw/
  legiscan/
    2026-09-27/                      # LegiScan dataset_date (the Sunday refresh)
      getDatasetList.json
      getDatasetRaw2026-09-27.zip    # same file as today, moved
  usaspending/
    awards/2026-10-05/               # run date (UTC), one folder per run
      request.json                   # exact POST body (filters, date range)
      page_001.json
      page_002.json
      ...
    transactions/2026-10-05/
      CONT_AWD_W91ZRS26FC010_....json   # one file per award queried
    weekly_transactions/2026-10-05/
      page_001.json
  manifest.jsonl                     # one line per file: path, sha256, source, endpoint, fetched_at, record_count
```

Rules:

- **Folder date:** LegiScan uses `dataset_date`, so the folder ties to the dataset version and not to when it was downloaded. USASpending uses the run date.
- **Write-once:** a file that already exists is never overwritten. Re-running on the same day writes to `2026-10-05_2/`, or the run skips if the request hash already appears in `manifest.jsonl`.
- **Every Mongo document records its file** in `raw_source` (path + page). Any row can be traced back to the bytes it came from.
- **Order of operations:** save the raw file → append to the manifest → upsert into Mongo. If the upsert fails, the run can be replayed from disk without calling the API again.
- **Git:** the repo already commits LegiScan zips. USASpending pages are small JSON, so committing them keeps the "history in git log" convention. If the repo gets too large, gzip the files (`page_001.json.gz`) or move `raw/` to Git LFS.
- Migration: move `legiscan_getDatasetRaw_data/*.zip` into `raw/legiscan/<date>/`. Keep `usa_spending_old_data/` where it is, since it is a one-time bulk download and not a weekly API response.

## 6. Deduplicating weekly upserts

Each weekly load becomes an idempotent `bulkWrite` of `updateOne(..., upsert: true)` operations keyed on `_id`. Running the same week twice then changes only `last_seen`.

### LegiScan (fixes the current duplicate bug)

For each bill that passes the keyword filter:

```
filter: { _id: "legiscan:<bill_id>" }
update:
  $setOnInsert: { first_seen: <dataset_date>, date_added: <dataset_date> }
  $set:         { last_seen: <dataset_date> }
  -- and only if change_hash differs from the stored one:
  $set:         { raw, item_name, identifier, legiscan_change_hash, raw_source, last_changed: <dataset_date> }
upsert: true
```

Use `change_hash` the way LegiScan intends: as the change detector. If it matches, skip the field rewrite and don't add a `legislation_history` entry. If it differs, overwrite the fields and insert a history document whose `_id` is `{bill_id, change_hash}`. Because the `_id` is unique, a duplicate history entry fails harmlessly with E11000, which you can ignore.

Hand-entered fields (`track`, `notes`, `event_*`, `sequence`) are **never** in the `$set` above, so weekly runs don't overwrite manual edits. The current SQLite flow can't guarantee this.

### USASpending `contracts`

```
filter: { _id: generated_internal_id }
update: $set: { ...mapped fields, raw, raw_source, last_seen }, $setOnInsert: { first_seen }
upsert: true
```

This behaves like today's `INSERT OR REPLACE`, except it keeps `first_seen` and any manual `notes`. Today `REPLACE` deletes the row and inserts a new one, which wipes `notes`. Optionally, skip the write when the stored `last_modified_date` is greater than or equal to the incoming one.

### `contract_transactions` / `contracts_weekly_transactions_all`

Use the same pattern, keyed on `transaction_id` and on `{generated_internal_id, mod}` respectively. Both are natural keys from the API, so no extra dedup logic is needed.

### Backfill collections (`contracts_past*`)

These are one-time loads. Replace `to_sql(if_exists='replace')` with `drop()` followed by `insert_many()`, or upsert by `_id` if you ever add a new FY file. Keep the existing pandas dedup ("keep latest action per `contract_award_unique_key`") before loading.

### Run log (optional, new collection `runs`)

```js
{ _id: ObjectId, pipeline: "legiscan", started_at, finished_at, dataset_date,
  raw_folder: "raw/legiscan/2026-09-27", upserted: 3, modified: 5, unchanged: 64 }
```

This replaces the "did I already run this week?" check that today depends on the commit history.

## 7. Open questions / things to decide before coding

1. **Clean up existing `legislation` rows on migration.** Collapse the 287 rows to 72 bill documents, plus about 77 history documents. Recommended.
2. **Hosting:** local `mongod`, Docker, or Atlas free tier. This decides how the "DB committed to git" convention continues. A likely replacement is a `mongoexport`/CSV step in each weekly commit plus the `raw/` folder as the source of truth.
3. **Money type:** `Decimal128` (exact, slightly more awkward in pandas) or `double` (matches today's `REAL`).
4. **Keep SQLite in parallel** for a few weeks and compare CSV exports before switching over.
