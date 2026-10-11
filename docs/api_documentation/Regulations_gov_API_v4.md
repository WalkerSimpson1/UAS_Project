# Regulations.gov API v4 - Documentation

Source: https://open.gsa.gov/api/regulationsgov/
Captured 2026-10-11. Published by GSA.

> Personal account details from the original capture have been redacted.
> A few long example URLs were clipped by the PDF renderer and are marked `[TRUNCATED]`.

## Overview

When Congress passes laws, federal agencies implement those laws through regulations. These regulations vary in subject, but include everything from ensuring water is safe to drink to setting health care standards. Regulations.gov is the place where users can find and comment on regulations. The APIs allow for users to find creative ways to present regulatory data. To learn more about the program visit the About Us page.

## Getting Started

To begin using this API, you will need to register for an API Key below.

If you want to use commenting API, you MUST use the form below to register for an API key.

Your API key for YOUR_EMAIL has been e-mailed to you. You can use your API key to begin making web service requests immediately.

If you don't receive your API Key via e-mail within a few minutes, please contact us.

For additional support, please contact us. When contacting us, please tell us what API you're accessing and provide the following account details so we can quickly find you:

Account Email: YOUR_EMAIL Account ID: YOUR_ACCOUNT_ID

In order to enable commenting, please contact the Regulations.gov Help desk and provide the first 5 digits of your API key, organization name, organization full address, organization phone number, tax ID and the email address used to sign up for the key. You will be notified when your API key is ready for posting comments. In order to activate your API key, these data elements are required to run an entity validation search.

After registration, you will need to provide this API key in the X-Api-Key HTTP header with every API request.

HTTP Header Name                 Description

X-Api-Key                        API key from api.data.gov. For sample purposes, you can use DEMO_KEY as an API key.

## Terms of Participation

The eRulemaking post Application Programming Interface (API), informally referred to as the Comment API, is provided as a convenience to facilitate the bulk upload of comments from a number of different commenters. The use of the Comment API requires a key, which may be obtained through the open GSA website.

By registering for, receiving and using a key to the Comment API, the key holder agrees to the following terms and conditions:

1. When developing interfaces for commenters who will submit comment language and/or attachments through the Comment API, the key
  - holder will include in the interface:
  1. A link to the same terms of participation and privacy notice that users encounter on the comment form for Regulations.gov, and
  2. A link to the Federal Register notice or other specific document in Regulations.gov for which the key holder is collecting or facilitating
comments to be delivered through the Comment API.

2. The key holder certifies that:
  1. I will only submit comments through the Comment API that it has gathered through lawful means and that, to the best of the key
    - holder's knowledge, represent comments from real persons, and
  2. It has not and will not submit comments of its own creation under fictitious or misappropriated identities or otherwise in violation of
federal law.

3. The API key may be disabled if an API key holder is determined to have violated these Terms of Participation.
## API Description

Regulations.gov offers a GET API for documents, comments, dockets, and a POST API for comments. These endpoints can be used for searching documents, comments, dockets, and posting a comment.

### Searching for documents

You can search for a list of documents based on the criteria passed by using the endpoint /v4/documents. The search operation supports full text keyword searches and filtering based on a number of available parameters.

### Detailed information for a single document

In order to obtain more details about a single document, you can use the endpoint /v4/documents/{documentId}. A document is defined by one of the following types: Proposed Rule, Rule, Supporting & Related, or Other. Each document type has its own set of attributes, which vary based on the Agency posting the document. Another defining characteristic is if the document is part of a Rulemaking or Nonrulemaking Docket.

You can choose to include attachments using include parameter. Attachments are not included by default.

### Searching for comments

You can search for a list of comments based on the criteria passed by using the endpoint /v4/comments. The search operation supports full text keyword searches and filtering based on a number of available parameters.

### Detailed information for a single comment

In order to obtain more details about a single comment, you can use the endpoint /v4/comments/{commentId}. Each comment has its own set of attributes, which vary based on the Agency posting the comment. Another defining characteristic is if the comment is part of a Rulemaking or Nonrulemaking Docket.

You can choose to include attachments using include parameter. Attachments are not included by default.

### Searching for dockets

A docket is an organizational folder containing multiple documents. Dockets can be searched using the endpoint: /v4/dockets.

### Detailed information for a single docket

In order to obtain more details about a single docket, you can use the endpoint /v4/dockets/{docketId}. Each docket has its own set of attributes, which vary based on the Agency posting the docket. Another defining characteristic is if the docket is a Rulemaking or a Nonrulemaking Docket

### Posting a comment

User can post a comment using the endpoint /v4/comments. User can post the comment using one of the following submitter types:

  - Individual
  - Organization
  - Anonymous
If user would like to attach files with their submission, user can get a presigned url for the amazon s3 bucket using the endpoint /v4/file- upload-urls

A submissionKey can be retrieved using /v4/submission-keys endpoint.

submissionType should be set to API.

## Data Limitations

A recent GAO report expressed concerns over whether comment data is fully described to the public, including any limitations. Various aspects of the commenting process can create limitations for certain external users of public comment data and some data fields are managed solely by agencies. For agency-specific commenting practices, contact eRulemaking@gsa.gov. The Open API Specification document has been updated with information on agency configurable fields. For convenience, the data is also provided below in a concise format:

### List of fields that are always publicly viewable on a comment

Here is the list of fields that is always available in the JSON response for a comment:

  - agencyId
  - comment
  - commentOnId
  - docketId
documentId - This field is returned as an Id of the document in the JSON response.

  - documentType
  - postedDate
  - receiveDate
  - restrictReason - if restrictReasonType is set to "Other"
  - restrictReasonType - if the document is restricted
  - reasonWithdrawn - if the comment has been withdrawn
  - title
  - trackingNbr
  - withdrawn
### List of agency configurable comment fields

Agency configured fields can be updated by an agency at any point in time and made accessible or inaccessible in the JSON response of a comment. Here is the list of these fields:

  - city
  - country
  - docAbstract
  - firstName
  - govAgency
  - govAgencyType
  - lastName
  - legacyId
  - organization
  - pageCount
  - postmarkDate
  - stateProvinceRegion
  - subtype
  - zip
### List of fields that are never publicly viewable on a comment

  - originalDocumentId
  - address1
  - address2
  - email
  - phone
  - fax
## Post Comment API Validation

### Common Validations for all comments

commentOnDocumentId, comment and submissionType are required fields. If sendEmailReceipt is true, email field is required. An emoji is not a valid character. If a field has a maximum length requirement, the requirement is applied to both, the number of characters and the number of bytes. submitterType field must be one of these allowed values: ANONYMOUS, INDIVIDUAL, ORGANIZATION. submissionType field must be set to API. Field value for comment must be less than or equals to 5000 characters/bytes. Field value for email must be less than or equals to 100 characters/bytes.

