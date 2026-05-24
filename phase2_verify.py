"""
Phase 2: Verify documents are actual RFPs + score Strived relevance.
Input:  output/phase1_rfp_candidates.csv
Output: output/phase2_verified_rfps.csv

3-layer pipeline:
  Layer 0: RFP verification (mandatory/supporting/negative terms)
  Layer 1: Keyword filter (software + education terms)
  Layer 2: Groq LLM classification
"""

import os
import re
import time

import pandas as pd
from groq import Groq

from config import (
    PHASE1_OUTPUT, PHASE2_OUTPUT, GROQ_API_KEY, GROQ_MODEL, GROQ_SLEEP,
    PAGES_TO_PARSE, EMAIL_REGEX, LLM_SYSTEM_PROMPT,
    RFP_MANDATORY_TERMS, RFP_SUPPORTING_TERMS, RFP_NEGATIVE_TERMS,
    SOFTWARE_TERMS, EDUCATION_TERMS, PROJECT_TERMS, STRIVED_NEGATIVE_TERMS,
)
from utils import download_pdf_cached, extract_text_from_pdf, extract_emails_from_text, recover_school_name


def is_actual_rfp(text):
    text_lower = text.lower()

    for neg in RFP_NEGATIVE_TERMS:
        if neg in text_lower:
            return False, f"Not an RFP: contains '{neg}'"

    has_mandatory = any(term in text_lower for term in RFP_MANDATORY_TERMS)
    support_count = sum(1 for term in RFP_SUPPORTING_TERMS if term in text_lower)

    if has_mandatory and support_count >= 2:
        return True, f"Confirmed RFP (mandatory=True, support={support_count})"
    if support_count >= 5:
        return True, f"Likely RFP (high support count={support_count})"
    return False, f"Not an RFP (mandatory={has_mandatory}, support={support_count})"


def keyword_filter(text):
    text_lower = text.lower()

    for neg in STRIVED_NEGATIVE_TERMS:
        if neg in text_lower:
            return False, [], f"Rejected: negative term '{neg}'"

    sw_hits = [t for t in SOFTWARE_TERMS if t in text_lower]
    ed_hits = [t for t in EDUCATION_TERMS if t in text_lower]
    pj_hits = [t for t in PROJECT_TERMS if t in text_lower]

    all_hits = sw_hits + ed_hits + pj_hits

    if sw_hits and ed_hits:
        return True, all_hits, "Passed: software + education keywords found"
    return False, all_hits, f"Failed: software={len(sw_hits)}, education={len(ed_hits)}"


