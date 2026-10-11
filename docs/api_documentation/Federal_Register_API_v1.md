# Federal Register API v1 - Documentation

Source: https://www.federalregister.gov/developers/documentation/api/v1
Captured 2026-10-11. Published by the Office of the Federal Register / GPO.

**No API key required.** All you need is an HTTP client or a browser.

Base URL: `https://www.federalregister.gov/api/v1/`

> Legal status: FederalRegister.gov is an unofficial prototype of the daily Federal Register. Each document links to the official PDF on govinfo.gov. For legal research, verify against an official edition.

---

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| GET | `/documents/{document_number}.{format}` | Fetch a single document |
| GET | `/documents/{document_numbers}.{format}` | Fetch multiple documents (comma-separated) |
| GET | `/documents.{format}` | Search all documents published since 1994 |
| GET | `/documents/facets/{facet}` | Counts of matching documents grouped by a facet |
| GET | `/issues/{publication_date}.{format}` | Table of contents for one print edition |
| GET | `/public-inspection-documents/{document_number}.{format}` | Single public inspection document |
| GET | `/public-inspection-documents/{document_numbers}.{format}` | Multiple public inspection documents |
| GET | `/public-inspection-documents/current.{format}` | All documents currently on public inspection |
| GET | `/public-inspection-documents.{format}` | Search documents currently on public inspection |
| GET | `/agencies` | All agency details |
| GET | `/agencies/{slug}` | A particular agency's details |
| GET | `/images/{identifier}` | Image variants and metadata for one image identifier |
| GET | `/suggested_searches` | All suggested searches, optionally limited by section |
| GET | `/suggested_searches/{slug}` | A particular suggested search |

`{format}` is a path parameter and is required. Use `json`.

---

## Federal Register Documents

### GET /documents/{document_number}.{format}

Fetch a single Federal Register document.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `format` | path | string | yes | Response format (`json`) |
| `document_number` | path | string | yes | Federal Register document number |
| `fields[]` | query | array[string] | no | Which attributes to return. A reasonable default set is returned if omitted. |

Returns `200 Success`.

### GET /documents/{document_numbers}.{format}

Fetch multiple Federal Register documents.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `format` | path | string | yes | Response format (`json`) |
| `document_numbers` | path | array[string] | yes | Document numbers, separated by a comma |
| `fields[]` | query | array[string] | no | Which attributes to return |

Returns `200 Success`.

### GET /documents.{format}

Search all Federal Register documents published since 1994. **This is the main search endpoint.**

**Paging and output**

| Parameter | In | Type | Description |
|---|---|---|---|
| `format` | path | string | Required. Response format (`json`) |
| `fields[]` | query | array[string] | Which attributes to return |
| `per_page` | query | integer | How many documents to return at once; **1000 maximum**. Default 20 |
| `page` | query | integer | Page of the result set |
| `order` | query | array[string] | Result order. Options include `relevance`, `newest`, `oldest` |

**Search conditions**

| Parameter | Type | Description |
|---|---|---|
| `conditions[term]` | string | Full text search |
| `conditions[publication_date][is]` | string | Exact publication date (YYYY-MM-DD) |
| `conditions[publication_date][year]` | string | Published in a given year (YYYY) |
| `conditions[publication_date][gte]` | string | Published on or after a date (YYYY-MM-DD) |
| `conditions[publication_date][lte]` | string | Published on or before a date (YYYY-MM-DD) |
| `conditions[effective_date][is]` | string | Exact effective date (YYYY-MM-DD) |
| `conditions[effective_date][year]` | string | Effective date in a given year (YYYY) |
| `conditions[effective_date][gte]` | string | Effective on or after a date (YYYY-MM-DD) |
| `conditions[effective_date][lte]` | string | Effective on or before a date (YYYY-MM-DD) |
| `conditions[agencies][]` | array[string] | Publishing agency (slug; see `/agencies`) |
| `conditions[type][]` | array[string] | Document type — see below |
| `conditions[presidential_document_type][]` | array[string] | Presidential document type; only for Presidential Documents |
| `conditions[president][]` | array[string] | Signing President; only for Presidential Documents |
| `conditions[docket_id]` | string | Agency docket number associated with the document |
| `conditions[regulation_id_number]` | string | Regulation ID Number (RIN) associated with the document |
| `conditions[sections][]` | array[string] | Limit to a FederalRegister.gov section |
| `conditions[topics][]` | array[string] | Limit to a CFR indexing term |
| `conditions[significant]` | string | Deemed significant under EO 12866: `0` not significant, `1` significant |
| `conditions[cfr][title]` | integer | Documents affecting the associated CFR title |
| `conditions[cfr][part]` | integer | Part or part range (e.g. `17` or `1-50`); requires CFR title |
| `conditions[near][location]` | string | Location search; zipcode or City and State |
| `conditions[near][within]` | integer | Max distance from location in miles (max 200) |

**Document types (`conditions[type][]`)**

