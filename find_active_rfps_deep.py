"""
Deep scan for RFP deadlines using multiple sources:
  Pass 1: Broad regex on ALL PDF pages (not just 20)
  Pass 2: Fetch bid_url HTML pages for deadline info
  Pass 3: Groq LLM extraction for remaining unknowns

Output: output/STRIVED_ACTIVE_RFPS.csv (with all deadlines filled)
"""

import re
import os
import sys
import time
from datetime import datetime

import pandas as pd
import requests
from groq import Groq

sys.path.insert(0, os.path.dirname(__file__))

from config import EXISTING_VERIFIED_CSV, OUTPUT_DIR, GROQ_API_KEY, GROQ_MODEL, REQUEST_TIMEOUT
from utils import download_pdf_cached, extract_text_from_pdf

ACTIVE_OUTPUT = os.path.join(OUTPUT_DIR, "STRIVED_ACTIVE_RFPS.csv")

DATE_PATTERNS = [
    re.compile(
        r"(?:proposal[s]?\s*(?:are\s*)?due|due\s*date|deadline|"
        r"submit(?:tal|ted)?\s*(?:date|deadline|by|no\s*later)|"
        r"response[s]?\s*due|bid[s]?\s*due|closing\s*date|"
        r"submission\s*(?:deadline|date|due)|"
        r"proposals?\s*must\s*be\s*(?:received|submitted)|"
        r"must\s*be\s*(?:received|submitted)\s*(?:by|before|no\s*later)|"
        r"received\s*(?:by|before|no\s*later\s*than)|"
        r"no\s*later\s*than|not\s*later\s*than|"
        r"open(?:ing)?\s*date|close[sd]?\s*(?:on|date))"
        r"\s*[:\-–—\s]*?"
        r"(\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:proposal[s]?\s*(?:are\s*)?due|due\s*date|deadline|"
        r"submit(?:tal|ted)?\s*(?:date|deadline|by)|"
        r"response[s]?\s*due|bid[s]?\s*due|closing\s*date|"
        r"submission\s*(?:deadline|date|due)|"
        r"no\s*later\s*than|received\s*by)"
        r"\s*[:\-–—\s]*?"
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due|deadline|closing|submission|received|submitted)\s*[:\-–—\s]*?"
        r"(\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due|deadline|closing|submission)\s*[:\-–—\s]*?"
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\w+\s+\d{1,2},?\s*\d{4})\s*(?:at|by)\s*\d{1,2}:\d{2}",
        re.IGNORECASE,
    ),
    re.compile(
        r"(\d{1,2}/\d{1,2}/\d{2,4})\s*(?:at|by)\s*\d{1,2}:\d{2}",
        re.IGNORECASE,
    ),
]

DATE_FORMATS = [
    "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    "%m/%d/%Y", "%m/%d/%y",
    "%d %B %Y", "%d %b %Y",
    "%B %d,%Y", "%b %d,%Y",
    "%B%d, %Y", "%B%d,%Y",
]


def parse_date(date_str):
    date_str = re.sub(r"\s+", " ", date_str.strip())
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.year < 2020 or dt.year > 2030:
                continue
            return dt
        except ValueError:
            continue
    return None


def extract_deadline_regex(text):
    for pattern in DATE_PATTERNS:
        matches = pattern.findall(text)
        parsed = []
        for match in matches:
            dt = parse_date(match)
            if dt and dt.year >= 2023:
                parsed.append((dt, match))
        if parsed:
            parsed.sort(key=lambda x: x[0], reverse=True)
            return parsed[0]
    return None, ""


def fetch_html_deadline(url):
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return None, ""
    if url.lower().endswith(".pdf"):
        return None, ""

    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        if "text/html" not in resp.headers.get("content-type", ""):
            return None, ""
        text = resp.text
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text)
        return extract_deadline_regex(text)
    except Exception:
        return None, ""