### Individual Comment Validations

firstName and lastName are required fields. Field value for firstName and lastName must be less than or equals to 25 characters/bytes. Field value for city, stateProvinceRegion, phone and country must be less than or equals to 50 characters/bytes. Field value for zip field must have less than or equals to 10 characters/bytes.

### Organization Comment Validations

organization and organizationType are required fields. Field value for organization field must have less than or equals to 120 characters/bytes.

## Examples

### Searching for documents

Here are few example queries for searching documents:

  - Search for term water:
```
https://api.regulations.gov/v4/documents?filter[searchTerm]=water&api_key=DEMO_KEY
```

  - Filter documents by a specific date:
```
https://api.regulations.gov/v4/documents?filter[postedDate]=2020-09-01&api_key=DEMO_KEY
```

  - Filter documents by a date range:
```
https://api.regulations.gov/v4/documents?filter[postedDate][ge]=2020-09-01&filter[postedDate][le]=2020-09-01&api_key=DEMO_KEY
```

  - Search for a documentId:
```
https://api.regulations.gov/v4/documents?filter[searchTerm]=FDA-2009-N-0501-0012&api_key=DEMO_KEY
```

  - Sort documents by posted date in asc:
```
https://api.regulations.gov/v4/documents?sort=postedDate&api_key=DEMO_KEY
```

  - Sort documents by posted date in desc:
```
https://api.regulations.gov/v4/documents?sort=-postedDate&api_key=DEMO_KEY
```

### Detailed information for a single document

There are few ways a user can query documents endpoint to retrieve detailed information for a document.

  - Get document details without attachments:
```
https://api.regulations.gov/v4/documents/FDA-2009-N-0501-0012?api_key=DEMO_KEY
```

  - Get document details with attachments:
```
https://api.regulations.gov/v4/documents/FDA-2009-N-0501-0012?include=attachments&api_key=DEMO_KEY
```

### Searching for comments

Here are few example queries for searching comments:

  - Search for term water:
```
https://api.regulations.gov/v4/comments?filter[searchTerm]=water&api_key=DEMO_KEY
```

  - Filter comments by a specific date:
```
https://api.regulations.gov/v4/comments?filter[postedDate]=2020-09-01&api_key=DEMO_KEY
```

  - Filter comments by a date range:
```
https://api.regulations.gov/v4/comments?filter[postedDate][ge]=2020-09-01&filter[postedDate][le]=2020-09-01&api_key=DEMO_KEY
```

  - Search for a commentId:
```
https://api.regulations.gov/v4/comments?filter[searchTerm]=HHS-OCR-2018-0002-5313&api_key=DEMO_KEY
```

  - Sort comments by posted date in asc:
```
https://api.regulations.gov/v4/comments?sort=postedDate&api_key=DEMO_KEY
```

  - Sort comments by posted date in desc:
```
https://api.regulations.gov/v4/comments?sort=-postedDate&api_key=DEMO_KEY
```

  - Retrieve all comments for a docket where number of comments is less than 5000:
  - Step 1: Get all documents for the docketId FAA-2018-1084:
```
https://api.regulations.gov/v4/documents?filter[docketId]=FAA-2018-1084&api_key=DEMO_KEY
```

It returns two documents, FAA-2018-1084-0001 and FAA-2018-1084-0002. Each document metadata includes an objectId attribute.

  - Step 2: Get all comments for each document using objectId:
```
https://api.regulations.gov/v4/comments?filter[commentOnId]=0900006483a6cba3&api_key=DEMO_KEY
```

The above request returns a list of comments for document FAA-2018-1084-0001.

Note: Step 2 should be repeated for FAA-2018-1084-0002 in the above example.

  - Retrieve all comments for a docket where number of comments is greater than 5000:
  - Step 1: Get all documents for the docketId EOIR-2020-0003:
```
https://api.regulations.gov/v4/documents?filter[docketId]=EOIR-2020-0003&api_key=DEMO_KEY
```

The above query returns five documents where four documents are Supporting & Related Material documents and one document is a Proposed Rule. Response for the above request includes an attribute objectId for each document and its set to 09000064846eebaf for the Proposed Rule, EOIR-2020-0003-0001.

  - Step 2: Get all comments for each document using objectId:
```
https://api.regulations.gov/v4/comments?filter[commentOnId]=09000064846eebaf&api_key=DEMO_KEY
```

The above request returns a list of comments for document EOIR-2020-0003-0001, the only Proposed Rule in the docket. totalElements under meta attribute shows that this document has total 88,061 comments.

Note: Step 2 should be repeated for each document.

  - Step 3: Page through the first set of 5000 documents:
```
https://api.regulations.gov/v4/comments?filter[commentOnId]=09000064846eebaf&page[size]=250&p   [TRUNCATED - see live docs]
```

The first 5000 documents can be retrieved using the query above and paging through the results where N is the page number between 1 and 20. Please note we are sorting the results by lastModifiedDate to ensure we can filter our data by lastModifiedDate later. On the last page of this set, please note the lastModifiedDate of the last document. In our case, EOIR-2020-0003-5548 is the last document on page 20 and the lastModifiedDate attribute of the document is 2020-08-10T15:58:52Z. We will be filtering the data in the next step using this date.

  - Step 4: Page through the next set of 5000 documents:
```
https://api.regulations.gov/v4/comments?filter[commentOnId]=09000064846eebaf&filter[lastModif   [TRUNCATED - see live docs]
```

The next 5000 documents can be retrieved using the query above and paging through the results where N is the page number between 1 and 20.

The lastModifiedDate attribute of the last document in the first set (Step 3) was 2020-08-10T15:58:52Z. This date translates to 2020-08-10 11:58:52 in Eastern time. Running the above query should return all documents where lastModifiedDate is greater than or equal to 2020-08-10T15:58:52Z. Its important to note that we are running a "greater than or equal to" query to ensure we do not miss any documents where last modified date is 2020-08-10T15:58:52Z.

On the last page of this set, please note the lastModifiedDate of the last document and repeat.

Note: Step 4 should be repeated for as many times as needed to retrieve all 88,061 comments.

### Detailed information for a single comment

There are few ways a user can query comments endpoint to retrieve detailed information for a comment:

  - Get comment details without attachments:
```
https://api.regulations.gov/v4/comments/HHS-OCR-2018-0002-5313?api_key=DEMO_KEY
```

  - Get comment details with attachments:
```
https://api.regulations.gov/v4/comments/HHS-OCR-2018-0002-5313?include=attachments&api_key=DEMO_KEY
```

### Searching for dockets

Here are few example queries for searching dockets:

  - Search for term water:
```
https://api.regulations.gov/v4/dockets?filter[searchTerm]=water&api_key=DEMO_KEY
```

  - Search for a docketId:
