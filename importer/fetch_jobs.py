#!/usr/bin/env python3
"""
Fetches the current 'Ask HN: Who is Hiring?' thread from the HN Firebase API
and parses job postings into structured fields.

Usage:
    python fetch_jobs.py               # parse 20 jobs → jobs_preview.json
    python fetch_jobs.py --limit 50    # parse more
    python fetch_jobs.py --limit 0     # parse all (can be 500+, takes a few minutes)
    python fetch_jobs.py --output out.json
"""

import argparse
import json
import os
import re
import time
from datetime import datetime, timezone


import psycopg2
import requests
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from psycopg2.extras import execute_values

load_dotenv()

# ---------------------------------------------------------------------------
# DATABASE STUFF
# ---------------------------------------------------------------------------


def get_db_conn():
    return psycopg2.connect(
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


# ---------------------------------------------------------------------------
#
# ---------------------------------------------------------------------------



def save_jobs(conn, jobs):
    sql = """
        INSERT INTO jobs (
            source,
            source_id,
            title,
            company,
            location,
            is_remote,
            salary_min,
            salary_max,
            description,
            url,
            raw_text,
            posted_at
        )
        VALUES %s
        ON CONFLICT (source, source_id) DO UPDATE SET
            title = EXCLUDED.title,
            company = EXCLUDED.company,
            location = EXCLUDED.location,
            is_remote = EXCLUDED.is_remote,
            salary_min = EXCLUDED.salary_min,
            salary_max = EXCLUDED.salary_max,
            description = EXCLUDED.description,
            url = EXCLUDED.url,
            raw_text = EXCLUDED.raw_text,
            posted_at = EXCLUDED.posted_at,
            scraped_at = NOW()
    """

    rows = []

    for job in jobs:
        rows.append(
            (
                job["source"],
                job["source_id"],
                job["title"],
                job["company"],
                job["location"],
                job["is_remote"],
                job["salary_min"],
                job["salary_max"],
                job["description"],
                job["url"],
                job["raw_text"],
                job["posted_at"],
            )
        )

    with conn.cursor() as cur:
        execute_values(cur, sql, rows)

    conn.commit()

    return len(rows)

HN_API = "https://hacker-news.firebaseio.com/v0"
WHOISHIRING_USER = "whoishiring"
HIRING_THREAD_RE = re.compile(r"Ask HN: Who is Hiring\?", re.IGNORECASE)

# Token classifiers for pipe-separated header lines.
# HN posters don't follow a fixed field order, so we classify by content.
_SALARY_RE = re.compile(r'[\$£€]|\b\d+[kK]\b|\bsalary\b', re.IGNORECASE)
_JOB_TYPE_RE = re.compile(
    r'^\s*(full[- ]?time|part[- ]?time|contract|freelance|permanent|intern(ship)?)\s*$',
    re.IGNORECASE,
)
_REMOTE_RE = re.compile(r'\b(remote|wfh|work[- ]from[- ]home|distributed)\b', re.IGNORECASE)
_ONSITE_RE = re.compile(r'\b(on[- ]?site|in[- ]?office|in[- ]?person|hybrid)\b', re.IGNORECASE)
# Signals that a REMOTE-matching token also carries geographic info worth keeping
_GEO_CHAR_RE = re.compile(r'[/,\-\(]|\b(only|timezone|est|pst|cst|mst|gmt|utc)\b', re.IGNORECASE)
# Bare domain token (no spaces, ends with known TLD) — not a location
_DOMAIN_RE = re.compile(r'^[\w][\w.-]*\.[a-zA-Z]{2,6}$')
_TITLE_RE = re.compile(
    r'\b(engineers?|developers?|\bdev\b|manager|designer|scientist|analyst|architect|lead|'
    r'director|\bvp\b|\bcto\b|\bcoo\b|head of|software|frontend|backend|full[- ]?stack|'
    r'machine learning|\bml\b|\bai\b|devops|\bsre\b|\bswe\b|product|\bqa\b|tester|'
    r'researcher|recruiter|founding|firmware|mechanical|hardware|principal|staff|'
    r'programmer|hacker)\b',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# HN API helpers
# ---------------------------------------------------------------------------

def get_item(item_id: int) -> dict | None:
    resp = requests.get(f"{HN_API}/item/{item_id}.json", timeout=10)
    resp.raise_for_status()
    return resp.json()


def find_hiring_thread() -> dict:
    """
    Walk the whoishiring account's recent submissions to find the latest
    'Ask HN: Who is Hiring?' thread.

    The account also posts 'Who wants to be hired?' and 'Freelancer?' threads,
    so we filter by title. Checking the last 20 submissions is enough to find
    the current month's thread even if the account posted other things recently.
    """
    user = requests.get(f"{HN_API}/user/{WHOISHIRING_USER}.json", timeout=10).json()
    for item_id in user["submitted"][:20]:
        item = get_item(item_id)
        if item and HIRING_THREAD_RE.search(item.get("title", "")):
            return item
    raise RuntimeError("No 'Ask HN: Who is Hiring?' thread found in last 20 submissions")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """
    Convert HN comment HTML to plain text.

    HN wraps paragraph breaks in <p> tags and encodes special characters as
    HTML entities (e.g. &#x27; for apostrophe). BeautifulSoup handles both:
    it unescapes entities and lets us insert newlines before <p> tags so the
    plain-text output preserves paragraph structure.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("p"):
        tag.insert_before("\n\n")
    return soup.get_text().strip()


def extract_urls(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [a["href"] for a in soup.find_all("a", href=True)]


def parse_job(item: dict) -> dict:
    """
    Parse a single HN comment into a structured job posting.

    Heuristic strategy
    ------------------
    Most HN job posts use a pipe-separated first line, but field order varies:
        Acme Corp | Senior Engineer | Remote | $150k–$180k
        Prior Labs | Berlin / NYC | ONSITE | Full-time | Multiple Roles
        ALBERT | REMOTE ALMOST ANYWHERE | Hiring principal engineers...

    We classify each token by content rather than by position:
        - salary tokens ($, £, "100k")           → skipped
        - job-type tokens ("Full-time", etc.)     → skipped
        - pure work-arrangement tokens (REMOTE, ONSITE, HYBRID ≤25 chars) → skipped
        - remote tokens with geographic qualifiers ("Remote - US only")   → location
        - tokens matching job-role keywords       → title (first match)
        - remaining short tokens (≤80 chars)      → location, then title

    When the first line has no pipes all three fields stay None — we'd rather
    leave a field empty than populate it with the wrong value.
    """
    html_text = item.get("text") or ""
    raw_text = html_to_text(html_text)
    urls = extract_urls(html_text)

    company = title = location = None

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    if lines and "|" in lines[0]:
        parts = [p.strip() for p in lines[0].split("|")]
        company = parts[0] or None

        for part in parts[1:]:
            if not part or part.startswith("http"):
                continue
            if _DOMAIN_RE.match(part):
                continue
            if _SALARY_RE.search(part):
                continue
            if _JOB_TYPE_RE.match(part):
                continue
            if _ONSITE_RE.search(part) and len(part) <= 25:
                continue
            if _REMOTE_RE.search(part):
                # Keep as location only when the token also has geographic qualifiers
                # (e.g. "Remote - US only"), not for pure phrases like "REMOTE ANYWHERE"
                if _GEO_CHAR_RE.search(part) and location is None and len(part) <= 50:
                    location = part
                continue
            # Assign to the first slot whose content matches
            if title is None and _TITLE_RE.search(part):
                title = part
            elif location is None and len(part) <= 80:
                location = part
            elif title is None and len(part) <= 80:
                title = part

    is_remote = bool(re.search(r"\bremote\b", raw_text, re.IGNORECASE))

    posted_at = (
        datetime.fromtimestamp(item["time"], tz=timezone.utc).isoformat()
        if item.get("time")
        else None
    )

    return {
        "source": "hackernews",
        "source_id": str(item["id"]),
        "title": title,
        "company": company,
        "location": location,
        "is_remote": is_remote,
        "salary_min": None,
        "salary_max": None,
        "description": raw_text,
        "url": urls[0] if urls else None,
        "raw_text": raw_text,
        "posted_at": posted_at,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Parse HN 'Who is Hiring?' job postings")
    parser.add_argument(
        "--limit", type=int, default=20,
        help="Max top-level comments to fetch (0 = all)"
    )
    parser.add_argument(
        "--output", default="jobs_preview.json",
        help="Path to write parsed results (default: jobs_preview.json)"
    )
    args = parser.parse_args()

    print("Finding latest 'Ask HN: Who is Hiring?' thread...")
    thread = find_hiring_thread()
    kids = thread.get("kids", [])
    print(f'Found: "{thread["title"]}"')
    print(f"Thread ID: {thread['id']} | {len(kids)} top-level comments")

    if args.limit > 0:
        kids = kids[: args.limit]
    print(f"Fetching {len(kids)} postings...\n")

    jobs = []
    skipped = 0
    for i, kid_id in enumerate(kids, 1):
        print(f"  [{i:>3}/{len(kids)}] fetching {kid_id} ...", end="\r")
        try:
            item = get_item(kid_id)
        except requests.RequestException as e:
            print(f"\n  Warning: failed to fetch {kid_id}: {e}")
            skipped += 1
            continue

        if not item or item.get("dead") or item.get("deleted") or not item.get("text"):
            skipped += 1
            continue

        jobs.append(parse_job(item))
        time.sleep(0.05)  # stay polite to the API

    print(f"\nParsed {len(jobs)} job postings  ({skipped} skipped — dead/deleted/no text)")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2, ensure_ascii=False)
    print(f"Written to {args.output}")

    conn = get_db_conn()
    try:
        saved = save_jobs(conn, jobs)
        print(f"Saved {saved} jobs to PostgreSQL")
    finally:
        conn.close()

    # Print a quick sample so you can sanity-check without opening the file
    print("\n--- Sample: first 5 parsed postings ---")
    for job in jobs[:5]:
        print(f"  company:   {job['company']}")
        print(f"  title:     {job['title']}")
        print(f"  location:  {job['location']}")
        print(f"  remote:    {job['is_remote']}")
        print(f"  url:       {job['url']}")
        print(f"  posted_at: {job['posted_at']}")
        print()


if __name__ == "__main__":
    main()
