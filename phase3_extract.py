"""
Phase 3: Extract/enrich email addresses from verified RFP PDFs.
Input:  output/phase2_verified_rfps.csv
Output: output/phase3_rfps_with_emails.csv

Re-downloads PDFs that have no emails and parses more pages to find contacts.
Uses cached PDF downloads and proper email validation from utils.
"""

import pandas as pd

from config import PHASE2_OUTPUT, PHASE3_OUTPUT
from utils import download_pdf_cached, extract_text_from_pdf, extract_emails_from_text, validate_email

EXTENDED_PAGES = 30


def extract_emails_from_url(url):
    try:
        content = download_pdf_cached(url)
        text = extract_text_from_pdf(content, max_pages=EXTENDED_PAGES)
        emails = extract_emails_from_text(text)
        valid = [e for e in emails if validate_email(e)]
        return ", ".join(valid) if valid else ""
    except Exception:
        return ""


def main(input_csv=None):
    input_file = input_csv or PHASE2_OUTPUT

    print(f"Loading {input_file}...")
    try:
        df = pd.read_csv(input_file)
    except FileNotFoundError:
        print(f"Could not find {input_file}")
        return 0

    total = len(df)

    if "emails" not in df.columns:
        df["emails"] = ""

    missing_mask = df["emails"].isna() | (df["emails"].astype(str).str.strip() == "")
    missing_count = missing_mask.sum()

    existing_mask = ~missing_mask
    validated = 0
    for idx in df[existing_mask].index:
        raw = str(df.at[idx, "emails"])
        email_list = [e.strip() for e in raw.split(",")]
        valid = [e for e in email_list if validate_email(e)]
        if valid:
            df.at[idx, "emails"] = ", ".join(valid)
            validated += 1
        else:
            df.at[idx, "emails"] = ""

    print(f"Total RFPs: {total}")
    print(f"Validated existing emails: {validated}")
    print(f"Missing emails (will re-extract): {missing_count}\n")

    enriched = 0
    for idx in df[missing_mask].index:
        url = df.at[idx, "pdf_url"]
        school = str(df.at[idx, "school_name"])[:30]

        if not isinstance(url, str) or not url.startswith("http"):
            continue

        print(f"  Re-extracting: {school} | {str(url)[:60]}...")
        new_emails = extract_emails_from_url(url)

        if new_emails:
            df.at[idx, "emails"] = new_emails
            enriched += 1
            print(f"    Found: {new_emails}")

    df.to_csv(PHASE3_OUTPUT, index=False)

    has_emails = df["emails"].notna() & (df["emails"].astype(str).str.strip() != "")

    print(f"\n{'='*50}")
    print(f"PHASE 3 COMPLETE")
    print(f"  Total RFPs:           {total}")
    print(f"  Newly enriched:       {enriched}")
    print(f"  Total with emails:    {has_emails.sum()}")
    print(f"  Saved to: {PHASE3_OUTPUT}")
    print(f"{'='*50}")

    return int(has_emails.sum())


if __name__ == "__main__":
    main()