```
https://api.regulations.gov/v4/dockets?filter[searchTerm]=EPA-HQ-OAR-2003-0129&api_key=DEMO_KEY
```

  - Filter dockets by multiple agencyIds:
```
https://api.regulations.gov/v4/dockets?filter[agencyId]=GSA,EPA&api_key=DEMO_KEY
```

  - Sort dockets by title in asc order:
```
https://api.regulations.gov/v4/dockets?sort=title&api_key=DEMO_KEY
```

  - Sort dockets by title in desc order:
```
https://api.regulations.gov/v4/dockets?sort=-title&api_key=DEMO_KEY
```

### Detailed information for a single docket

To retrieve detailed information on a docket, the following query can be used:

```
https://api.regulations.gov/v4/dockets/EPA-HQ-OAR-2003-0129?api_key=DEMO_KEY
```

### Posting a comment

  - Posting an anonymous comment without attachment:
  - POST https://api.regulations.gov/v4/comments {
  - "data": {
    - "attributes": {
"commentOnDocumentId":"FDA-2009-N-0501-0012", "comment":"test comment", "submissionType":"API",

      - "submitterType":"ANONYMOUS"
},

    - "type":"comments"
  - }
  - }
Note: No submission key is needed for comments with no attached files.

  - Posting a comment with attachment:
    - Step 1: Get a submission key:
    - POST https://api.regulations.gov/v4/submission-keys {
      - "data": {
      - "type":"submission-keys"
      - }
  - }
  - Step 2: Get presigned url for each attachment:
  - POST https://api.regulations.gov/v4/file-upload-urls {
    - "data": {
"type":"file-upload-urls",

      - "attributes": {
"fileName":"test.jpg", "submissionKey":"kex-d31z-fe04",

        - "contentType":"image/jpeg"
      - }
      - }
  - }
  - Step 3: Upload binaries to presigned url
  - Step 4: Submit your comment
  - POST https://api.regulations.gov/v4/comments {
    - "data": {
      - "attributes": {
"commentOnDocumentId":"FDA-2009-N-0501-0012", "comment":"test comment", "submissionType":"API", "submissionKey":"kex-d31z-fe04", "submitterType":"ANONYMOUS",

        - "files":[ "test.jpg" ]
},

      - "type":"comments"
      - }
  - }
  - Posting a comment with agency category:
  - Step 1: Get agency categories for agency:
```
https://api.regulations.gov/v4/agency-categories?filter[acronym]=FDA&api_key=DEMO_KEY
```

  - Step 2: Submit your comment with category:
  - POST https://api.regulations.gov/v4/comments {
      - "data": {
      - "attributes": {
"commentOnDocumentId":"FDA-2009-N-0501-0012", "comment":"test comment", "submissionType":"API", "submitterType":"ANONYMOUS",

        - "category":"Academia - E0007"
},

      - "type":"comments"
      - }
  - }
  - Posting multiple submissions in a single comment:
To post a comment with attachment that carries 5 submissions, user should follow the following steps:

  - Step 1: Get a submission key:
    - POST https://api.regulations.gov/v4/submission-keys {
      - "data": {
        - "type":"submission-keys"
      - }
    - }
    - Step 2: Get presigned url for the attachment with multiple submissions:
    - POST https://api.regulations.gov/v4/file-upload-urls {
      - "data": {
"type":"file-upload-urls",

        - "attributes": {
"fileName":"multipleSubmissions.pdf", "submissionKey":"kex-d31z-fe04",

          - "contentType":"image/jpeg"
        - }
      - }
    - }
    - Step 3: Upload binaries to presigned url
    - Step 4: Submit your comment with
    - POST https://api.regulations.gov/v4/comments {
      - "data": {
        - "attributes": {
"commentOnDocumentId":"FDA-2009-N-0501-0012", "comment":"test comment", "submissionType":"API", "submissionKey":"kex-d31z-fe04", "submitterType":"ANONYMOUS", "files":[ "multipleSubmissions.pdf" ],

        - "numItemsReceived": 5
},

        - "type":"comments"
      - }
    - }
## OpenAPI Specification File

You can view the full details of this API in the OpenAPI Specification file available here: Open API specification file for the Regulations.gov API

## Frequently Asked Questions

I am not seeing all fields returned by v3/documents endpoint in v4/documents endpoint. How do I access this information?

Our v3 API had a single search endpoint which returned information about documents, comments and dockets. To streamline our data, we have split our search into three endpoints:

  - Document Search
  - Comment Search
  - Docket Search
Further, some data that could be retrieved using search in v3 has now been moved under details endpoint. For example, you can retrieve RIN for a docket using /dockets/{docketId} endpoint. The rin is not returned by /documents endpoint anymore.

How do I get document status from the new /documents endpoint?

The new /v4/documents carries a withdrawn field. This is a boolean field. If set to true, the document is withdrawn otherwise it's a posted document.

There are strict pagination limits in v4. How do I retrieve all comments in a docket posted on the same day if the number of comments is greater than 2500?

We have added an example that shows how to retrieve more than 5000 comments on a docket. Please see the example section.

Please note the new parameter lastModifiedDate is in beta and may be removed when we have a permanent bulk download solution available.

I submitted a comment, but I am unable to find it on regs. What happened to my comment?

Comments created via API are not made available in Regulations.gov right away. Agencies need to approve before the newly created comment can be posted out to Regulations.gov.

I am seeing 400 errors from commenting API. What am I doing wrong?

Please make sure you are setting Content-Type to application/vnd.api+json in request header.

What is DEMO_KEY api key?

As indicated by name, DEMO_KEY should only be used for demonstration purposes. We have added this api_key to our examples to make it easier for users to copy/paste the urls. It should not be used for anything more than exploring our APIs.

What is the staging API url?

Users should be able to access our staging API at https://api-staging.regulations.gov. Please use this environment for testing purposes.

I have an API key. How many requests can I make per hour and how do I know I am about to reach my request limit?

Please review https://api.data.gov/docs/rate-limits/ for information on rate limits. Commenting API is restricted to 50 requests per minute with a secondary limit of 500 requests per hour.

Can I request rate limit increases for my keys?

GSA may grant a rate limit increase on the GET keys for an indefinite period. Such requests must establish the need to justify the rate limit increases. Each submission will be reviewed and considered on a case-by-case basis. GSA is unable to increase the rate limits for POST API keys upon requests at this time. However, the current POST API key holders can request one additional key without going through the validation process again.

## API Calls

                4.0   OAS 3.0
Regulations.gov API Public API for Regulations.gov

Servers

```
https://api.regulations.gov/v4 - Production endpoint for Regul                                                        Authorize
```

documents

      - /
      - d
      - o
      - c
      - u
      - m
      - e
      - n
      - t
      - s
  - GET       Li
      - st
      - of
      - d
      - o
      - c
      - u
      - m
      - e
      - nt
      - s
  - This endpoint returns list of documents
  - Parameters
  - Name                             Description
