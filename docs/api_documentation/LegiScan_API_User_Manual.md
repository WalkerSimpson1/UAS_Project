# LegiScan API v1.91 - User Manual

Revision 20250317. Converted from the official PDF published by LegiScan LLC.
Source: https://legiscan.com/gaits/documentation/legiscan

## Introduction

The LegiScan API is a legislative data web-service offering uniform data from multiple states with access to a wide array of legislative information including detailed bill information and history, full bill texts, roll call information and more. LegiScan saves time and assimilates legislative data in a manner that enhances analysis and communications with those who matter most and remain aware of germane legislation and issues that affects you.

## Started Getting

To use the LegiScan API you need an API Key which can be obtained at LegiScan. All it takes to get started is an authenticated OneVote free public service account with LegiScan. The API registration form can be accessed below. Once registered you can also visit the same link to get an API status report and adjust various API Profile settings.

```
https://legiscan.com/legiscan
```

## API Interactions

Interacting with the LegiScan API comes in two basic flavors: Pull and Push. Both contain the same data payloads and is differentiated primarily by which side is responsible for returning the status response. Except for Push adding fields of last_push and the reasons array. These fields include a timestamp of the last successful Push and a list of 25 unique flags of what changed about the bill to trigger the Push.

### Bulk API

The Bulk API utilizes the weekly datasets that contain all getBill, getRollCall and getPerson payload records as individual JSON files in separate ZIP archives for each session. These can then be processed to import a new or update an existing session in its entirety. Also available through the getDatasetList and getDataset API operations.

```
https://legiscan.com/datasets
```

### Pull API

The Pull API is the basic setup for LegiScan interactions. It is an interface with resources determined by URI query strings. It is the sole responsibility of the client to generate requests into the API to determine and obtain updated information. Public service keys have a monthly limit of 30,000 queries to mirror the OneVote public tracking service.

### Push API

The Push API is available as a paid subscription service to clients that require real-time updates, from a single state to the entire nation, that are pushed every 15 minutes to 4 hours as changes are detected in bill information. The client's push endpoint is responsible for processing the payload information. Though many applications can be driven by the master bill payload, the endpoint can request bill text or vote information that is missing for local storage that will also be sent to fully replicate the national database remotely.

## LegiScan API Client Source Code

While users are free to develop their own interface into the API we encourage users to investigate the LegiScan API Client which is a turnkey application that supports all current API features and interactions including Pull, Push and Bulk with a defined database schema.

```
https://api.legiscan.com/dl

https://api.legiscan.com/docs
```

## Pull Interface

### Query String

The general format for Pull API calls is:

```
https://api.legiscan.com/?key=APIKEY&op=OPERATION&PARAMS
```

Where APIKEY is a valid LegiScan API key, OPERATION is one of the operation requests defined below, and PARAMS is a set of name-value pairs that are specific to each operation as defined below. The appropriate Content-Type header will be set for the responding payload as application/json for JSON responses.

```
Keys, operations, state abbreviations and bill numbers are case-insensitive.
```

### Status Response

The responding side of a request is responsible for returning a status element that indicates if the operation completed successfully. For Pull requests, status is returned by LegiScan; for Push responses, status is returned by the endpoint listener.

A successful response must use status = OK (case sensitive)

```
JSON OK Response
{
 "status": "OK",
 ...
}
```

A failed response must use status = ERROR (case sensitive) and may optionally include an alert message with further information on the nature of the failure.

```
JSON ERROR Response
{
 "status": "ERROR",
 "alert": {
   "message": "IMPORTANT_SYSTEM_MESSAGE"
 }
}
```

### Update Workflow

The typical workflow for maintaining session data begins with Bulk loading the appropriate datasets, then periodically using getMasterListRaw to compare current change_hash with stored value for each bill and using getBill to retrieve and update those bills that have changed. See the LegiScan API Client worker daemon for this and other synchronization strategies.

```
 https://api.legiscan.com/docs/class-LegiScan_Worker.html
```

## API Operations

The following operations are available in the Pull API; the Frequency are guidelines for how often an individual request for each operation should be performed or be refreshed, reflecting public cache, internal scheduling and reasonable logic.

```
Operation                Description                                                             Frequency
getSessionList           Retrieve session list for a particular state                            Daily
getMasterList            Retrieve master bill list for a session                                 1 hour
getMasterListRaw         Retrieve master bill list optimized for change_hash detection           1 hour
getBill                  Retrieve bill detail information for a given bill_id                    3 hours
getBillText              Retrieve full bill text for a given doc_id                              Static
getAmendment             Retrieve amendment text for a given amendment_id                        Static
getSupplement            Retrieve supplemental document for a given supplement_id                Static
getRollCall              Retrieve roll call vote information for a given roll_call_id            Static
getPerson                Retrieve basic information for a given people_id                        Weekly
getSearch                Retrieve results from the full text search engine (50 results)          1 hour
getSearchRaw             Retrieve results from the full text search engine (2000 results)        1 hour
getDatasetList           Retrieve list of available dataset snapshots                            Weekly
getDataset               Retrieve an individual dataset for a specific session_id                Weekly
getSessionPeople         Retrieve list of people active in a specific session_id                 Weekly
getSponsoredList         Retrieve list of bills sponsored by an individual people_id             Daily
getMonitorList           Retrieve bills from GAITS monitor list                                  1 hour
getMonitorListRaw        Retrieve bills from GAITS monitor list for change_hash detection        1 hour
setMonitor               Interact with GAITS to add/remove bills from monitor/ignore lists       Live
```

For clarity, the Frequency is not how often data requests should happen, reflecting rather the minimum time resolution that could include changes in data. Requests that exceed these recommendations will be served unchanged cached data while still spending an API query operation. In practice most use cases can be satisfied with daily updates.

### Internal Identifiers

Each data payload uses a unique positive integer value to globally identify the object.

| Object | Value |
|---|---|
| session_id | Identifier for getMasterList, getMasterListRaw, getDataset, getSessionPeople |
| bill_id | Identifier for getBill |
| doc_id | Identifier for getBillText |
| amendment_id | Identifier for getAmendment |
| supplement_id | Identifier for getSupplement |
| roll_call_id | Identifier for getRollCall |
| people_id | Identifier for getPerson, getSponsoredList |

### getSessionList

**Description**

This operation returns a list of sessions that are available for access in the given state abbreviation, or all sessions if no state is given.

**Input Parameters**

| Name | Value |
|---|---|
| state | (Optional) Retrieve a list of available sessions for the given state abbreviation |

**Pull API Template**

```
    https://api.legiscan.com/?key=APIKEY&op=getSessionList

    https://api.legiscan.com/?key=APIKEY&op=getSessionList&state=STATE
```

**Client Source Code**

