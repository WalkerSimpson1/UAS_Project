# Publishing format (draft)

Three layers, each pointing to the next: the **dashboard** (what changed this week) links to **policy pages** (one per policy) and **track pages** (one per group of policies). Track and policy pages are Substack posts or pages; the dashboard is `site/` on GitHub Pages. Tracks are loose: a policy can sit in more than one, and the list will change.

The pipeline fills in facts and an AI first draft (`analyses.ai_*`). You write the analysis, publish it on Substack, and paste the URL into `analyses.substack_url` (and `tracks.substack_url` for track pages). The dashboard then links to your page instead of the source.

## Track page (Substack page, updated as policies move)

1. **The question.** One or two sentences: what this track decides and for whom. (*"Who may detect and take down drones, and with what liability?"*)
2. **Where things stand.** Three to five bullets, newest first, each with a date and a link to a policy page.
3. **Policies in this track.** A table: policy, type, stage, last action, link. From the dashboard's policy list filtered by track.
4. **Market evidence.** Two or three numbers or one chart from the contracts data (e.g. counter-UAS obligations by fiscal year, top recipients), each with a one-line "so what".
5. **What to watch.** Dated upcoming items: comment deadlines, markups, conference, effective dates.
6. **Stakeholder map.** The groups that keep showing up across this track's policies and where they stand.

## Policy page (Substack post, one per policy)

Every policy page has the same frame, a header, three core sections and a footer:

- **Header:** title, type, stage, dates, tracks, source links.
- **Summary:** what it does, where it stands, what comes next. Start from `ai_summary`, verify it against the sources, and rewrite it.
- **Stakeholders:** who is affected and where they stand, with evidence (sponsors, commenters, statements). Start from `ai_stakeholders` and check each entry marked "inferred".
- **What affected firms should do:** concrete steps and deadlines. Start from `ai_firm_notes`.
- **Footer:** "Tracked since <first_seen>", plus a changelog of the policy's events.

The middle of the page depends on the policy type:

| Type | Add these sections |
|---|---|
| **Bill** | Bill text in brief (key sections, what changes in law); path to enactment (committee, companion bill, likely vehicle such as the NDAA or appropriations, odds); sponsors and cosponsors by party. |
| **Rulemaking** | Rule stage and timeline (ANPRM → NPRM → final, effective date); what changes for operators and manufacturers (requirements, compliance dates, costs from the RIA); comment record (count, who commented, main themes); legal hooks (statutory authority, CRA window, litigation risk). |
| **Executive action** | What it orders, which agencies, deadlines, and the legal basis; how it interacts with pending bills or rules. |
| **Litigation** | Parties, court, question presented, posture and next date; what each outcome means for firms. |
| **Other** (notices, meetings) | Usually no page. Mention it on the track page if it matters, or mark it `not_relevant`. |

## Weekly dashboard (`site/`, automatic)

- **This week:** every event the pipeline found in the past 7 days (`weekly_changes`). Each row shows the date, policy (linked to your page if published, otherwise the source), change type, one-line summary and tracks.
- **Tracks:** cards with policy counts, each linked to its track page.
- **Contracts:** monthly obligations FY2020–FY2025 from the bulk data, top recipients this fiscal year, and contract actions in the last 60 days (counter-UAS actions are tagged).
- **All tracked policies,** filterable by track.

## Weekly routine (about an hour)

1. Open the dashboard and the review queue (`SELECT * FROM review_queue`).
2. For each new AI draft: mark it `not_relevant`, or set `in_review`, confirm or fix the tracks, and decide whether it gets a page now.
3. Write and publish pages for the important ones. Paste the URLs into `analyses.substack_url`, then set `published`.
4. Update the "Where things stand" and "What to watch" sections of any track page that had changes.