def llm_classify(text, client):
    truncated = text[:15000]

    chat = client.chat.completions.create(
        messages=[
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user", "content": f"Analyze this RFP:\n\n{truncated}"},
        ],
        model=GROQ_MODEL,
        temperature=0.0,
        max_tokens=100,
    )

    reply = chat.choices[0].message.content.strip()

    result = {
        "llm_software": "", "llm_education": "", "llm_strived_fit": "",
        "llm_score": 0, "llm_summary": "",
    }

    for line in reply.split("\n"):
        line = line.strip()
        if line.startswith("SOFTWARE:"):
            result["llm_software"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("EDUCATION:"):
            result["llm_education"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("STRIVED_FIT:"):
            result["llm_strived_fit"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("SCORE:"):
            try:
                result["llm_score"] = int(line.split(":", 1)[1].strip())
            except ValueError:
                result["llm_score"] = 0
        elif line.startswith("SUMMARY:"):
            result["llm_summary"] = line.split(":", 1)[1].strip()

    return result


def process_pdf(row, client):
    school_name = row.get("school_name", "Unknown")
    url = row.get("pdf_url", "")
    title = row.get("title", "")

    result = {
        "school_name": school_name, "pdf_url": url, "emails": "",
        "is_rfp": False, "rfp_reason": "",
        "keyword_pass": False, "keyword_hits": "",
        "llm_software": "", "llm_education": "", "llm_strived_fit": "",
        "llm_score": 0, "llm_summary": "",
        "is_relevant": False, "status": "",
    }

    if not isinstance(url, str) or not url.startswith("http"):
        result["status"] = "Invalid URL"
        return result

    try:
        content = download_pdf_cached(url)
        text = extract_text_from_pdf(content)

        if not text.strip():
            result["status"] = "PDF is blank or unreadable"
            return result

        emails = extract_emails_from_text(text)
        result["emails"] = ", ".join(emails)

        is_rfp, rfp_reason = is_actual_rfp(text)
        result["is_rfp"] = is_rfp
        result["rfp_reason"] = rfp_reason
        if not is_rfp:
            result["status"] = f"RFP Check: {rfp_reason}"
            return result

        passed, hits, reason = keyword_filter(text)
        result["keyword_pass"] = passed
        result["keyword_hits"] = ", ".join(hits)
        if not passed:
            result["status"] = f"Keyword Filter: {reason}"
            return result

        llm_result = llm_classify(text, client)
        result.update(llm_result)

        result["school_name"] = recover_school_name(school_name, result["llm_summary"], title)

        if result["llm_software"] == "YES" and result["llm_education"] == "YES" and result["llm_score"] >= 6:
            result["is_relevant"] = True

        result["status"] = "Success"

    except Exception as e:
        result["status"] = f"Error: {e}"

    return result


def main(input_csv=None):
    input_file = input_csv or PHASE1_OUTPUT

    print(f"Loading {input_file}...")
    try:
        df = pd.read_csv(input_file)
    except FileNotFoundError:
        print(f"Could not find {input_file}")
        return 0

    client = Groq(api_key=GROQ_API_KEY)
    rows = df.to_dict("records")
    total = len(rows)

    already_done = set()
    all_results = []
    if os.path.exists(PHASE2_OUTPUT):
        existing = pd.read_csv(PHASE2_OUTPUT)
        already_done = set(existing["pdf_url"].dropna().tolist())
        all_results = existing.to_dict("records")
        print(f"Resuming: {len(already_done)} already processed")

    skipped = 0
    print(f"Processing {total} PDFs...\n")

    for i, row in enumerate(rows):
        url = row.get("pdf_url", "")
        if url in already_done:
            skipped += 1
            continue

        school = str(row.get("school_name", ""))[:30]
        print(f"[{i+1}/{total}] {school} | {str(url)[:60]}")

        result = process_pdf(row, client)
        all_results.append(result)
        already_done.add(url)

        tag = ""
        if result["is_relevant"]:
            tag = "RELEVANT"
        elif not result["is_rfp"]:
            tag = "Not an RFP"
        elif result["keyword_pass"]:
            tag = f"LLM rejected (score={result['llm_score']})"
        else:
            tag = "Keyword rejected"

        email_count = len(result["emails"].split(",")) if result["emails"] else 0
        print(f"        -> {tag} | Emails: {email_count} | {str(result['status'])[:60]}")

        if result["keyword_pass"]:
            time.sleep(GROQ_SLEEP)

        if len(all_results) % 10 == 0:
            pd.DataFrame(all_results).to_csv(PHASE2_OUTPUT, index=False)

    out_df = pd.DataFrame(all_results)
    out_df.to_csv(PHASE2_OUTPUT, index=False)

    relevant_count = int(out_df["is_relevant"].sum())
    rfp_count = int(out_df["is_rfp"].sum())

    print(f"\n{'='*50}")
    print(f"PHASE 2 COMPLETE")
    print(f"  Total PDFs:          {total}")
    print(f"  Skipped (resumed):   {skipped}")
    print(f"  Confirmed RFPs:      {rfp_count}")
    print(f"  RELEVANT to Strived: {relevant_count}")
    print(f"  Saved to: {PHASE2_OUTPUT}")
    print(f"{'='*50}")

    return relevant_count


if __name__ == "__main__":
    main()