```
    https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

List of session information including session_id for subsequent getMasterList calls along with session years, special session indicator and the dataset_hash which reflects the current dataset version for the session_id for identifying and tracking when archives change.

```
JSON
{
"status": "OK",
"sessions": [
  {
     "session_id": 1791,
     "state_id": 5,
     "year_start": 2021,
     "year_end": 2022,
     "prefile": 0,
     "sine_die": 0,
     "prior": 0,
     "special": 0,
     "session_tag": "Regular Session",
     "session_title": "2021-2022 Regular Session",
     "session_name": "2021-2022 Session",
     "dataset_hash": "ead44c3c2a0055a7ecbc5c13ebd71f70"
  },
  {
     "session_id": 1624,
     "state_id": 5,
     "year_start": 2019,
     "year_end": 2020,
     "prefile": 0,
     "sine_die": 1,
     "prior": 1,
     "special": 0,
     "session_tag": "Regular Session",
     "session_title": "2019-2020 Regular Session",
     "session_name": "2019-2020 Session",
     "dataset_hash": "b42df4087677a62e0fbe86f4e0b11b47"
  },
  ...
}
```

### getMasterList

**Description**

This operation returns a master list of summary bill data in the given session_id or current state session.

**Input Parameters (Invocation A)**

| Name | Value |
|---|---|
| id | Retrieve bill master list for the session_id as given by id |

**Input Parameters (Invocation B)**

| Name | Value |
|---|---|
| state | Retrieve bill master list for "current" session in the given state (use with caution) |

**Pull API Template**

```
    https://api.legiscan.com/?key=APIKEY&op=getMasterList&state=STATE

    https://api.legiscan.com/?key=APIKEY&op=getMasterList&id=SESSION_ID
```

**Client Source Code**

```
    https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

List of bill information including bill_id and bill_number. The change_hash is a representation of the current bill status; it should be stored for a quick comparison to subsequent getMasterList calls to detect what bills have changed and need updating via getBill.

```
JSON
{
"status": "OK",
"masterlist": {
  "0": {
     "bill_id": 1132030,
     "number": "AB1",
     "change_hash": "d72444d8f2026219e38cb2179dcc67a0",
     "url": "https://legiscan.com/CA/bill/AB1/2019",
     "status_date": "2018-12-03",
     "status": "1",
     "last_action_date": "2018-12-04",
     "last_action": "From printer. May be heard in committee January 3.",
     "title": "Youth athletics: California Youth Football Act.",
     "description": "An act to add Article 2.7 to the Health and Safety"
  },
  "1": {
     "bill_id": 1131894,
     "number": "AB2",
     "change_hash": "d733e82d03a815e568f92e66f6fd87dc",
     "url": "https://legiscan.com/CA/bill/AB2/2019",
     "status_date": "2018-12-03",
     "status": "1",
     "last_action_date": "2018-12-04",
     "last_action": "From printer. May be heard in committee January 3.",
     "title": "Community colleges: California College Promise.",
     "description": "An act to amend Section 76396.3 postsecondary"
  },
  ...
}
```

### getMasterListRaw

**Description**

This operation returns a master list of bill change_hash data in the given session_id or current state session.

**Input Parameters (Invocation A)**

| Name | Value |
|---|---|
| id | Retrieve bill master list for the session_id as given by id |

**Input Parameters (Invocation B)**

| Name | Value |
|---|---|
| state | Retrieve bill master list for "current" session in the given state (use with caution) |

**Pull API Template**

```
   https://api.legiscan.com/?key=APIKEY&op=getMasterListRaw&id=SESSION_ID

   https://api.legiscan.com/?key=APIKEY&op=getMasterListRaw&state=STATE
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

List of bill information including bill_id and bill_number. The change_hash is a representation of the current bill status; it should be stored for a quick comparison to subsequent getMasterListRaw calls to detect what bills have changed and need updating via getBill.

JSON {

```
"status": "OK",
"masterlist": {
 "0": {
    "bill_id": 1132030,
    "number": "AB1",
    "change_hash": "d72444d8f2026219e38cb2179dcc67a0",
 },
 "1": {
    "bill_id": 1131894,
    "number": "AB2",
    "change_hash": "d733e82d03a815e568f92e66f6fd87dc",
 },
 "2": {
    "bill_id": 1132059,
    "number": "AB3",
    "change_hash": "39bb633b8b80ee8223fc7b83814ca705"
 },
 "3": {
    "bill_id": 1131884,
    "number": "AB4",
    "change_hash": "42a452ffc18126030dc5dc11958fc9cf"
 },
 ...
```

}

### getBill

**Description**

This operation returns the primary bill detail information including sponsors, committee references, full history, bill text and roll call information.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve bill detail information for bill_id as given by id |

**Pull API Template**

```
   https://api.legiscan.com/?key=APIKEY&op=getBill&id=BILL_ID
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Detail bill information object enumerating associated information along with sponsor people_id, bill text doc_id and voting roll_call_id. The change_hash is a representation of the current bill version, it should be stored for a quick comparison to subsequent getMasterListRaw calls to detect when bills have changed and need updating.

```
JSON
{
"status": "OK",
"bill": {
"bill_id": 1167968,
"change_hash": "0176f32311854cf3ab1ab69a94e237e5",
"session_id": 1636,
"session": {
   "session_id": 1636,
   "state_id": 20,
   "year_start": 2019,
   "year_end": 2019,
   "prefile": 0,
   "sine_die": 1,
   "prior": 1,
   "special": 0,
   "session_tag": "Regular Session",
   "session_title": "2019 Regular Session",
   "session_name": "2019 Regular Session"
},
"url": "https://legiscan.com/MD/bill/SB181/2019",
"state_link":
"http://mgaleg.maryland.gov/mgawebsite/Legislation/Details/SB0181?ys=2019rs",
"completed": 1,
"status": 4,
"status_date": "2019-05-25",
"progress": [
   {
      "date": "2019-01-23",
      "event": 1
   },
   {
      "date": "2019-01-23",
      "event": 9
   }
],

"state": "MD",
"state_id": 20,
"bill_number": "SB181",
"bill_type": "B",
"bill_type_id": "1",
"body": "S",
"body_id": 50,
"current_body": "S",
"current_body_id": 50,
"title": "Education - Child Care Subsidies - Mandatory Funding Level",
"description": "Altering certain classes of goods and services for which a person
```

may register a mark; prohibiting a person from registering a certain name or surname as a mark; requiring an applicant for registration of a mark or renewal of a mark to submit 3 different specimens or reproductions of the mark as used; prohibiting the specimens or reproductions from including certain business papers; requiring the Secretary of State to include a full description of the mark on a certain certificate of registration; etc.",

```
"pending_committee_id": 1928,
"committee": {
"committee_id": 1928,
"chamber": "H",
"chamber_id": 49,
"name": "Ways and Means"
},
"referrals": [
{
  "date": "2019-01-23",
  "committee_id": 1929,
  "chamber": "S",
  "chamber_id": 50,
  "name": "Budget and Taxation"
},
{
  "date": "2019-02-22",
  "committee_id": 1922,
  "chamber": "H",
  "chamber_id": 49,
  "name": "Appropriations"
}
],
"history": [
{
  "date": "2019-01-23",
  "action": "First Reading Budget and Taxation",
  "chamber": "S",
  "chamber_id": 50,
  "importance": 1
},
{
  "date": "2019-02-18",
  "action": "Favorable Report by Budget and Taxation",
  "chamber": "S",
  "chamber_id": 50,
  "importance": 1
},
{
  "date": "2019-02-21",
  "action": "Third Reading Passed (46-0)",
  "chamber": "S",
  "chamber_id": 50,
  "importance": 1
},
{

   "date": "2019-03-25",
   "action": "Third Reading Passed (125-4)",
   "chamber": "H",
   "chamber_id": 49,
   "importance": 0
},
{
 "date": "2019-05-25",
 "action": "Enacted under Article II, Section 17(c) of the Maryland
```

Constitution - Chapter 596",

```
 "chamber": "S",
 "chamber_id": 50,
 "importance": 1
}
],
"sponsors": [
{
 "people_id": 4718,
 "person_hash": "qp3urxai",
 "party_id": 1,
 "party": "D",
 "role_id": 2,
 "role": "Sen",
 "name": "Nancy King",
 "first_name": "Nancy",
 "middle_name": "",
 "last_name": "King",
 "suffix": "",
 "nickname": "",
 "district": "SD-039",
 "ftm_eid": 5304165,
 "votesmart_id": 36382,
 "opensecrets_id": "",
 "knowwho_pid": 213267,
 "ballotpedia": "Nancy_King",
 "sponsor_type_id": 1,
 "sponsor_order": 1,
 "committee_sponsor": 0,
 "committee_id": "0"
}
],
"sasts": [
{
 "type_id": 5,
 "type": "Crossfiled",
 "sast_bill_number": "SB10",
 "sast_bill_id": 1293389
}
],
"subjects": [
{
 "subject_id": 3309,
 "subject_name": "Education"
}
],
"texts": [
{
 "doc_id": 1868195,
 "date": "2019-01-23",
 "type": "Introduced",
 "type_id": 1,
 "mime": "application/pdf",
 "mime_id": 2,

"url": "https://legiscan.com/MD/text/SB181/id/1868195",
"state_link": "http://mgaleg.maryland.gov/2019RS/bills/sb/sb0181f.pdf",
"text_size": 85467,
"text_hash": "423ba752efdfa002d991006e2b358a7f"
}
],
"votes": [
{
  "roll_call_id": 806862,
  "date": "2019-02-21",
  "desc": "Senate Floor - Third Reading Passed (46-0)",
  "yea": 47,
  "nay": 1,
  "nv": 1,
  "absent": 2,
  "total": 51,
  "passed": 1,
  "chamber": "S",
  "chamber_id": 50,
  "url": "https://legiscan.com/MD/rollcall/SB181/id/806862",
  "state_link": "http://mgaleg.maryland.gov/2019RS/votes/Senate/0250.pdf"
}
],
"amendments": [
{
  "amendment_id": 72535,
  "adopted": 1,
  "chamber": "S",
  "chamber_id": 50,
  "date": "0000-00-00",
  "title": "Senate Amendment (Senator Pinsky) 723129/01",
  "description": "Senate Amendment (Senator Pinsky) 723129/01",
  "mime": "application/pdf",
  "mime_id": 2,
  "url": "https://legiscan.com/MD/amendment/SB1030/id/72535",
  "state_link":
```

"http://mgaleg.maryland.gov/2019RS/amds/bil_0000/sb1030_72312901.pdf",

```
  "amendment_size": 27525,
  "amendment_hash": "d259b164825990e655e5c388042180c7"
}
],
"supplements": [
{
  "supplement_id": 94182,
  "date": "0000-00-00",
  "type": "Fiscal Note/Analysis",
  "type_id": 3,
  "title": "Analysis",
  "description": "Fiscal Note/Analysis",
  "mime": "application/pdf",
  "mime_id": 2,
  "url": "https://legiscan.com/MD/supplement/SB181/id/94182",
  "state_link": "http://mgaleg.maryland.gov/2019RS/fnotes/bil_0001/sb0181.pdf",
  "supplement_size": 124662,
  "supplement_hash": "b6d1cb83cd49e558d93127317efef2ce"
},
{
  "supplement_id": 96165,
  "date": "0000-00-00",
  "type": "Vote Image",
  "type_id": 4,
  "title": "Vote - Senate - Committee Budget and Taxation",
  "description": "Vote Image",

    "mime": "application/pdf",
    "mime_id": 2,
    "url": "https://legiscan.com/MD/supplement/SB181/id/96165",
    "state_link": "http://mgaleg.maryland.gov/2019RS/votes_comm/sb0181_b&t.pdf",
    "supplement_size": 170981,
    "supplement_hash": "800c0705ce0152441e60da424e75c6b8"
   }
],
"calendar": [
   {
     "type_id": 1,
     "type": "Hearing",
     "date": "2019-03-19",
     "time": "13:00",
     "location": "",
     "description": "House Appropriations Hearing"
   }
]
}
```

}

### getBillText

**Description**

This operation returns an individual copy of a bill text.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve bill text information for doc_id as given by id |

**Pull API Template**

```
   https://api.legiscan.com/?key=APIKEY&op=getBillText&id=DOC_ID
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Bill text including date, draft revision information and MIME type, the bill text itself is base64 encoded to allow for binary PDF/Word transfers.

```
JSON
{
"status":"OK",
"text":{
  "doc_id": 647508,
  "bill_id": 502329,
  "date": "2012-05-23",
  "type": "Enrolled",
  "type_id": "5,
  "mime": "application/pdf",
  "mime_id": 2,
  "text_size": 1724832,
  "text_hash": "bcd4ee6b03d1ae74a8b225f9b85b6863",
  "doc": "MIME 64 Encoded Document"
}
}
```

### getAmendment

**Description**

This operation returns an individual copy of an amendment.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve amendment information for amendment_id as given by id |

**Pull API Template**

```
  https://api.legiscan.com/?key=APIKEY&op=getAmendment&id=AMENDMENT_ID
```

**Client Source Code**

```
  https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Amendment text including date, adoption status, along with title/description information and MIME type, the amendment text itself is base64 encoded to allow for binary PDF/Word transfers.

JSON {

```
"status":"OK",
"amendment": {
 "amendment_id": 37508,
 "chamber": "S",
 "chamber_id": 36,
 "bill_id": 852200,
 "adopted": 0,
 "date": "2016-04-01",
 "title": "Senate Amendment 001",
 "description": "Senate Amendment 001",
 "mime": "text/html",
 "mime_id": 1,
 "amendment_size": 6914,
 "amendment_hash": "350a599b8db27a1999d17efd71b420d6",
 "doc": "MIME 64 Encoded Document"
}
```

}

### getSupplement

**Description**

This operation returns a supplemental document such as fiscal notes, veto letters, etc.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve supplement information for supplement_id as given by id |

**Pull API Template**

```
  https://api.legiscan.com/?key=APIKEY&op=getSupplement&id=SUPPLEMENT_ID
```

**Client Source Code**

```
  https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Supplement text including type of supplement, date, along with title/description information and MIME type, the supplement text itself is base64 encoded to allow for binary PDF/Word transfers.

JSON {

```
"status":"OK",
"supplement": {
 "supplement_id": 47508,
 "bill_id": 853693,
 "date": "0000-00-00",
 "type_id": 3,
 "type": "Fiscal Note/Analysis",
 "title": "Analysis",
 "description": "Fiscal Note/Analysis",
 "mime": "application/pdf",
 "mime_id": 2,
 "supplement_size": 134305,
 "supplement_hash": "ba83212a92b26b34d3a1b751e0d45ad1",
 "doc": "MIME 64 Encoded Document"
}
```

}

### getRollCall

**Description**

This operation returns a vote record detail with summary information and individual vote information.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve vote detail information for roll_call_id as given by id |

**Pull API Template**

```
   https://api.legiscan.com/?key=APIKEY&op=getRollcall&id=ROLL_CALL_ID
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Roll call detail for individual votes for people_id and summary result information.

```
JSON
{
"status":"OK",
"roll_call": {
  "roll_call_id": 234223,
  "bill_id": 460445,
  "date": "2013-02-20",
  "desc": "House: Human Services Subcommittee: DO PASS",
  "yea": 2,
  "nay": 1,
  "nv": 1,
  "absent": 1,
  "total": 5,
  "passed": 1,
  "chamber": "H",
  "chamber_id": 79,
  "votes":[
    {
       "people_id": 3709,
       "vote_id": 1,
       "vote_text": "Yea"
    },
    {
       "people_id": 3715,
       "vote_id": 2,
       "vote_text": "Nay"
    },
    {
       "people_id": 3723,
       "vote_id": 3,
       "vote_text": "NV"
    },
    {
       "people_id": 12137,
       "vote_id": 4,
       "vote_text": "Absent"
    },
    ...
  ]
}
}
```

### getPerson

**Description**

This operation returns a record with basic sponsor information and third party identifiers.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve person information for people_id as given by id |

**Pull API Template**

```
  https://api.legiscan.com/?key=APIKEY&op=getPerson&id=PEOPLE_ID
```

**Client Source Code**

```
  https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Sponsor information including name information, party affiliation and role along with identifiers for third party data sources:

```
           https://followthemoney.org/               https://ballotpedia.org/
           https://votesmart.org/                    https://opensecrets.org/
```

Note person records reflect the current status, and if viewed in prior session context may have different role, district or party affiliation. The person_hash is a representation of the current record values, it can be stored for comparison to subsequent person records to detect when information has changed and needs updating.

JSON {

```
"status":"OK",
"person": {
 "people_id": 16788,
 "person_hash": " 1s25cljm",
 "state_id": 46,
 "party_id": "1",
 "party": "D",
 "role_id": 1,
 "role": "Rep",
 "name": " Joseph Preston",
 "first_name": " Joseph ",
 "middle_name": "E.",
 "last_name": "Preston",
 "suffix": "",
 "nickname": "",
 "district": "HD-063",
 "ftm_eid": 7290094,
 "votesmart_id": 154843,
 "opensecrets_id": "",
 "knowwho_pid": 523841,
 "ballotpedia": " Joseph_Preston_(Virginia)",
 "committee_sponsor": 0,
 "committee_id": 0
}
```

}

### getSearch

**Description**

Performs a search against the national database using the LegiScan full text engine, returning a paginated result set, appropriate to drive an interactive search appliance.

Further Reading:

```
 https://legiscan.com/bill-numbers
 https://legiscan.com/fulltext-search
```

**Input Parameters (Invocation A)**

| Name | Value |
|---|---|
| state | State abbreviation to search on, or ALL for entire nation |
| query | Full text query string to run against the search engine, URL encoded |
| year | (Optional) Year where 1=all, 2=current, 3=recent, 4=prior, >1900=exact [Default: 2] |
| page | (Optional) Result set page number to return [Default: 1] |

**Input Parameters (Invocation B)**

| Name | Value |
|---|---|
| id | Search a single specific session_id as given by id |
| query | Full text query string to run against the search engine, URL encoded |
| page | (Optional) Result set page number to return [Default: 1] |

**Pull API Template**

```
   https://api.legiscan.com/?key=APIKEY&op=getSearch&state=STATE&query=QUERY

   https://api.legiscan.com/?key=APIKEY&op=getSearch&id=SESSION_ID&query=QUERY
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Page of search results based on relevance to the given search parameters. The change_hash should be stored for a quick comparison on subsequent calls to detect when bills have changed and need updating.

JSON {

```
"status":"OK",
"searchresult":{
 "summary":{
    "page":"1 of 451",
    "range":"1 - 50",
    "relevancy":"100% - 99%",
    "count":22525,
    "page_current":1,
    "page_total":451
 },
 "0":{
    "relevance": 100,
    "state": "IN",
    "bill_number": "SB0544",
    "bill_id": 449621,
    "change_hash": "76132191d8644cee472e05bf5f3ec515",
    "url": "https://legiscan.com/IN/bill/SB0544/2013",

  "text_url": "https://legiscan.com/IN/text/SB0544/2013",
  "research_url": "https://legiscan.com/IN/research/SB0544/2013",
  "last_action_date": "2012-05-10",
  "last_action": "Public Law 261",
  "title": "State and local tax administration."
},
"1":{
 ...
}
},
...
```

}

### getSearchRaw

**Description**

Performs a search against the national database using the LegiScan full text engine, returning 2000 results at a time with simplified details, appropriate for automated keyword monitoring.

**Input Parameters**

| Name | Value |
|---|---|
| state | State abbreviation to search on, or ALL for entire nation |
| query | Full text query string to run against the search engine, URL encoded |
| year | (Optional) Year where 1=all, 2=current, 3=recent, 4=prior, >1900=exact [Default: 2] |
| id | (Optional) Limit search to a specific session_id as given by id |
| page | (Optional) Result set page number to return [Default: 1] |

**Input Parameters (Invocation B)**

| Name | Value |
|---|---|
| id | Search a single specific session_id as given by id |
| query | Full text query string to run against the search engine, URL encoded |
| page | (Optional) Result set page number to return [Default: 1] |

Example Calls:

```
   https://api.legiscan.com/?key=APIKEY&op=getSearchRaw&state=STATE&query=QUERY

   https://api.legiscan.com/?key=APIKEY&op=getSearchRaw&id=SESSION_ID&query=QUERY
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

Page of search results based on relevance to the given search parameters. The change_hash should be stored for a quick comparison on subsequent calls to detect when bills have changed and need updating.

JSON {

```
"status":"OK",
"searchresult":{
 "summary":{
    "page":"1 of 451",
    "range":"1 - 2000",
    "relevancy":"100% - 99%",
    "count":22525,
    "page_current":1,
    "page_total":14
 },
 "results": [
    "0":{
       "relevance":100,
       "bill_id":449621,
       "change_hash": "983e627cc15be727c85d4d9bd563e989",
    },
    "1":{
       "relevance":99,
       "bill_id":431262,
       "change_hash": "5b10d1d4288e161a01dbad10276ae7ed",
    },

   "2":{
     "relevance":99,
     "bill_id":425231,
     "change_hash": "800c0705ce0152441e60da424e75c6b8",
   }
   "3":{
     ...
   }
]
},
...
```

}

### getDatasetList

**Description**

This operation returns a list of available session datasets, with optional state and year filtering.

**Input Parameters**

| Name | Value |
|---|---|
| state | (Optional) Filter dataset results for a given state |
| year | (Optional) Filter dataset results for a given year |

Example Calls:

```
    https://api.legiscan.com/?key=APIKEY&op=getDatasetList

    https://api.legiscan.com/?key=APIKEY&op=getDatasetList&state=STATE

    https://api.legiscan.com/?key=APIKEY&op=getDatasetList&year=YEAR
```

**Client Source Code**

```
    https://api.legiscan.com/docs/class-LegiScan_Import.html
```

**Response**

List of dataset information including session_id and access_key which will be required for getDataset. The dataset_hash is a representation of the current archive version, not the file itself, it should be stored for a quick comparison to subsequent getDatasetList calls to detect when archives have changed and need retrieval.

```
JSON
{
"status": "OK",
"datasetlist": [
  {
     "state_id": 5,
     "session_id": 1624,
     "special": 0,
     "year_start": 2019,
     "year_end": 2020,
     "session_name": "2019-2020 Regular Session",
     "session_title": "Regular Session",
     "dataset_hash": "e0f2b493b637ec870ca886931bfa9896",
     "dataset_date": "2020-01-19",
     "dataset_size": 11958086,
     "access_key": "3Qd0kRszXtZuRloonDQx63",
  },
  {
     "state_id": 5,
     "session_id": 1400,
     "special": 0,
     "year_start": 2017,
     "year_end": 2018,
     "session_name": "2017-2018 Regular Session",
     "session_title": "Regular Session",
     "dataset_hash": "64eaa5580417096fdc2b3d06402b2841",
     "dataset_date": "2018-12-09",
     "dataset_size": 22138644,
     "access_key": "5R6lftn5PdMvoZF9yMsE9V",
  }
]
}
```

### getDataset

**Description**

This operation returns a single ZIP archive for the requested dataset containing all bills, votes and people data for the specified session in individual JSON or CSV files. The ZIP is returned as an encoded JSON response.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve dataset archive information for session_id as given by id |
| access_key | Access key from getDatasetList for the session_id being requested |
| format | (Optional) Data file format for ZIP file contents where json=JSON, csv=CSV [Default: json] |

Example Calls:

```
   https://api.legiscan.com/?key=APIKEY&op=getDataset&id=SESSION_ID&access_key=ACCESS_KEY
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Import.html
```

**Response**

Dataset archive including meta information, the dataset itself is base64 encoded to allow for binary ZIP transfers.

```
JSON
{
"status": "OK",
"dataset": {
  "state_id": 5,
  "session_id": 1624,
  "session_name": "2019-2020 Regular Session",
  "dataset_hash": "1c7d77fe298a4d30ad763733ab2f8c84",
  "dataset_date": "2018-12-23",
  "dataset_size": 317775,
  "mime": "application/zip",
  "zip": "MIME 64 Encoded ZIP Archive"
}
}
```

### getDatasetRaw

**Description**

This operation returns a single ZIP archive for the requested dataset containing all bills, votes and people data for the specified session in individual JSON or CSV files. The ZIP is returned as a raw binary data stream.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve dataset archive information for session_id as given by id |
| access_key | Access key from getDatasetList for the session_id being requested |
| format | (Optional) Data file format for ZIP file contents where json=JSON, csv=CSV [Default: json] |

Example Calls:

```
 https://api.legiscan.com/?key=APIKEY&op=getDatasetRaw&id=SESSION_ID&access_key=ACCESS_KEY
```

**Client Source Code**

```
 https://api.legiscan.com/docs/class-LegiScan_Import.html
```

**Response**

Dataset archive including meta information, the dataset itself is base64 encoded to allow for binary ZIP transfers.

ZIP

ZIP format binary data stream

### getSessionPeople

**Description**

This operation returns a list of people records for all those individuals active in a given session.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve active people list for session_id as given by id |

Example Calls:

```
  https://api.legiscan.com/?key=APIKEY&op=getSessionPeople&id=SESSION_ID
```

**Response**

A list of getPerson equivalent records for legislators that have sponsor or vote activity in the given session.

JSON {

```
"status": "OK",
"sessionpeople": {
 "session": {
    "session_id": 1624,
    "state_id": 5,
    "year_start": 2019,
    "year_end": 2020,
    "special": 0,
    "prefile": 0,
    "prior": 1,
    "sine_die": 1,
    "session_name": "Regular Session",
    "name": "2019-2020 Regular Session",
    "dataset_hash": "d07f740e40e69cc9b3aa55fe69d0ff6b"
 },
 "people": [
    {
       "people_id": 1498,
       "person_hash": "03qcgdwg",
       "state_id": 5,
       "party_id": "1",
       "party": "D",
       "role_id": 2,
       "role": "Sen",
       "name": "Jim Beall",
       "first_name": "Jim",
       "middle_name": "",
       "last_name": "Beall",
       "suffix": "Jr.",
       "nickname": "",
       "district": "SD-015",
       "ftm_eid": "776647",
       "votesmart_id": 58229,
       "opensecrets_id": "",
       "ballotpedia": "James_Beall_Jr.",
       "committee_sponsor": 0,
       "committee_id": 0
    },
    ...
 ]
}
```

}

### getSponsoredList

**Description**

This operation returns a list of bills sponsored by an individual legislator.

**Input Parameters**

| Name | Value |
|---|---|
| id | Retrieve list of bills sponsored by people_id as given by id |

Example Calls:

```
  https://api.legiscan.com/?key=APIKEY&op=getSponsoredList&id=PEOPLE_ID
```

**Response**

The associated getPerson equivalent record for the people_id, a list of sessions with had activity and the list of sponsored bills itself.

JSON {

```
"status": "OK",
"sponsoredbills": {
 "sponsor": {
    "people_id": 1498,
    "person_hash": "03qcgdwg",
    "state_id": 5,
    "party_id": "1",
    "party": "D",
    "role_id": 2,
    "role": "Sen",
    "name": "Jim Beall",
    "first_name": "Jim",
    "middle_name": "",
    "last_name": "Beall",
    "suffix": "Jr.",
    "nickname": "",
    "district": "SD-015",
    "ftm_eid": "776647",
    "votesmart_id": 58229,
    "opensecrets_id": "",
    "knowwho_pid": 248442,
    "ballotpedia": "James_Beall_Jr.",
    "committee_sponsor": 0,
    "committee_id": 0
 },
 "sessions": [
    {
       "session_id": 1624,
       "session_name": "2019-2020 Regular Session"
    },
    {
       "session_id": 1400,
       "session_name": "2017-2018 Regular Session"
    },
    {
       "session_id": 1120,
       "session_name": "2015-2016 Regular Session"
    },
    {
       "session_id": 993,
       "session_name": "2013-2014 Regular Session"

  },
  {
       "session_id": 82,
       "session_name": "2011-2012 Session"
   }
],
"bills": [
   {
       "session_id": 1624,
       "bill_id": 1131906,
       "number": "AB8"
   },
   {
       "session_id": 1400,
       "bill_id": 937925,
       "number": "AB249"
   },
   {
       "session_id": 1120,
       "bill_id": 704240,
       "number": "AB194"
   },
     {
       "session_id": 993,
       "bill_id": 624598,
       "number": "AB2126"
   },
   {
       "session_id": 82,
       "bill_id": 391931,
       "number": "AB1611"
   }
]
}
```

}

### getMonitorList

**Description**

This operation returns the GAITS monitor list of summary bill data being tracked by the account associated with the API key.

**Input Parameters**

| Name | Value |
|---|---|
| record | (Optional) Record filter current or archived, 2010 >= exact year [Default: current] |

**Pull API Template**

```
   https://api.legiscan.com/?key=APIKEY&op=getMonitorList&record=current
```

**Client Source Code**

```
   https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

List of bill information including bill_id and bill_number. The change_hash is a representation of the current bill status, it should be stored for a quick comparison to subsequent getMonitorList calls to detect what bills have changed and need updating via getBill.

```
JSON
{
"status": "OK",
"monitorlist": {
  "0": {
     "bill_id": 1132030,
     "state": "CA",
     "number": "AB1",
     "stance": 2,
     "change_hash": "d72444d8f2026219e38cb2179dcc67a0",
     "url": "https://legiscan.com/CA/bill/AB1/2019",
     "status_date": "2018-12-03",
     "status": 1,
     "last_action_date": "2018-12-04",
     "last_action": "From printer. May be heard in committee January 3.",
     "title": "Youth athletics: California Youth Football Act.",
     "description": "An act to add Article 2.7 to the Health and Safety"
  },
  "1": {
     "bill_id": 1131894,
     "state": "CA",
     "number": "AB2",
     "stance": 1,
     "change_hash": "d733e82d03a815e568f92e66f6fd87dc",
     "url": "https://legiscan.com/CA/bill/AB2/2019",
     "status_date": "2018-12-03",
     "status": 1,
     "last_action_date": "2018-12-04",
     "last_action": "From printer. May be heard in committee January 3.",
     "title": "Community colleges: California College Promise.",
     "description": "An act to amend Section 76396.3 postsecondary"
  },
  ...
}
```

### getMonitorListRaw

**Description**

This operation returns the GAITS monitor list of bill change_hash data being tracked by the account associated with the API key.

**Input Parameters**

| Name | Value |
|---|---|
| record | (Optional) Record filter current or archived, 2010 >= exact year [Default: current] |

**Pull API Template**

```
  https://api.legiscan.com/?key=APIKEY&op=getMonitorListRaw

  https://api.legiscan.com/?key=APIKEY&op=getMonitorListRaw&record=archived
```

**Client Source Code**

```
  https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

List of bill information including bill_id and bill_number. The change_hash is a representation of the current bill status, it should be stored for a quick comparison to subsequent getMonitorListRaw calls to detect what bills have changed and need updating via getBill.

JSON {

```
"status": "OK",
"monitorlist": {
 "0": {
    "bill_id": 1132030,
    "state": "CA",
    "number": "AB1",
    "stance": 2,
    "change_hash": "d72444d8f2026219e38cb2179dcc67a0",
    "status": 1,
 },
 "1": {
    "bill_id": 1131894,
    "state": "CA",
    "number": "AB2",
    "stance": 1,
    "change_hash": "d733e82d03a815e568f92e66f6fd87dc",
    "status": 1,
 },
 "2": {
    "bill_id": 1132059,
    "state": "CA",
    "number": "AB3",
    "stance": 0,
    "change_hash": "39bb633b8b80ee8223fc7b83814ca705"
    "status": 1,
 },
 ...
```

}

### setMonitor

**Description**

This operation returns a master list of bill change_hash data in the given session_id or current state session.

**Input Parameters**

| Name | Value |
|---|---|
| list | List of one or more comma separate bill_id to operate on |
| action | Action to take on the bill list: monitor, remove, set |
| stance | (Optional) Position on the bill: watch, support, oppose [Default: watch] |

**Pull API Template**

```
  https://api.legiscan.com/?key=APIKEY&op=setMonitor&action=monitor&stance=watch&list=150334,141690

  https://api.legiscan.com/?key=APIKEY&op=setMonitor&action=set&stance=support&list= 1533377
```

**Client Source Code**

```
  https://api.legiscan.com/docs/class-LegiScan_Process.html
```

**Response**

The return status of each bill_ id in the list and the action taken, the overall API status response indicates the if request was valid to be processed.

JSON {

```
"status": "OK",
"return": {
 "1396551": "OK: Bill 1396551 added to monitor list with stance watch",
 "1401311": "OK: Bill 1401311 added to monitor list with stance watch",
 "1410699": "OK: Bill 1410699 added to monitor list with stance watch",
 "1425543": "OK: Bill 1425543 added to monitor list with stance watch",
 "1486510": "ERROR: adding 1486510 to monitor list",
 "1501580": "OK: Bill 1501580 added to monitor list with stance watch"
}
```

}

JSON {

```
"status": "OK",
"return": {
 "1396551": "OK: Set stance support for 1396551",
 "1401311": "OK: Set stance support for 1401311",
 "1410699": "OK: Set stance support for 1410699",
 "1425543": "OK: Set stance support for 1425543",
 "1486510": "ERROR: Bill 1486510 not on monitor list",
 "1501580": "OK: Set stance support for 1501580"
}
```

}

## Data Dictionary

### bill (getBill)

| Field | Type | Description |
|---|---|---|
| bill_id | integer | Internal bill id |
| change_hash | string | MD5 hash of bill status meta-data to aid change detection in Pull |
| last_push | datetime | Timestamp of last successful push (Push Only) |
| reasons[] | array | Array of flags indicating changes triggering push (Push Only) |
| session_id | integer | Internal session id |
| session[] | array | Array of session information |
| session_id | integer | Internal session id |
| year_start | integer | Starting year of the session |
| year_end | integer | Ending year of the session |
| prefile | integer | Flag for session being in prefile (0, 1) |
| sine_die | integer | Flag for session being adjourned sine die (0, 1) |
| prior | integer | Flag for session being archived out of production updates (0, 1) |
| special | integer | Flag for being a special session (0, 1) |
| session_name | string | State specific session name |
| session_title | string | Normalized session title with year(s) and Regular/Special Session |
| url | string | LegiScan URL |
| state_link | string | State URL |
| completed | integer | DEPRECATED DO NOT USE |
| status | integer | Internal progress id for Intro, Engross, Enroll, Pass, Veto |
| status_date | date | Date of status action |
| progress[][] | array | History array converted to significant progress steps to calculate status field |
| step[] | array | Individual progress records |
| date | date | Date of event |
| event | integer | Internal event type id matching the history action |
| state | string | State abbreviation |
| state_id | integer | Internal state id |
| bill_number | string | Bill number |
| bill_type | string | Type of instrument text |
| bill_type_id | integer | Internal bill type id |
| body | string | Originating body text |
| body_id | integer | Internal body id |
| current_body | string | Current body text |
| current_body_id | integer | Internal body id |
| title | string | Title |
| description | string | Description |
| pending_committee_id | integer | Internal committee id |
| committee[] | array | Array of committee info if currently pending |
| committee_id | integer | Internal committee id |
| chamber | string | Chamber of committee |
| chamber_id | integer | Internal body id |
| committee_name | string | Name of committee |
| referrals[][] | array | Array of history steps |
| referral[] | array | Individual history step |

```
date                date      Date of referral
committee_id        integer   Internal committee id
chamber             string    Chamber of committee
chamber_id          integer   Internal body id
committee_name      string    Name of committee
```

history[][]              array     Array of history steps step[]                 array     Individual history step

```
date                date      Date of action
action              string    Action step text
chamber             string    Chamber taking action
chamber_id          integer   Internal body id
importance          boolean   Flag for "major" steps, i.e., matches a progress condition (0, 1)
```

sponsors[][]             array     Array of sponsors sponsor[]              array     Individual sponsor record

```
people_id           integer   Internal people id
person_hash         string    Hash of the personal details to aid change detection
party_id            integer   Internal party id
party               string    Party text
role_id             integer   Internal role id
role                string    Role text
name                string    Full name
first_name          string    First name
middle_name         string    Middle name
last_name           string    Last name
suffix              string    Suffix
district            string    Legislative district
ftm_eid             integer   FollowTheMoney.org EID
votesmart_id        integer   VoteSmart.org ID
opensecrets_id      string    OpenSecrets.org ID (Congress Only)
knowwho_pid         integer   KnowWho.com PID
ballotpedia         string    Ballotpedia.org Name
sponsor_type_id     integer   Internal sponsor type id (primary, co, joint)
sponsor_order       integer   Index of order in sponsorship list
committee_sponsor   boolean   Committee sponsor flag (0, 1)
committee_id        integer   Internal committee id (if committee_sponsor)
```

sasts[][]                array     Array of same as/similar to relations sast[]                 array     Individual SA/ST record

```
type_id             integer   Internal sast id
type                string    Relationship text
sast_bill_number    string    Related bill number
sast_bill_id        integer   Internal bill id
```

subjects[][]             array     Array of subjects subject[]              array     Individual subject record

```
subject_id          integer   Internal subject id
subject_name        string    Subject name
```

texts[][]                array     Array of text drafts text[]                 array     Individual text record

```
doc_id              integer   Internal document id

date             date      Date of text if (available)
type             string    Type of draft text (introduced, amended, enrolled, comm sub, etc.)
type_id          integer   Internal bill text type id
mime             string    MIME type of document
mime_id          integer   Internal mime type id
url              string    LegiScan URL
state_link       string    State URL
text_size        integer   Size in bytes of the decoded BASE64 document (add 33% for base64)
text_hash        string    MD5 hash of the decoded BASE64 document
```

votes[][]            array     Array of related roll calls vote[]             array     Individual roll call record

```
roll_call_id     integer   Internal roll call id
date             date      vote date if available
desc             string    Roll call description
yea              integer   Count of yeas
nay              integer   Count of nays
nv               integer   Count of not voting
absent           integer   Count of absent
total            integer   Total number of votes
passed           boolean   Passed flag (0, 1)
chamber          string    Origin chamber of vote
chamber_id       integer   Internal body id
url              string    LegiScan URL
state_link       string    State URL
```

amendments[][]       array     Array of amendment documents amendment[]        array     Individual amendment record

```
amendment_id     integer   Internal amendment id
adopted          boolean   Flag for adoption (0, 1)
chamber          string    Origin chamber of amendment
chamber_id       integer   Internal body id
date             string    Amendment date (if available)
title            string    Amendment Title
description      string    Amendment Description
mime             string    MIME type of document
mime_id          integer   Internal mime type id
url              string    LegiScan URL
state_link       string    State URL
amendment_size   integer   Size in bytes of the decoded BASE64 document (add 33% for base64)
amendment_hash   string    MD5 hash of the decoded BASE64 document
```

supplements[][]      array     Array of supplemental documents supplement[]       array     Individual supplement record

```
supplement_id    integer   Internal supplement id
date             date      Supplement date (if available)
type_id          integer   Internal supplement type id
type             string    Supplement type text
title            string    Supplement title
description      string    Supplement description
mime             string    MIME type of document

mime_id              integer   Internal mime type id
url                  string    LegiScan URL
state_link           string    State URL
supplement_size      integer   Size in bytes of the decoded BASE64 document (add 33% for base64)
supplement_hash      string    MD5 hash of the decoded BASE64 document
```

calendar[][]             array     Array of events / hearings event[]                array     Individual event record

```
type_id              integer   Internal event type id
type                 string    Event type text
date                 date      Event date
time                 time      Event time (if available)
location             string    Event location (if available)
description          string    Event description
```

### roll_call (getRollCall)

| Field | Type | Description |
|---|---|---|
| roll_call_id | integer | Internal roll call id |
| bill_id | integer | Internal bill id |
| date | string | Vote date (if available) |
| desc | string | Vote description |
| yea | integer | Count of yeas |
| nay | integer | Count of nays |
| nv | integer | Count of not voting |
| absent | integer | Count of absent |
| total | integer | Total number of votes |
| passed | boolean | Passed flag (0, 1) |
| chamber | string | Origin chamber of vote |
| chamber_id | integer | Internal body id |
| votes[][] | array | Array of vote details |
| vote[] | array | Individual vote record |
| people_id | integer | Internal people id |
| vote_id | integer | Internal vote type id |
| vote_text | string | Vote type id description text (Yea, Nay, NV, Absent) |

### text (getBillText)

| Field | Type | Description |
|---|---|---|
| doc_id | integer | Internal document id |
| bill_id | integer | Internal bill id |
| date | date | Document date (if available) |
| type | string | Type of draft text |
| type_id | integer | Internal text type id |
| mime | string | MIME type of document |
| mime_id | integer | Internal mime type id |
| text_size | integer | Size in bytes of the decoded BASE64 document (add 33% for base64) |
| text_hash | string | MD5 hash of the decoded BASE64 document |

doc                  string    BASE64 encoded document

### amendment (getAmendment)

| Field | Type | Description |
|---|---|---|
| amendment_id | integer | Internal amendment id |
| chamber | string | Chamber of origin |
| chamber_id | integer | Internal body id |
| bill_id | integer | Internal bill id |
| adopted | boolean | Flag for adoption (0, 1) |
| date | date | Amendment date (if available) |
| title | string | Amendment title |
| description | string | Amendment description |
| mime | string | MIME type of document |
| mime_id | integer | Internal mime type id |
| amendment_size | integer | Size in bytes of the decoded BASE64 document (add 33% for base64) |
| amendment_hash | string | MD5 hash of the decoded BASE64 document |
| doc | string | BASE64 encoded document |

### supplement (getSupplement)

| Field | Type | Description |
|---|---|---|
| supplement_id | integer | Internal supplement id |
| bill_id | integer | Internal bill id |
| date | date | Supplement date (if available) |
| type | string | Type of supplement |
| type_id | integer | Internal supplement type id |
| title | string | Supplement title |
| description | string | Supplement description |
| mime | string | MIME type of document |
| mime_id | integer | Internal mime type id |
| supplement_size | integer | Size in bytes of the decoded BASE64 document (add 33% for base64) |
| supplement_hash | string | MD5 hash of the decoded BASE64 document |
| doc | string | BASE64 encoded document |

### person (getPerson)

| Field | Type | Description |
|---|---|---|
| people_id | integer | Internal people id |
| person_hash | string | Hash of the personal details to aid change detection |
| state_id | integer | Internal state id |
| party_id | integer | Internal party id |
| party | string | Party text (D, R, I, etc) |
| role_id | integer | Internal role id |
| role | string | Role text (Rep, Sen |
| name | string | Full name |
| first_name | string | First name |
| middle_name | string | Middle name |

last_name             string    Last name suffix                string    Suffix district              string    Legislative district ftm_eid               integer   FollowTheMoney.org EID votesmart_id          integer   VoteSmart.org ID opensecrets_id        string    OpenSecrets.org ID (Congress Only) knowwho_pid           integer   KnowWho.com PID ballotpedia           string    Ballotpedia.org Name committee_sponsor     integer   Committee sponsor flag (0, 1) committee_id          integer   Internal committee id (if committee_sponsor)

### session (getSessionList)

| Field | Type | Description |
|---|---|---|
| session_id | integer | Internal session id |
| state_id | integer | Internal state id |
| year_start | integer | Starting year of session |
| year_end | integer | Ending year of session |
| special | integer | Flag for being a special session (0, 1) |
| prefile | integer | Flag for session being in prefile (0, 1) |
| prior | integer | Flag for session being archived (0, 1) |
| sine_die | integer | Flag for session being adjourned sine die (0, 1) |
| session_name | string | State specific session name |
| session_title | string | Normalized session title with year(s) and Regular/Nth Special Session |

## Static Values

### Bill Types

| Value | Type | Description |
|---|---|---|
| 1 | B | Bill |
| 2 | R | Resolution |
| 3 | CR | Concurrent Resolution |
| 4 | JR | Joint Resolution |
| 5 | JRCA | Joint Resolution Constitutional Amendment |
| 6 | EO | Executive Order |
| 7 | CA | Constitutional Amendment |
| 8 | M | Memorial |
| 9 | CL | Claim |
| 10 | C | Commendation |
| 11 | CSR | Committee Study Request |
| 12 | JM | Joint Memorial |
| 13 | P | Proclamation |
| 14 | SR | Study Request |
| 15 | A | Address |
| 16 | CM | Concurrent Memorial |
| 17 | I | Initiative |
| 18 | PET | Petition |
| 19 | SB | Study Bill |
| 20 | IP | Initiative Petition |
| 21 | RB | Repeal Bill |
| 22 | RM | Remonstration |
| 23 | CB | Committee Bill |

### Event Types

| Value | Description |
|---|---|
| 1 | Hearing |
| 2 | Executive Session |
| 3 | Markup Session |

### MIME Types

| Value | Description | File Extension |
|---|---|---|
| 1 | HTML | .html |
| 2 | PDF | .pdf |
| 3 | WordPerfect | .wpd |
| 4 | MS Word | .doc |
| 5 | Rich Text Format | .rtf |
| 6 | MS Word 2007 | .docx |

### Political Party

| Value | Description |
|---|---|
| 1 | Democrat |
| 2 | Republican |
| 3 | Independent |
| 4 | Green Party |
| 5 | Libertarian |
| 6 | Nonpartisan |

Reasons (Push API Only)

| Value | Flag | Description |
|---|---|---|
| 1 | Newbill | New legislation |
| 2 | StatusChange | Status value changed |
| 3 | Chamber | Bill moved chambers |
| 4 | Complete | Bill completed legislative action (DEPRECATED DO NOT USE) |
| 5 | Title | Title changed |
| 6 | Description | Description changed |
| 7 | CommRefer | Referred or re-referred to committee |
| 8 | CommReport | Reported from committee |
| 9 | SponsorAdd | Sponsor was added |
| 10 | SponsorRemove | Sponsor was removed |
| 11 | SponsorChange | Existing sponsor position / type changed |
| 12 | HistoryAdd | New history steps were added |
| 13 | HistoryRemove | History steps have been removed |
| 14 | HistoryRevised | Prior history steps have been revised |
| 15 | HistoryMajor | History changes included major steps |
| 16 | HistoryMinor | History changes included minor steps |
| 17 | SubjectAdd | Subject was added |
| 18 | SubjectRemove | Subject was removed |
| 19 | SAST | New SAST bill associated |
| 20 | Text | New bill text document |
| 21 | Amendment | New amendment document |
| 22 | Supplement | New supplement document |
| 23 | Vote | New vote record |
| 24 | Calendar | New/updated calendar event |
| 25 | Progress | Progress array has been updated |

### Roles

| Value | Description |
|---|---|
| 1 | Representative / Lower Chamber |
| 2 | Senator / Upper Chamber |
| 3 | Joint Conference |

### SAST Types

| Value | Description |
|---|---|
| 1 | Same As |
| 2 | Similar To |
| 3 | Replaced By |
| 4 | Replaces |
| 5 | Cross-filed |
| 6 | Enabling For |
| 7 | Enabled By |
| 8 | Related |
| 9 | Carry Over |

### Sponsor Types

| Value | Description |
|---|---|
| 0 | Sponsor (Generic / Unspecified) |
| 1 | Primary Sponsor |
| 2 | Co-Sponsor |
| 3 | Joint Sponsor |

### Stance

| Value | Description |
|---|---|
| 0 | Watch |
| 1 | Support |
| 2 | Oppose |

Status / Progress

| Value | Description | Notes |
|---|---|---|
| 0 | N/A | Pre-filed or pre-introduction |
| 1 | Introduced |  |
| 2 | Engrossed |  |
| 3 | Enrolled |  |
| 4 | Passed |  |
| 5 | Vetoed |  |
| 6 | Failed | Limited support based on state |
| 7 | Override | Progress array only |
| 8 | Chaptered | Progress array only |
| 9 | Refer | Progress array only |
| 10 | Report Pass | Progress array only |
| 11 | Report DNP | Progress array only |
| 12 | Draft | Progress array only |

### Supplement Types

| Value | Description |
|---|---|

1            Fiscal Note 2            Analysis 3            Fiscal Note/Analysis 4            Vote Image 5            Local Mandate 6            Corrections Impact 7            Miscellaneous 8            Veto Letter

### Text Types

| Value | Description |
|---|---|
| 1 | Introduced |
| 2 | Committee Substitute |
| 3 | Amended |
| 4 | Engrossed |
| 5 | Enrolled |
| 6 | Chaptered |
| 7 | Fiscal Note |
| 8 | Analysis |
| 9 | Draft |
| 10 | Conference Substitute |
| 11 | Prefiled |
| 12 | Veto Message |
| 13 | Veto Response |
| 14 | Substitute |

### Votes

| Value | Description |
|---|---|
| 1 | Yea |
| 2 | Nay |
| 3 | Not Voting / Abstain |
| 4 | Absent / Excused |