def llm_extract_deadline(text, client):
    truncated = text[:8000]
    try:
        chat = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Extract the submission deadline/due date from this RFP document. Reply with ONLY the date in MM/DD/YYYY format. If no deadline is found, reply NONE."},
                {"role": "user", "content": truncated},
            ],
            model=GROQ_MODEL,
            temperature=0.0,
            max_tokens=30,
        )
        reply = chat.choices[0].message.content.strip()
        if reply.upper() == "NONE" or len(reply) > 30:
            return None, ""
        dt = parse_date(reply)
        if dt:
            return dt, reply
        date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", reply)
        if date_match:
            dt = parse_date(date_match.group(1))
            if dt:
                return dt, date_match.group(1)
        word_match = re.search(r"(\w+\s+\d{1,2},?\s*\d{4})", reply)
        if word_match:
            dt = parse_date(word_match.group(1))
            if dt:
                return dt, word_match.group(1)
        return None, ""
    except Exception:
        return None, ""


def main():
    today = datetime.now()
    print(f"Today: {today.strftime('%B %d, %Y')}")
    print(f"Loading {EXISTING_VERIFIED_CSV}...\n")

    df = pd.read_csv(EXISTING_VERIFIED_CSV)
    relevant = df[df["is_relevant"] == True].copy()
    total = len(relevant)
    print(f"Relevant RFPs to scan: {total}\n")

    client = Groq(api_key=GROQ_API_KEY)

    records = []
    pdf_texts = {}

    # ── PASS 1: Broad regex on full PDF text ──
    print("=" * 60)
    print("  PASS 1: Full PDF regex scan (all pages)")
    print("=" * 60 + "\n")

    pass1_found = 0
    for i, (idx, row) in enumerate(relevant.iterrows()):
        url = str(row.get("pdf_url", ""))
        school = str(row.get("school_name", "Unknown"))[:40]

        record = {
            "school_name": row.get("school_name", ""),
            "pdf_url": url,
            "bid_url": row.get("bid_url", ""),
            "title": row.get("title", ""),
            "emails": row.get("emails", ""),
            "llm_score": row.get("llm_score", 0),
            "llm_summary": row.get("llm_summary", ""),
            "deadline_raw": "",
            "deadline_parsed": "",
            "deadline_source": "",
            "is_active": False,
            "days_remaining": "",
        }

        if not url.startswith("http"):
            records.append(record)
            continue

        print(f"[{i+1}/{total}] {school}...", end=" ", flush=True)

        try:
            content = download_pdf_cached(url)
            text = extract_text_from_pdf(content, max_pages=999)
            pdf_texts[i] = text

            result = extract_deadline_regex(text)
            if result and result[0]:
                dt, raw = result
                record["deadline_raw"] = raw
                record["deadline_parsed"] = dt.strftime("%Y-%m-%d")
                record["deadline_source"] = "pdf_regex"
                pass1_found += 1
                print(f"FOUND: {raw}")
            else:
                print("no deadline")
        except Exception as e:
            print(f"ERROR: {e}")

        records.append(record)

    print(f"\nPass 1 result: {pass1_found} deadlines found\n")

    # ── PASS 2: Check bid_url HTML pages ──
    missing_idx = [i for i, r in enumerate(records) if not r["deadline_parsed"]]
    print("=" * 60)
    print(f"  PASS 2: Checking bid_url HTML pages ({len(missing_idx)} remaining)")
    print("=" * 60 + "\n")

    pass2_found = 0
    for i in missing_idx:
        r = records[i]
        bid_url = str(r.get("bid_url", ""))
        pdf_url = str(r.get("pdf_url", ""))

        if bid_url == pdf_url or not bid_url.startswith("http") or bid_url.lower().endswith(".pdf"):
            continue

        school = str(r["school_name"])[:40]
        print(f"  Checking bid_url: {school}...", end=" ", flush=True)

        result = fetch_html_deadline(bid_url)
        if result and result[0]:
            dt, raw = result
            records[i]["deadline_raw"] = raw
            records[i]["deadline_parsed"] = dt.strftime("%Y-%m-%d")
            records[i]["deadline_source"] = "bid_url_html"
            pass2_found += 1
            print(f"FOUND: {raw}")
        else:
            print("no deadline")

    print(f"\nPass 2 result: {pass2_found} additional deadlines found\n")

    # ── PASS 3: Groq LLM extraction ──
    missing_idx = [i for i, r in enumerate(records) if not r["deadline_parsed"]]
    print("=" * 60)
    print(f"  PASS 3: Groq LLM extraction ({len(missing_idx)} remaining)")
    print("=" * 60 + "\n")

    pass3_found = 0
    for i in missing_idx:
        r = records[i]
        text = pdf_texts.get(i, "")
        if not text.strip():
            continue

        school = str(r["school_name"])[:40]
        print(f"  LLM extracting: {school}...", end=" ", flush=True)

        dt, raw = llm_extract_deadline(text, client)
        if dt:
            records[i]["deadline_raw"] = raw
            records[i]["deadline_parsed"] = dt.strftime("%Y-%m-%d")
            records[i]["deadline_source"] = "groq_llm"
            pass3_found += 1
            print(f"FOUND: {raw}")
        else:
            print("none")

        time.sleep(2)

    print(f"\nPass 3 result: {pass3_found} additional deadlines found\n")

    # ── Final classification ──
    total_found = 0
    active_count = 0
    for r in records:
        if r["deadline_parsed"]:
            total_found += 1
            dt = datetime.strptime(r["deadline_parsed"], "%Y-%m-%d")
            delta = (dt - today).days
            r["days_remaining"] = delta
            if delta >= 0:
                r["is_active"] = True
                active_count += 1

    results_df = pd.DataFrame(records)
    results_df.to_csv(ACTIVE_OUTPUT, index=False)

    active_df = results_df[results_df["is_active"] == True].sort_values("days_remaining")

    print("=" * 60)
    print("  DEEP SCAN COMPLETE")
    print("=" * 60)
    print(f"  Total scanned:        {total}")
    print(f"  Deadlines found:      {total_found}  (Pass1={pass1_found}, Pass2={pass2_found}, Pass3={pass3_found})")
    print(f"  ACTIVE (still open):  {active_count}")
    print(f"  Expired:              {total_found - active_count}")
    print(f"  No deadline found:    {total - total_found}")
    print("=" * 60)

    if len(active_df) > 0:
        print(f"\n{'='*60}")
        print(f"  ACTIVE RFPs (deadline >= {today.strftime('%Y-%m-%d')})")
        print(f"{'='*60}\n")
        for _, r in active_df.iterrows():
            print(f"  {r['school_name'][:50]}")
            print(f"    Deadline:  {r['deadline_raw']} ({r['days_remaining']} days left)")
            print(f"    Score:     {r['llm_score']}")
            print(f"    Source:    {r['deadline_source']}")
            if r['emails'] and str(r['emails']) != 'nan':
                print(f"    Emails:    {str(r['emails'])[:70]}")
            if r['llm_summary'] and str(r['llm_summary']) != 'nan':
                print(f"    Summary:   {str(r['llm_summary'])[:100]}")
            print(f"    PDF:       {str(r['pdf_url'])[:80]}")
            print()
    else:
        print("\nNo active RFPs found.")

    # ── Also list recently expired (within 30 days) ──
    recent_expired = results_df[
        (results_df["deadline_parsed"] != "") &
        (results_df["is_active"] == False) &
        (results_df["days_remaining"].apply(lambda x: isinstance(x, (int, float)) and x >= -30))
    ].sort_values("days_remaining", ascending=False)

    if len(recent_expired) > 0:
        print(f"{'='*60}")
        print(f"  RECENTLY EXPIRED (within 30 days — may still accept late)")
        print(f"{'='*60}\n")
        for _, r in recent_expired.iterrows():
            print(f"  {r['school_name'][:50]}")
            print(f"    Deadline:  {r['deadline_raw']} ({abs(r['days_remaining'])} days ago)")
            print(f"    Score:     {r['llm_score']}")
            if r['emails'] and str(r['emails']) != 'nan':
                print(f"    Emails:    {str(r['emails'])[:70]}")
            print()

    print(f"Full results saved to: {ACTIVE_OUTPUT}")
    return active_count


if __name__ == "__main__":
    main()
