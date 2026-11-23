"""Federal Register load into regulatory_documents (Postgres; MongoDB version in git history).

    .venv/Scripts/python scripts/federal_register_loader.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]

One search per keyword phrase over a publication_date window (default: the 9
days up to today, so weekly runs overlap), de-duplicated by document_number.
Every page is saved under $RAW_ROOT/federal_register/<run date>/ first.

The API's term search is full text, so "drone" anywhere in a 200-page notice
matches. A document is kept only if a keyword is in its title or abstract
(is_relevant), and Coast Guard safety-zone notices are dropped; the rest are counted
as body_only in the run log. Rows are
keyed "fr:<document_number>": re-running any window is safe, first_seen is
kept and matched_keywords only grows. No API key.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import time

import requests

from pg_common import (RawStore, connect, eastern_midnight, finish_run, jsonb, start_run, to_date,
                       upsert)

API = "https://www.federalregister.gov/api/v1/documents.json"
SOURCE = "federal_register"
WINDOW_DAYS = 9
PER_PAGE = 1000

# Multi-word phrases go in double quotes for an exact-phrase search.
KEYWORDS = ["unmanned aircraft", "unmanned aerial", "drone", "counter-UAS", "UAS", "advanced air mobility"]
RELEVANT_RE = re.compile(
    r"\b(unmanned aircraft|unmanned aerial|drones?|counter-UAS|C-UAS|sUAS|UAS|UAV|advanced air mobility"
    r"|beyond visual line of sight|BVLOS)\b", re.IGNORECASE)
# effective_date is rejected by the API ("field 'effective_date' not valid"); it's effective_on.
FIELDS = [
    "document_number", "title", "abstract", "type", "publication_date", "effective_on",
    "agencies", "agency_names", "docket_ids", "regulation_id_numbers", "cfr_references",
    "comments_close_on", "significant", "html_url", "pdf_url", "json_url",
]


class ApiError(Exception):
    pass


# Not policy: Coast Guard drone-show safety zones, advisory-committee and EIS notices, and rules that only list drones in passing.
NOISE_RE = re.compile(r"^(Safety Zones?|Annual Fireworks|Special Local Regulations?)\b|Federal Advisory Committee"
                      r"|Environmental Impact Statement|Stablecoin|Regulation for Federal Financial Assistance", re.IGNORECASE)


# Defense and trade-control agencies (P23): their documents are kept when the full-text
# search found a drone term anywhere, even if the title and abstract don't mention one.
DEFENSE_AGENCY_RE = re.compile(
    r"^(Defense Department|Defense Acquisition Regulations System|Army Department|Navy Department"
    r"|Air Force Department|Industry and Security Bureau|Foreign Assets Control Office"
    r"|Trade Representative, Office of United States)$", re.IGNORECASE)


def is_relevant(title: str | None, abstract: str | None, agencies=None) -> bool:
    if NOISE_RE.search(title or ""):
        return False
    if RELEVANT_RE.search(f"{title or ''} {abstract or ''}"):
        return True
    return any(DEFENSE_AGENCY_RE.match(a or "") for a in agencies or [])


def get(session, params, pause=0.2, retries=3) -> dict:
    for attempt in range(retries + 1):
        time.sleep(pause)
        try:
            resp = session.get(API, params=params, timeout=120)
        except requests.RequestException:
            if attempt == retries:
                raise
            time.sleep(2 ** attempt * 5)
            continue
        if (resp.status_code == 429 or resp.status_code >= 500) and attempt < retries:
            time.sleep(2 ** attempt * 5)
            continue
        try:
            payload = resp.json()
        except ValueError:
            payload = resp.text
        if resp.status_code != 200 or not isinstance(payload, dict):
            raise ApiError(f"GET {API} -> {resp.status_code}: {str(payload)[:500]}")
        return payload
    raise ApiError("retries exhausted")


def search(session, store, run_date, phrase, start, end) -> tuple[list[dict], dict[str, str], int]:
    """Every page of one phrase. Returns (documents, {document_number: raw_source}, requests)."""
    term = f'"{phrase}"' if " " in phrase else phrase
    slug = re.sub(r"[^a-z0-9]+", "_", phrase.lower()).strip("_")
    base = [("conditions[term]", term), ("conditions[publication_date][gte]", start.isoformat()),
            ("conditions[publication_date][lte]", end.isoformat()), ("per_page", PER_PAGE),
            ("order", "newest")] + [("fields[]", f) for f in FIELDS]
    docs, sources, page = [], {}, 1
    while True:
        payload = get(session, base + [("page", page)])
        results = payload.get("results") or []  # no hits: neither results nor total_pages
        rel = store.write_json(SOURCE, run_date, f"{slug}_p{page}.json", payload, SOURCE, len(results))
        docs.extend(results)
        sources.update({d["document_number"]: rel for d in results})
        if page >= (payload.get("total_pages") or 1):
            return docs, sources, page
        page += 1


def row_from(doc: dict, keywords: list[str], raw_source: str, run_date) -> dict:
    sig = doc.get("significant")
    dockets = doc.get("docket_ids") or []
    rins = doc.get("regulation_id_numbers") or []
    return {
        "id": f"fr:{doc['document_number']}", "source": SOURCE, "document_number": doc["document_number"],
        "title": doc.get("title"), "abstract": doc.get("abstract"), "document_type": doc.get("type"),
        "agency_names": doc.get("agency_names") or [], "docket_id": dockets[0] if dockets else None,
        "docket_ids": dockets, "rin": rins[0] if rins else None,
        "cfr_references": jsonb(doc.get("cfr_references") or []),
        "significant": None if sig is None else bool(sig),
        "publication_date": to_date(doc.get("publication_date")),
        "effective_date": to_date(doc.get("effective_on")),
        "comments_close_at": eastern_midnight(doc.get("comments_close_on")),
        "html_url": doc.get("html_url"), "pdf_url": doc.get("pdf_url"),
        "matched_keywords": keywords, "raw": jsonb(doc), "raw_source": raw_source,
        "first_seen": run_date, "last_seen": run_date,
    }


def ingest(conn, start=None, end=None, run_date=None, run_tag=None, parent_id=None, session=None) -> dict:
    run_date = run_date or dt.date.today()
    end = end or run_date
    start = start or end - dt.timedelta(days=WINDOW_DAYS)
    if start > end:
        raise ValueError(f"window start {start} is after end {end}")
    run_id = f"{SOURCE}-{run_date}"
    rid = start_run(conn, SOURCE, run_id, run_tag, parent_id)
    store, session = RawStore(run_id), session or requests.Session()
    details = {"window_start": str(start), "window_end": str(end), "requests": 0}
    try:
        matched: dict[str, tuple[dict, list[str], str]] = {}
        per_keyword = {}
        for phrase in KEYWORDS:
            docs, sources, n = search(session, store, run_date, phrase, start, end)
            details["requests"] += n
            per_keyword[phrase] = len(docs)
            for d in docs:
                num = d["document_number"]
                _, phrases, _ = matched.setdefault(num, (d, [], sources[num]))
                if phrase not in phrases:
                    phrases.append(phrase)

        kept = {n: v for n, v in matched.items() if is_relevant(v[0].get("title"), v[0].get("abstract"), v[0].get("agency_names"))}
        old_kw = dict(conn.execute("SELECT id, matched_keywords FROM regulatory_documents WHERE id = ANY(%s)",
                                   ([f"fr:{n}" for n in kept],)).fetchall())
        new = 0
        for num, (d, phrases, rel) in kept.items():
            prev = old_kw.get(f"fr:{num}") or []
            keywords = prev + [p for p in phrases if p not in prev]
            new += upsert(conn, "regulatory_documents", row_from(d, keywords, rel, run_date), ("id",),
                          ("first_seen",))
        conn.commit()
        details.update(raw_folder=store.folders.get(SOURCE), upserted=len(kept), new=new,
                       steps={"search": {"returned": len(matched), "kept": len(kept),
                                         "body_only": len(matched) - len(kept), "per_keyword": per_keyword}})
    except Exception as e:
        conn.rollback()
        finish_run(conn, rid, "failed", details, [f"{type(e).__name__}: {e}"])
        raise
    finish_run(conn, rid, "ok", details)
    return {"run_id": run_id, "status": "ok", **details}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--start", type=dt.date.fromisoformat, help=f"default: {WINDOW_DAYS} days before --end")
    p.add_argument("--end", type=dt.date.fromisoformat, help="default: today")
    args = p.parse_args()
    with connect() as conn:
        print(json.dumps(ingest(conn, args.start, args.end), indent=1, default=str))


if __name__ == "__main__":
    main()