filter[agencyId]                 'Filters results for the agency acronym specified in the value. Example: ''EPA'''

  - string
  - (query)                            filter[agencyId]
filter[commentEndDate]           Filters results relative to the comment end date. The value must be formatted as yyyy-MM-dd .

  - string($date)
  - (query)
Omission of a parameter modifier will match results to the exact date provided, otherwise, one of the parameter modifiers below may be used.

                - ge - greater than or equal
                - le     - less than or equal
                - filter[commentEndDate]
filter[docketId]                 Filters results on the specified docket ID.

  - string
  - (query)                            filter[docketId]
filter[documentType]             Filters results on the specified document type.

  - string
              - Available values : Notice, Rule, Proposed Rule, Supporting & Related Material, Other
  - (query)
                - --
filter[frDocNum]                 Filters results on the specified frDocNum.

  - string
  - (query)                            filter[frDocNum]
filter[searchTerm]               Filters results on the given term.

  - string
  - (query)                            filter[searchTerm]
  - Name                       Description
filter[postedDate]         Filters results relative to the posted date. The value must be formatted as yyyy-MM-dd .

  - string($date)
  - (query)
Omission of a parameter modifier will match results to the exact date provided, otherwise, one of the parameter modifiers below may be used.

            - ge - greater than or equal
            - le    - less than or equal
              - filter[postedDate]
filter[lastModifiedDate]   Filters results relative to the last modified date. The value must be formatted as yyyy-MM-dd

  - string($date)
HH:mm:ss .

  - (query)
Omission of a parameter modifier will match results to the exact date provided, otherwise, one of the parameter modifiers below may be used.

            - ge    - greater than or equal
            - le    - less than or equal
              - filter[lastModifiedDate]
  - filter[subtype]            Filters results on the supplied document subtype
  - string
  - (query)                      filter[subtype]
filter[withinCommentPeriod] Filters results for documents that are open for comment by setting the value to true .

  - boolean
  - (query)
False    is not an acceptable value for this parameter, hence it should be removed when not being used.

              - --
sort                       Sorts the results on the field specified in the value. The default behavior will sort the results in

  - string
ascending order; to sort in descending order, prepend a minus sign to the value.

  - (query)
            - Supported values are commentEndDate , postedDate , lastModifiedDate , documentId and
title . Multiple sort options can be passed in as a comma separated list to sort results by multiple fields.

              - sort
page[number]               Specifies the number for the page of results that will be returned from the query.

  - integer
  - (query)
Acceptable values are numerical between, and including, 1 and 20.

              - page[number]
page[size]                 Specifies the size per page of results that will be returned from the query.

  - integer
  - (query)
Acceptable values are numerical between, and including, 5 and 250.

              - page[size]
  - Responses
Code       Description                                                                              Links

200        A JSON:API document with the a list of documents                                         No links

      - Media type
      - application/vnd.api+json