| Code | Meaning |
|---|---|
| `RULE` | Final Rule |
| `PRORULE` | Proposed Rule |
| `NOTICE` | Notice |
| `PRESDOCU` | Presidential Document |

**Presidential document types** include `determination`, `executive_order`, `memorandum`, and others.

**Sections** include `business-and-industry`, `environment`, `health-and-public-welfare`, and others (6 total).

Returns `200 Success`.

### GET /documents/facets/{facet}

Fetch counts of matching documents grouped by a facet.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `facet` | path | string | yes | What to group by for counting. Using `daily` may require limiting the request to smaller date ranges. |

Accepts the same `conditions[...]` query parameters as `/documents.{format}` above.

Returns `200 Success`.

### GET /issues/{publication_date}.{format}

Fetch a document table of contents based on the print edition.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `format` | path | string | yes | Response format (`json`) |
| `publication_date` | path | string | yes | Exact publication date (YYYY-MM-DD) |

Returns `200 Success`.

---

## Public Inspection Documents

Documents filed for public inspection but not yet published. Use the document search above for already-published documents.

### GET /public-inspection-documents/{document_number}.{format}

| Parameter | In | Type | Required |
|---|---|---|---|
| `format` | path | string | yes |
| `document_number` | path | string | yes |

### GET /public-inspection-documents/{document_numbers}.{format}

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `format` | path | string | yes | Response format (`json`) |
| `document_numbers` | path | array[string] | yes | Document numbers, separated by a comma |

### GET /public-inspection-documents/current.{format}

Fetch all documents currently on public inspection.

| Parameter | In | Type | Required |
|---|---|---|---|
| `format` | path | string | yes |

### GET /public-inspection-documents.{format}

Search all documents currently on public inspection.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `format` | path | string | yes | Response format (`json`) |
| `fields[]` | query | array[string] | no | Which attributes to return |
| `per_page` | query | integer | no | How many to return at once; 1000 maximum. Default 20 |
| `page` | query | integer | no | Page of the result set |
| `conditions[available_on]` | query | string | **yes** | Public Inspection issue date (YYYY-MM-DD) |
| `conditions[term]` | query | string | no | Full text search |
| `conditions[agencies][]` | query | array[string] | no | Publishing agency |
| `conditions[type][]` | query | array[string] | no | Document type (`RULE`, `PRORULE`, `NOTICE`, `PRESDOCU`) |
| `conditions[special_filing]` | query | string | no | `0` Regular Filing, `1` Special Filing |
| `conditions[docket_id]` | query | string | no | Agency docket number |

Note `conditions[available_on]` is **required** on this endpoint.

---

## Agencies

### GET /agencies

Fetch all agency details. No parameters. Returns `200 Success`.

Use this to discover valid agency slugs for `conditions[agencies][]`.

### GET /agencies/{slug}

Fetch a particular agency's details.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | array[string] | yes | Federal Register slug for the agency |
| `id` | query | integer | no | **Deprecated.** Federal Register ID for the agency |

---

## Images

### GET /images/{identifier}

Fetch the available image variants and their metadata for a single image identifier.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `identifier` | path | string | yes | Federal Register image identifier |

Returns `200 Success` or `404 Not Found`.

---

## Suggested Searches

### GET /suggested_searches

Fetch all suggested searches, or limit by FederalRegister.gov section.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `conditions[sections]` | query | array[string] | no | Federal Register slug for the section |

### GET /suggested_searches/{slug}

Fetch a particular suggested search.

| Parameter | In | Type | Required | Description |
|---|---|---|---|---|
| `slug` | path | string | yes | Federal Register slug for the suggested search (e.g. `accountable-care-organizations`) |

---

## Schemas (enumerated value sets)

The documentation page lists these enums. Counts are the number of valid values; the full lists are available interactively on the docs page, and agency slugs can also be fetched from `/agencies`.

| Schema | Type | Count |
|---|---|---|
| `Agency` | array[string] | 473 |
| `DocumentField` | array[string] | 56 |
| `DocumentType` | array[string] | 4 |
| `Facet` | string | 10 |
| `PublicInspectionDocumentField` | array[string] | 27 |
| `President` | array[string] | 6 |
| `PresidentialDocumentType` | array[string] | 7 |
| `Section` | array[string] | 6 |
| `SuggestedSearch` | string | 93 |
| `Topic` | array[string] | 7798 |

---

## Notes for building against this API

- No authentication, so no key management and no rate-limit headers to track.
- `conditions[term]` is full-text search — the natural place for UAS keyword filters.
- `conditions[docket_id]` is the join key to the Regulations.gov API, which holds the docket's public comments.
- `conditions[agencies][]` takes slugs, not display names. Fetch them once from `/agencies` and cache.
- `per_page` maxes at 1000, far above most other federal APIs, so pagination is rarely needed for a weekly window.
- `conditions[significant]` filters to rules deemed significant under EO 12866 — a cheap way to separate consequential rulemakings from routine notices.
