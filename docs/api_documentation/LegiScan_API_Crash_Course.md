# LegiScan API Crash Course

Source: https://legiscan.com/legiscan/crashcourse
Captured 2026-10-10. Published by LegiScan LLC.

## Query Limits

- Public API keys have **10,000 queries per month**, with a rate limit of approximately **2 requests/second sustained**
- Flex & Pull API subscriptions for expanded query limits; Push API subscriptions for full enterprise replication & delivery
- Timing guidelines for each operation on page 7 of the LegiScan API Manual should be followed
- Recommend local caching of JSON responses to minimise spend on replayability
- Always check the `status` code in JSON responses for `OK` or `ERROR` and act appropriately

## Datasets

- Created weekly on **Sunday morning at 5am Eastern**
- All individual `getBill`, `getRollCall` and `getPerson` JSON payloads in a single ZIP file for each legislative session
- Approximately 1000 API queries for 3,800,000+ records for all 2010-2026 data
- ZIP archive files are Base64 encoded in their JSON transport
- Use `dataset_hash` to prevent duplicative downloads; **failure to do so will result in suspended access**

## Texts & Documents

- Bill documents are available via `getBillText`, `getAmendment` and `getSupplement` hooks
- Necessary `doc_id`, `amendment_id`, `supplement_id` identifiers are available in `getBill` payloads
- Document blobs are Base64 encoded in their JSON transport
- There is no need to download the same document blob more than once
- One-time snapshot of complete 375GB data for all 2010-2026 sessions available as a separate paid service

## Hashes

- 32-character hash values are available to detect data changes and optimise query spend
- `change_hash` + `bill_id` for bill data; available in `getBill`, `getMasterList`, `getSearch`, `getMasterListRaw`, `getSearchRaw`
- `dataset_hash` + `session_id` for dataset archives; available in `getDatasetList`, `getDataset`
- Store and compare hash values. If the hash is the same the data is the same, so use the local cache and avoid query spend
- Use the hashes. No. Really. Use them.

## Work Loop

- Typical loop driven by checking `getMasterListRaw` or `getSearchRaw` periodically
- Use `change_hash` in the results to determine when to spend queries updating individual data
- Repeat `getMasterListRaw` or `getSearchRaw` daily/weekly or as needed within query spend
- Open source turnkey LegiScan API Client available to process Pull, Push and Bulk data into a defined SQL schema

## Housekeeping

- Scraping the legiscan.com front end site is prohibited and will result in suspended access
- Creating multiple Public API service keys is prohibited and will result in suspended access
- All data is licensed under **Creative Commons Attribution 4.0**; do as you like but you must give LegiScan attribution
- Play nice! Respect the free public service!

## Timing and Data Freshness

Legislative data does not change as frequently as many apparently think. Below is a table of API operations and the maximum frequency the hook should be called for any given ID or identical search string. Repeated calls more frequent than this schedule will always return unchanged data and waste spend.

| Operation | Description | Max frequency |
|---|---|---|
| `getSessionList` | Retrieve session list for a particular state | Daily |
| `getMasterList` | Retrieve master bill list for a session | 1 hour |
| `getMasterListRaw` | Retrieve master bill list optimised for change_hash detection | 1 hour |
| `getBill` | Retrieve bill detail information for a given bill_id | 3 hours |
| `getBillText` | Retrieve full bill text for a given doc_id | Static |
| `getAmendment` | Retrieve amendment text for a given amendment_id | Static |
| `getSupplement` | Retrieve supplemental document for a given supplement_id | Static |
| `getRollCall` | Retrieve roll call vote information for a given roll_call_id | Static |
| `getPerson` | Retrieve basic information for a given people_id | Weekly |
| `getSearch` | Retrieve results from the full text search engine (50 results) | 1 hour |
| `getSearchRaw` | Retrieve results from the full text search engine (2000 results) | 1 hour |
| `getDatasetList` | Retrieve list of available dataset snapshots and the dataset_hash | Sundays |
| `getDataset` | Retrieve an individual dataset for a specific session_id | Sundays |
| `getSessionPeople` | Retrieve list of people active in a specific session_id | Weekly |
| `getSponsoredList` | Retrieve list of bills sponsored by an individual people_id | Daily |