Controls Accept header.

      - Example Value    Schema
      - {
        - "data": [
          - {
"id": "string", "type": "string",

          - "attributes": {
"agencyId": "string", "commentEndDate": "2026-10-11T15:41:02.063Z", "commentStartDate": "2026-10-11T15:41:02.063Z", "docketId": "string", "documentType": "Notice", "frDocNum": "string", "highlightedContent": "string", "lastModifiedDate": "2026-10-11T15:41:02.063Z", "objectId": "string", "openForComment": true, "postedDate": "string", "subtype": "string", "title": "string",

          - "withdrawn": true
},

          - "links": [
          - {
            - "self": "string"
          - }
          - ]
          - }
],

            - {
400        Validation error                                                                         No links

      - Media type
      - application/vnd.api+json
      - Example Value    Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
403        API key is missing or invalid                                                            No links

      - Media type
      - application/vnd.api+json
      - Example Value    Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
      - /
      - d
      - o
      - c
      - u
      - m
      - e
      - n
      - t
      - s
      - /
      - {
      - d
      - o
      - c
      - u
      - m
      - e
      - n
      - t
      - I
      - d
      - }
      - G
      - et
      - d
      - et
  - GET       ai
      - le
      - d
      - in
      - fo
      - r
      - m
      - at
      - io
      - n
      - fo
      - r
      - s
      - p
      - e
      - ci
      - fi
      - e
      - d
      - d
      - o
      - c
      - u
      - m
      - e
      - nt
      - Id
Gets the detailed information of a specific document with the passed documentId.

  - Parameters
  - Name                      Description
  - documentId * required     ID of document to return
  - string
  - (path)                     documentId
  - include                   resources to include
  - string
            - Example : attachments
  - (query)
            - attachments
  - Responses
Code       Description                                                                         Links

200        successful operation                                                                No links

      - Media type
      - application/vnd.api+json
Controls Accept header.

      - Example Value      Schema
      - {
"id": "string", "type": "string",

        - "attributes": {
"address1": "string", "address2": "string", "agencyId": "string", "city": "string", "category": "string", "comment": "string", "country": "string", "docAbstract": "string", "docketId": "string", "documentType": "Notice", "email": "string", "fax": "string", "field1": "string", "field2": "string",

        - "fileFormats": [
          - {
"fileUrl": "string", "format": "string",

          - "size": 0
          - }
], "firstName": "string", "govAgency": "string", "govAgencyType": "string",

          - l               i
400        Validation error                                                                    No links

      - Media type
      - application/vnd.api+json
      - Example Value      Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
403        API key is missing or invalid                                                       No links

      - Media type
      - application/vnd.api+json
      - Example Value      Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
404        Document not found                                                                  No links

      - Media type
      - application/vnd.api+json
      - Example Value      Schema
      - {
        - "errors": [
        - {
Code            Description                                                                                                    Links "status": 0, "title": "string",

              - "detail": "string"
            - }
          - ]
        - }
comments

      - /
      - c
      - o
      - m
      - m
      - e
      - n
      - t
      - s
  - GET       Li
      - st
      - of
      - c
      - o
      - m
      - m
      - e
      - nt
      - s
  - This endpoint returns list of comments
  - Parameters
  - Name                              Description
filter[agencyId]                  'Filters results for the agency acronym specified in the value. Example: ''EPA'''

  - string
  - (query)                            filter[agencyId]
filter[searchTerm]                Filters results on the given term.

  - string
  - (query)                            filter[searchTerm]
filter[postedDate]                Filters results relative to the posted date. The value must be formatted as yyyy-MM-dd .

  - string($date)
  - (query)
Omission of a parameter modifier will match results to the exact date provided, otherwise, one of the parameter modifiers below may be used.

                - ge - greater than or equal
                - le   - less than or equal
                - filter[postedDate]
filter[lastModifiedDate] Filters results relative to the last modified date. The value must be formatted as yyyy-MM-dd

  - string($date)
HH:mm:ss .

  - (query)
Omission of a parameter modifier will match results to the exact date provided, otherwise, one of the parameter modifiers below may be used.

                - ge   - greater than or equal
                - le   - less than or equal
                - filter[lastModifiedDate]
  - Name                         Description
  - filter[commentOnId]          Filters results on the supplied commentOnId
  - string
  - (query)                       filter[commentOnId]
sort                         Sorts the results on the field specified in the value. The default behavior will sort the results in

  - string
ascending order; to sort in descending order, prepend a minus sign to the value.

  - (query)
The only supported values are postedDate , lastModifiedDate and documentId . Multiple sort options can be passed in as a comma separated list to sort results by multiple fields.

              - sort
page[number]                 Specifies the number for the page of results that will be returned from the query.

  - integer
  - (query)
Acceptable values are numerical between, and including, 1 and 20.

              - page[number]
page[size]                   Specifies the size per page of results that will be returned from the query.

  - integer
  - (query)
Acceptable values are numerical between, and including, 5 and 250.

              - page[size]
  - Responses
Code         Description                                                                                                        Links

200          A JSON:API document with the a list of comments                                                                    No links

      - Media type
        - application/vnd.api+json
Controls Accept header.

      - Example Value     Schema
        - {
        - "data": [
          - {
"id": "string", "type": "string",

          - "attributes": {
"agencyId": "string", "documentType": "Notice", "highlightedContent": "string", "lastModifiedDate": "2026-10-11T15:41:02.075Z", "objectId": "string", "postedDate": "string", "title": "string",

            - "withdrawn": true
},

          - "links": [
            - {
            - "self": "string"
            - }
          - ]
          - }
],

        - "meta": {
"hasNextPage": true, "hasPreviousPage": true, "numberOfElements": 0, "pageNumber": 0, "pageSize": 0,

            - l l
Code            Description                                                                                         Links

400             Validation error                                                                                    No links

        - Media type
        - application/vnd.api+json
        - Example Value   Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
403             API key is missing or invalid                                                                       No links

        - Media type
        - application/vnd.api+json
        - Example Value   Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
      - /
      - c
      - o
      - m
      - m
      - e
      - n
      - t
      - s
      - C
      - r
      - e
      - at
  - POST       e
      - s
      - a
      - n
      - e
      - w
      - c
      - o
      - m
      - m
      - e
      - nt
.

  - Parameters
  - No parameters
Request body required                                                                          application/vnd.api+json

  - A JSON object containing comment information
  - Example Value        Schema
  - {
"type": "string",

    - "attributes": {
"category": "string", "comment": "string", "commentOnDocumentId": "string", "email": "string",

      - "files": [
      - "string"
], "numItemsReceived": 0, "sendEmailReceipt": true, "submissionKey": "string", "submissionType": "string", "city": "string", "country": "string", "firstName": "string", "lastName": "string", "phone": "string", "stateProvinceRegion": "string", "submitterType": "Anonymous",

      - "zip": "string"
    - }
  - }
  - Responses
Code           Description                                                                           Links

201            Created                                                                               No links

        - Media type
        - application/vnd.api+json
Controls Accept header.

        - Example Value    Schema
        - {
"id": "string", "type": "string",

          - "attributes": {
"numItemsReceived": 0, "receiveDate": "string", "category": "string", "comment": "string", "commentOnDocumentId": "string", "email": "string",

          - "files": [
            - "string"
], "sendEmailReceipt": true, "submissionKey": "string", "submissionType": "string", "city": "string", "country": "string", "firstName": "string", "lastName": "string", "phone": "string", "stateProvinceRegion": "string", "submitterType": "Anonymous",

          - "zip": "string"
          - }
        - }
400            Validation error                                                                      No links

        - Media type
        - application/vnd.api+json
        - Example Value    Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
403            API key is missing or invalid                                                         No links

        - Media type
        - application/vnd.api+json
        - Example Value    Schema
Code           Description                                                                                Links

        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
404            Document not found                                                                         No links

        - Media type
        - application/vnd.api+json
        - Example Value   Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
      - /
      - c
      - o
      - m
      - m
      - e
      - n
      - t
      - s
      - /
      - {
      - c
      - o
      - m
      - m
      - e
      - n
      - t
      - I
      - d
      - }
      - G
      - et
      - d
      - et
      - ai
  - GET       le
      - d
      - in
      - fo
      - r
      - m
      - at
      - io
      - n
      - fo
      - r
      - s
      - p
      - e
      - ci
      - fi
      - e
      - d
      - c
      - o
      - m
      - m
      - e
      - nt
      - Id
Gets the detailed information of a specific comment with the passed commentId.

  - Parameters
  - Name                      Description
  - commentId * required      ID of comment to return
  - string
  - (path)                      commentId
  - include                   resources to include
  - string
            - Example : attachments
  - (query)
            - attachments
  - Responses
Code        Description                                                                         Links

200         successful operation                                                                No links

      - Media type
        - application/vnd.api+json
Controls Accept header.

      - Example Value     Schema
      - {
"id": "string", "type": "string",

        - "attributes": {
"address1": "string", "address2": "string", "agencyId": "string", "city": "string", "category": "string", "comment": "string", "country": "string", "docAbstract": "string", "docketId": "string", "documentType": "Notice", "email": "string", "fax": "string", "field1": "string", "field2": "string",

          - "fileFormats": [
          - {
"fileUrl": "string", "format": "string",

            - "size": 0
          - }
], "firstName": "string", "govAgency": "string", "govAgencyType": "string",

          - l               i
400         Validation error                                                                    No links

      - Media type
        - application/vnd.api+json
      - Example Value     Schema
      - {
        - "errors": [
          - {
"status": 0, "title": "string",

          - "detail": "string"
          - }
        - ]
      - }
403         API key is missing or invalid                                                       No links

Code            Description                                                                                         Links

        - Media type
        - application/vnd.api+json
        - Example Value     Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
404             Document not found                                                                                  No links

        - Media type
        - application/vnd.api+json
        - Example Value     Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
dockets

      - /
      - d
      - o
      - c
      - k
      - e
      - t
      - s
      - L
      - i
  - GET       s
      - t
      - o
      - f
      - d
      - o
      - c
      - k
      - e
      - t
      - s
  - This endpoint returns list of dockets
  - Parameters
  - Name                            Description
filter[agencyId]                'Filters results for the agency acronym specified in the value. Example: ''EPA'''

  - string
  - (query)                          filter[agencyId]
  - Name                         Description
filter[searchTerm]           Filters results on the given term.

  - string
  - (query)                       filter[searchTerm]
filter[lastModifiedDate] Filters results relative to the last modified date. The value must be formatted as yyyy-MM-dd

  - string($date)
HH:mm:ss .

  - (query)
Omission of a parameter modifier will match results to the exact date provided, otherwise, one of the parameter modifiers below may be used.

              - ge - greater than or equal
              - le   - less than or equal
              - filter[lastModifiedDate]
filter[docketType]           Filters results on the specified docket type.

  - string
              - Available values : Rulemaking, Nonrulemaking
  - (query)
              - --
sort                         Sorts the results on the field specified in the value. The default behavior will sort the results in

  - string
ascending order; to sort in descending order, prepend a minus sign to the value.

  - (query)
The only supported values are title , docketId and lastModifiedDate . Multiple sort options can be passed in as a comma separated list to sort results by multiple fields.

              - sort
page[number]                 Specifies the number for the page of results that will be returned from the query.

  - integer
  - (query)
Acceptable values are numerical between, and including, 1 and 20.

              - page[number]
page[size]                   Specifies the size per page of results that will be returned from the query.

  - integer
  - (query)
Acceptable values are numerical between, and including, 5 and 250.

              - page[size]
  - Responses
Code         Description                                                                                                        Links

200          A JSON:API document with the a list of dockets                                                                     No links

      - Media type
        - application/vnd.api+json
Controls Accept header.

      - Example Value     Schema
        - {
        - "data": [
          - {
"id": "string",

Code       Description                                                                              Links "type": "string",

          - "attributes": {
"agencyId": "string", "docketType": "Rulemaking", "highlightedContent": "string", "lastModifiedDate": "2026-10-11T15:41:02.089Z", "objectId": "string",

          - "title": "string"
},

          - "links": [
          - {
            - "self": "string"
          - }
          - ]
          - }
],

        - "meta": {
"hasNextPage": true, "hasPreviousPage": true, "numberOfElements": 0, "pageNumber": 0, "pageSize": 0, "totalElements": 0,

          - "totalPages": 0
400        Validation error                                                                         No links

      - Media type
      - application/vnd.api+json
      - Example Value    Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
403        API key is missing or invalid                                                            No links

      - Media type
      - application/vnd.api+json
      - Example Value    Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
      - /
      - d
      - o
      - c
      - k
      - e
      - t
      - s
      - /
      - {
      - d
      - o
      - c
      - k
      - e
      - t
      - I
      - d
      - }
      - G
      - et
      - d
      - et
      - ai
  - GET       le
      - d
      - in
      - fo
      - r
      - m
      - at
      - io
      - n
      - fo
      - r
      - s
      - p
      - e
      - ci
      - fi
      - e
      - d
      - d
      - o
      - c
      - k
      - et
      - Id
Gets the detailed information of a specific docket with the passed docketId.

  - Parameters
  - Name                     Description
  - docketId * required      ID of docket to return
  - string
  - (path)                        docketId
  - Responses
Code            Description                                                                                 Links

200             successful operation                                                                        No links

        - Media type
        - application/vnd.api+json
Controls Accept header.

Code       Description                                                                               Links

      - Example Value   Schema
      - {
"id": "string", "type": "string",

        - "attributes": {
"agencyId": "string", "category": "string", "dkAbstract": "string", "docketType": "Rulemaking", "effectiveDate": "2026-10-11T15:41:02.092Z", "field1": "string", "field2": "string", "generic": "string",

          - "keywords": [
          - "string"
], "legacyId": "string", "modifyDate": "2026-10-11T15:41:02.092Z", "objectId": "string", "organization": "string", "petitionNbr": "string", "program": "string", "rin": "string", "shortTitle": "string", "subType": "string", "subType2": "string",

          - "title": "string"
},

        - "links": [
          - {
400        Validation error                                                                          No links

      - Media type
      - application/vnd.api+json
      - Example Value   Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
403        API key is missing or invalid                                                             No links

      - Media type
      - application/vnd.api+json
      - Example Value   Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
404        Document not found                                                                        No links

      - Media type
      - application/vnd.api+json
      - Example Value   Schema
      - {
        - "errors": [
        - {
"status": 0, "title": "string",

          - "detail": "string"
        - }
        - ]
      - }
comment submission utilities

      - /
      - a
      - g
      - e
      - n
      - c
      - y
      - -
      - c
      - a
      - t
      - e
      - g
      - o
      - r
      - i
      - e
      - s
      - R
      - e
  - GET       t
      - u
      - r
      - n
      - s
      - a
      - li
      - s
      - t
      - o
      - f
      - c
      - a
      - t
      - e
      - g
      - o
      - ri
      - e
      - s
  - This endpoint returns list of categories
  - Parameters
  - Name                              Description
filter[acronym] * required        'Filters results for the agency acronym specified in the value. Example: ''EPA'''

  - string
  - (query)                             filter[acronym]
  - Responses
Code             Description                                                                                          Links

200              List of agency categories                                                                            No links

        - Media type
          - application/vnd.api+json
Controls Accept header.

        - Example Value    Schema
Code         Description                                                                       Links

        - {
        - "data": [
          - {
"default": true, "acronym": "string",

          - "categories": "string"
          - }
        - ]
        - }
400          Validation error                                                                  No links

      - Media type
        - application/vnd.api+json
      - Example Value   Schema
        - {
        - "errors": [
          - {
"status": 0, "title": "string",

          - "detail": "string"
          - }
        - ]
        - }
403          API key is missing or invalid                                                     No links

      - Media type
        - application/vnd.api+json
      - Example Value   Schema
        - {
        - "errors": [
          - {
"status": 0, "title": "string",

          - "detail": "string"
          - }
        - ]
        - }
      - /
      - s
      - u
      - b
      - m
      - i
      - s
      - s
      - i
      - o
      - n
      - -
      - k
      - e
      - y
      - s
      - C
      - r
      - e
      - at
  - POST    e
      - s
      - th
      - e
      - u
      - ni
      - q
      - u
      - e
      - s
      - u
      - b
      - m
      - is
      - si
      - o
      - n
      - k
      - e
      - y
  - Parameters
  - No parameters
Request body                                                                                    application/vnd.api+json

  - Example Value        Schema
  - {
    - "data": {
    - "type": "submissionKeys"
    - }
  - }
  - Responses
Code            Description                                                                                          Links

201             Created                                                                                              No links

        - Media type
        - application/vnd.api+json
Controls Accept header.

        - Example Value    Schema
        - {
          - "data": {
"id": "string", "type": "submission-keys",

          - "links": {
            - "self": "string"
          - }
          - }
        - }
403             API key is missing or invalid                                                                        No links

        - Media type
        - application/vnd.api+json
        - Example Value    Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
      - /
      - f
      - i
      - l
      - e
      - -
      - u
      - p
      - l
      - o
      - a
      - d
      - -
      - u
      - r
      - l
      - s
      - C
      - r
      - e
      - a
      - t
      - e
  - POST
      - s
      - a
      - p
      - r
      - e
      - si
      - g
      - n
      - e
      - d
      - u
      - rl
      - t
      - o
      - u
      - p
      - l
      - o
      - a
      - d
      - fil
      - e
  - Returns a pre-signed URL to upload a file to the S3 bucket
  - Parameters
  - No parameters
Request body required                                                                                    application/vnd.api+json

  - Example Value        Schema
  - {
    - "data": {
"type": "file-upload-urls",

    - "attributes": {
"submissionKey": "string", "fileName": "string",

      - "contentType": "string"
    - }
    - }
  - }
  - Responses
Code              Description                                                                                                 Links

201               Created                                                                                                     No links

        - Media type
          - application/vnd.api+json
Code            Description                                                                              Links

Controls Accept header.

        - Example Value    Schema
        - {
          - "data": {
"id": "string", "type": "file-upload-urls",

          - "attributes": {
"submissionKey": "string", "fileName": "string",

            - "contentType": "string"
          - }
          - }
        - }
400             Validation error                                                                         No links

        - Media type
        - application/vnd.api+json
        - Example Value    Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
403             API key is missing or invalid                                                            No links

        - Media type
        - application/vnd.api+json
        - Example Value    Schema
        - {
          - "errors": [
          - {
"status": 0, "title": "string",

            - "detail": "string"
          - }
          - ]
        - }
Schemas

  - DocumentFindAllResponse                     {
      - description:             A JSON:API document with a list of resources
      - data
                  - [...]
      - meta
                - FindAllResponseMetadata        {...}
  - }
  - CommentFindAllResponse               {
      - description:       A JSON:API document with a list of resources
      - data
                - [...]
      - meta
            - FindAllResponseMetadata        {...}
  - }
  - DocketFindAllResponse            {
      - description:       A JSON:API document with a list of resources
      - data
                - [...]
      - meta
            - FindAllResponseMetadata        {...}
  - }
  - DocumentFindAllItem         {
      - description:       A JSON:API document which represents a single document in the list
      - id
                - [...]
      - type
                - [...]
      - attributes
            - Document          {...}
      - links
                - [...]
  - }
  - CommentFindAllItem         {
      - description:       A JSON:API document which represents a single document in the list
      - id
                - [...]
      - type
                - [...]
      - attributes
            - Comment          {...}
      - links
                - [...]
  - }
  - DocketFindAllItem     {
      - description:       A JSON:API document which represents a single document in the list
      - id
                - [...]
      - type
                - [...]
      - attributes
            - Docket         {...}
      - links
                - [...]
  - }
  - AttachmentFindAllItem        {
      - description:       A JSON:API document which represents a single document in the list
      - id
              - [...]
      - type
              - [...]
      - attributes
            - Attachment     {...}
      - links
              - [...]
  - }
  - FindAllResponseMetadata          {
      - description:       A JSON:API document
      - hasNextPage
              - [...]
      - hasPreviousPage
              - [...]
      - numberOfElements
              - [...]
      - pageNumber
              - [...]
      - pageSize
              - [...]
      - totalElements
              - [...]
      - totalPages
              - [...]
      - firstPage
              - [...]
      - lastPage
              - [...]
  - }
  - DocumentFindOneResponse              {
      - description:       A JSON:API document which represents a single document
      - id
              - [...]
      - type
              - [...]
      - attributes
            - DocumentDetail        {...}
      - relationships
            - Relationship     {...}
      - links
              - [...]
      - included
              - [...]
  - }
  - CommentFindOneResponse               {
      - description:       A JSON:API document which represents a single document
      - id
              - [...]
      - type
              - [...]
      - attributes
            - CommentDetail         {...}
      - relationships
              - [...]
      - links
              - [...]
      - included
              - [...]
  - }
  - DocketFindOneResponse                    {
      - description:             A JSON:API document which represents a single document
      - id
                  - [...]
      - type
                  - [...]
      - attributes
                - DocketDetail       {...}
      - links
                  - [...]
  - }
  - Relationship            {
      - description:             A single relationship object
      - data
                - RelationshipToAttachment              [...]
      - links
                - RelationshipLinks          {...}
  - }
  - RelationshipToAttachment                     [
An array of attachment objects as relationship resources.

  - RelationshipToAttachment                     {
      - type
                  - [...]
      - id
                  - [...]
    - }]
  - RelationshipLinks             {
      - description:             Relationship links to other related resources ( attachments )
      - self
                - Link       [...]
      - related
                - Link       [...]
  - }
  - SelfLink            {
      - description:             Link to self
      - self
                - Link       [...]
  - }
  - Link            string($uri-reference)
  - uniqueItems: true
A string containing the link URL.

  - JSONResourcePostRequestObject                    {
description:               A JSON:API document which represents a single document being posted

      - type
                  - [...]
      - attributes
                  - {...}
  - }
  - JSONResourcePostResponseObject                       {
      - description:               A JSON:API document which represents the response from post
      - id
                  - [...]
      - type
                  - [...]
      - attributes
                - CommentPostResponse          {...}
  - }
  - JSONError            {
      - description:               A JSON:API document
      - errors
                  - [...]
  - }
  - DocumentType                string
  - type of document. This field is always returned in JSON response
  - Enum:
      - Array [ 5 ]
  - DocketType               string
  - the type of docket
  - Enum:
      - Array [ 2 ]
  - SubmitterType              string
  - the submitter type
  - Enum:
      - Array [ 3 ]
  - FileFormat           {
      - fileUrl
                  - [...]
      - format
                  - [...]
      - size
                  - [...]
  - }
  - Attachment              {
      - agencyNote
              - [...]
      - authors
              - [...]
      - docAbstract
              - [...]
      - docOrder
              - [...]
      - fileFormats
              - [...]
      - modifyDate
              - [...]
      - publication
              - [...]
      - restrictReason
              - [...]
      - restrictReasonType
              - [...]
      - title
              - [...]
  - }
  - Document            {
      - agencyId
              - [...]
      - commentEndDate
              - [...]
      - commentStartDate
              - [...]
      - docketId
              - [...]
      - documentType
              - DocumentType   [...]
      - frDocNum
              - [...]
      - highlightedContent
              - [...]
      - lastModifiedDate
              - [...]
      - objectId
              - [...]
      - openForComment
              - [...]
      - postedDate
              - [...]
      - subtype
              - [...]
      - title
              - [...]
      - withdrawn
              - [...]
  - }
  - Comment             {
      - agencyId
              - [...]
      - documentType
              - DocumentType   [...]
      - highlightedContent
              - [...]
      - lastModifiedDate
              - [...]
      - objectId
              - [...]
      - postedDate
              - [...]
      - title
              - [...]
      - withdrawn
              - [...]
  - }
  - Docket           {
      - agencyId
                - [...]
      - docketType
                - DocketType     [...]
      - highlightedContent
                - [...]
      - lastModifiedDate
                - [...]
      - objectId
                - [...]
      - title
                - [...]
  - }
  - BasicDetailModel            {
      - address1
                - [...]
      - address2
                - [...]
      - agencyId
                - [...]
      - city
                - [...]
      - category
                - [...]
      - comment
                - [...]
      - country
                - [...]
      - docAbstract
                - [...]
      - docketId
                - [...]
      - documentType
                - DocumentType      [...]
      - email
                - [...]
      - fax
                - [...]
      - field1
                - [...]
      - field2
                - [...]
      - fileFormats
                - [...]
      - firstName
                - [...]
      - govAgency
                - [...]
      - govAgencyType
                - [...]
      - lastName
                - [...]
      - legacyId
                - [...]
      - modifyDate
                - [...]
      - objectId
                - [...]
      - openForComment
                - [...]
      - organization
                - [...]
      - originalDocumentId
                - [...]
      - pageCount
                - [...]
      - phone
                - [...]
      - postedDate
                - [...]
      - postmarkDate
                - [...]
      - reasonWithdrawn
                - [...]
      - receiveDate
                - [...]
      - restrictReason
                - [...]
      - restrictReasonType
                - [...]
      - stateProvinceRegion
                - [...]
      - subtype
                - [...]
      - title
                - [...]
      - trackingNbr
                - [...]
      - withdrawn
                - [...]
      - zip
                - [...]
  - }
  - DocumentDetail            {
      - address1
                - [...]
      - address2
                - [...]
      - agencyId
                - [...]
      - city
                - [...]
      - category
                - [...]
      - comment
                - [...]
      - country
                - [...]
      - docAbstract
                - [...]
      - docketId
                - [...]
      - documentType
              - DocumentType   [...]
      - email
                - [...]
      - fax
                - [...]
      - field1
                - [...]
      - field2
                - [...]
      - fileFormats
                - [...]
      - firstName
                - [...]
      - govAgency
                - [...]
      - govAgencyType
                - [...]
      - lastName
                - [...]
      - legacyId
                - [...]
      - modifyDate
                - [...]
      - objectId
                - [...]
      - openForComment
                - [...]
      - organization
                - [...]
      - originalDocumentId
                - [...]
      - pageCount
                - [...]
      - phone
                - [...]
      - postedDate
                - [...]
      - postmarkDate
                - [...]
      - reasonWithdrawn
                - [...]
      - receiveDate
                - [...]
      - restrictReason
                - [...]
      - restrictReasonType
                - [...]
      - stateProvinceRegion
                - [...]
      - subtype
                - [...]
      - title
                - [...]
      - trackingNbr
                - [...]
      - withdrawn
                - [...]
      - zip
                - [...]
      - additionalRins
                - [...]
      - allowLateComments
                - [...]
      - authorDate
                - [...]
      - authors
                - [...]
      - cfrPart
                - [...]
      - commentEndDate
                - [...]
      - commentStartDate
                - [...]
      - effectiveDate
                - [...]
      - exhibitLocation
                - [...]
      - exhibitType
                - [...]
      - frDocNum
                - [...]
      - frVolNum
                - [...]
      - implementationDate
                - [...]
      - media
                - [...]
      - ombApproval
                - [...]
      - paperLength
                - [...]
      - paperWidth
                - [...]
      - regWriterInstruction
                - [...]
              - [   ]
      - sourceCitation
              - [...]
      - startEndPage
              - [...]
      - subject
              - [...]
      - topics
              - [...]
  - }
  - CommentDetail            {
      - address1
              - [...]
      - address2
              - [...]
      - agencyId
              - [...]
      - city
              - [...]
      - category
              - [...]
      - comment
              - [...]
      - country
              - [...]
      - docAbstract
              - [...]
      - docketId
              - [...]
      - documentType
              - DocumentType   [...]
      - email
              - [...]
      - fax
              - [...]
      - field1
              - [...]
      - field2
              - [...]
      - fileFormats
              - [...]
      - firstName
              - [...]
      - govAgency
              - [...]
      - govAgencyType
              - [...]
      - lastName
              - [...]
      - legacyId
              - [...]
      - modifyDate
              - [...]
      - objectId
              - [...]
      - openForComment
              - [...]
      - organization
              - [...]
      - originalDocumentId
              - [...]
      - pageCount
              - [...]
      - phone
              - [...]
      - postedDate
              - [...]
      - postmarkDate
              - [...]
      - reasonWithdrawn
              - [...]
      - receiveDate
              - [...]
      - restrictReason
              - [...]
      - restrictReasonType
              - [...]
      - stateProvinceRegion
              - [...]
      - subtype
              - [...]
      - title
              - [...]
      - trackingNbr
              - [...]
      - withdrawn
              - [...]
      - zip
              - [...]
      - commentOnDocumentId
              - [...]
      - duplicateComments
              - [...]
  - }
  - DocketDetail           {
      - agencyId
                - [...]
      - category
                - [...]
      - dkAbstract
                - [...]
      - docketType
              - DocketType   [...]
      - effectiveDate
                - [...]
      - field1
                - [...]
      - field2
                - [...]
      - generic
                - [...]
      - keywords
                - [...]
      - legacyId
                - [...]
      - modifyDate
                - [...]
      - objectId
                - [...]
      - organization
                - [...]
      - petitionNbr
                - [...]
      - program
                - [...]
      - rin
                - [...]
      - shortTitle
                - [...]
      - subType
                - [...]
      - subType2
                - [...]
      - title
                - [...]
  - }
  - BaseCommentPayload                {
      - category
                - [...]
      - comment*
                - [...]
      - commentOnDocumentId*
                - [...]
      - email
                - [...]
      - files
                - [...]
      - numItemsReceived
                - [...]
      - sendEmailReceipt
                - [...]
      - submissionKey
                - [...]
      - submissionType*
                - [...]
  - }
  - IndividualComment             {
      - allOf ->
              - BaseCommentPayload   {...}
                - {...}
  - }
  - OrganizationComment               {
      - allOf ->
              - BaseCommentPayload   {...}
                - {...}
  - }
    - AnonymousComment         {
      - allOf ->
            - BaseCommentPayload         {...}
              - {...}
    - }
    - CommentPostResponse           {
      - allOf ->
              - {...}
              - {...}
    - }
    - Error        {
      - status
              - [...]
      - title
              - [...]
      - detail
              - [...]
    - }
## Contact Us

If you have any questions or would like to provide feedback, please contact Regulations.gov Help desk.

open.gsa.gov / GSA Office of Office of Enterprise Data & Privacy Management An official website of the U.S. General Services Administration

About us Accessibility support FOIA requests No FEAR Act data Privacy policy Contact us Office of the Inspector General Performance reports
