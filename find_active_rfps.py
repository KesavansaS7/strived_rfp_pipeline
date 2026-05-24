"""
Scan STRIVED_ALL_VERIFIED_RFPS_WITH_EMAILS.csv, extract deadline dates
from each PDF, and identify active (still-open) RFPs.

Output: output/STRIVED_ACTIVE_RFPS.csv
"""

import re
import sys
import os
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from config import EXISTING_VERIFIED_CSV, OUTPUT_DIR
from utils import download_pdf_cached, extract_text_from_pdf

ACTIVE_OUTPUT = os.path.join(OUTPUT_DIR, "STRIVED_ACTIVE_RFPS.csv")

DATE_PATTERNS = [
    re.compile(
        r"(?:proposal[s]?\s*(?:are\s*)?due|due\s*date|deadline|submit(?:tal)?\s*(?:date|deadline)|"
        r"response[s]?\s*due|bid[s]?\s*due|closing\s*date|submission\s*deadline|"
        r"proposals?\s*must\s*be\s*(?:received|submitted))"
        r"\s*[:\-–—]?\s*"
        r"(\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:proposal[s]?\s*(?:are\s*)?due|due\s*date|deadline|submit(?:tal)?\s*(?:date|deadline)|"
        r"response[s]?\s*due|bid[s]?\s*due|closing\s*date|submission\s*deadline)"
        r"\s*[:\-–—]?\s*"
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:proposal[s]?\s*(?:are\s*)?due|due\s*date|deadline|submit(?:tal)?\s*(?:date|deadline)|"
        r"response[s]?\s*due|bid[s]?\s*due|closing\s*date|submission\s*deadline)"
        r"\s*[:\-–—]?\s*"
        r"(\d{1,2}\s+\w+\s+\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due|deadline|closing|submission)[:\s\-–—]*?"
        r"(\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
]

DATE_FORMATS = [
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%m/%d/%Y",
    "%m/%d/%y",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d,%Y",
]


def parse_date(date_str):
    date_str = date_str.strip().replace("  ", " ")
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.year < 2020 or dt.year > 2030:
                continue
            return dt
        except ValueError:
            continue
    return None


def extract_deadline(text):
    for pattern in DATE_PATTERNS:
        matches = pattern.findall(text)
        for match in matches:
            dt = parse_date(match)
            if dt:
                return dt, match
    return None, ""


def main():
    today = datetime.now()
    print(f"Today: {today.strftime('%B %d, %Y')}")
    print(f"Loading {EXISTING_VERIFIED_CSV}...")

    df = pd.read_csv(EXISTING_VERIFIED_CSV)
    relevant = df[df["is_relevant"] == True].copy()
    print(f"Relevant RFPs to scan: {len(relevant)}\n")

    results = []
    found_dates = 0
    active_count = 0
    errors = 0

    for i, (idx, row) in enumerate(relevant.iterrows()):
        url = str(row.get("pdf_url", ""))
        school = str(row.get("school_name", "Unknown"))[:40]

        if not url.startswith("http"):
            continue

        print(f"[{i+1}/{len(relevant)}] {school}...", end=" ")

        try:
            content = download_pdf_cached(url)
            text = extract_text_from_pdf(content, max_pages=20)

            deadline_dt, deadline_raw = extract_deadline(text)

            record = {
                "school_name": row.get("school_name", ""),
                "pdf_url": url,
                "title": row.get("title", ""),
                "emails": row.get("emails", ""),
                "llm_score": row.get("llm_score", 0),
                "llm_summary": row.get("llm_summary", ""),
                "deadline_raw": deadline_raw,
                "deadline_parsed": deadline_dt.strftime("%Y-%m-%d") if deadline_dt else "",
                "is_active": False,
                "days_remaining": "",
            }

            if deadline_dt:
                found_dates += 1
                delta = (deadline_dt - today).days
                record["days_remaining"] = delta

                if delta >= 0:
                    record["is_active"] = True
                    active_count += 1
                    print(f"ACTIVE (due {deadline_raw}, {delta} days left)")
                else:
                    print(f"expired ({deadline_raw}, {abs(delta)} days ago)")
            else:
                print("no deadline found")

            results.append(record)

        except Exception as e:
            print(f"ERROR: {e}")
            errors += 1

    results_df = pd.DataFrame(results)

    results_df.to_csv(ACTIVE_OUTPUT, index=False)

    active_df = results_df[results_df["is_active"] == True].sort_values("days_remaining")

    print(f"\n{'='*60}")
    print(f"SCAN COMPLETE")
    print(f"  Total scanned:        {len(results)}")
    print(f"  Deadlines found:      {found_dates}")
    print(f"  ACTIVE (still open):  {active_count}")
    print(f"  Expired:              {found_dates - active_count}")
    print(f"  No deadline found:    {len(results) - found_dates}")
    print(f"  Errors:               {errors}")
    print(f"{'='*60}")

    if len(active_df) > 0:
        print(f"\n{'='*60}")
        print(f"  ACTIVE RFPs (deadline >= today)")
        print(f"{'='*60}\n")
        for _, r in active_df.iterrows():
            print(f"  {r['school_name'][:45]}")
            print(f"    Deadline:  {r['deadline_raw']} ({r['days_remaining']} days left)")
            print(f"    Score:     {r['llm_score']}")
            print(f"    Emails:    {str(r['emails'])[:60]}")
            if r['llm_summary']:
                print(f"    Summary:   {str(r['llm_summary'])[:100]}")
            print()
    else:
        print("\nNo active RFPs found with parseable deadlines.")
        print("Note: Many RFPs don't have clear deadline text in the PDF.")

    print(f"Full results saved to: {ACTIVE_OUTPUT}")

    return active_count


if __name__ == "__main__":
    main()
