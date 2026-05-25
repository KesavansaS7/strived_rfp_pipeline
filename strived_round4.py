"""
strived_round4.py — Round 4 RFP Collection

150+ new DDGS queries focusing on:
  - More county/district-specific searches (highest hit rate in Round 3)
  - State-level education procurement
  - Specific software categories
  - Different document formats and terminology

Merges relevant results into STRIVED_COMPLETE_TEXAS.csv.
"""

import concurrent.futures
import io
import os
import random
import re
import sys
import time

import pandas as pd
import requests
from dotenv import load_dotenv
from ddgs import DDGS
from groq import Groq
from pypdf import PdfReader

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_SLEEP = 5

DDGS_MAX_RESULTS = 500
DDGS_SLEEP = (4.0, 7.0)
DDGS_ERROR_SLEEP = 30
DDGS_MAX_RETRIES = 3

PDF_VERIFY_WORKERS = 15

OUTPUT_DIR = "output"
MAIN_CSV = os.path.join(OUTPUT_DIR, "STRIVED_COMPLETE_TEXAS.csv")
ROUND4_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ROUND4.csv")

EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

SOFTWARE_TERMS = [
    "software", "saas", "platform", "cloud", "web-based", "web based",
    "technology", "data platform", "data analytics", "application",
    "system integration", "api", "dashboard", "portal", "digital",
    "information system", "database", "online platform",
]

EDUCATION_TERMS = [
    "student data", "student information", "learning management",
    "assessment", "attendance", "curriculum", "edtech", "ed-tech",
    "instructional technology", "data-driven", "progress monitoring",
    "academic performance", "classroom", "k-12", "student achievement",
    "learning", "school district", "educator", "instruction",
    "student", "teacher", "school",
]

NEGATIVE_TERMS = [
    "construction", "roofing", "hvac", "plumbing", "electrical wiring",
    "janitorial", "custodial", "food service", "cafeteria",
    "transportation", "school bus", "mowing", "landscaping",
    "demolition", "athletic equipment", "uniforms", "furniture",
    "copier", "insurance", "audit services", "legal services",
    "architectural", "paving", "flooring", "painting contractor",
    "fencing", "playground equipment", "asbestos", "fire alarm",
]


def keyword_filter(text):
    text_lower = text.lower()
    sw = sum(1 for t in SOFTWARE_TERMS if t in text_lower)
    ed = sum(1 for t in EDUCATION_TERMS if t in text_lower)
    neg = sum(1 for t in NEGATIVE_TERMS if t in text_lower)
    if sw >= 1 and ed >= 1 and neg <= sw + ed:
        return True, f"Passed (sw={sw}, ed={ed}, neg={neg})"
    if neg > 0 and neg > sw + ed:
        return False, f"Negative dominant (neg={neg} > sw+ed={sw + ed})"
    return False, f"Failed (sw={sw}, ed={ed})"


SYSTEM_PROMPT = """You are a procurement analyst for Strived.io — an AI-powered K-12 education data platform that consolidates student data (attendance, assessments, grades, edtech tools) and provides actionable classroom insights for educators.

Analyze this RFP and reply in EXACTLY this format (no extra text):
SOFTWARE: YES or NO
EDUCATION: YES or NO
STRIVED_FIT: YES or NO
SCORE: 1-10
SUMMARY: one line describing what the RFP is requesting"""


def llm_classify(text, client):
    truncated = text[:15000]
    chat = client.chat.completions.create(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Analyze this RFP:\n\n{truncated}"},
        ],
        model=GROQ_MODEL,
        temperature=0.0,
        max_tokens=100,
    )
    reply = chat.choices[0].message.content.strip()
    result = {"llm_software": "", "llm_education": "", "llm_strived_fit": "", "llm_score": 0, "llm_summary": ""}
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


QUERIES = [
    # --- More county school districts (high hit rate in Round 3) ---
    '"Maricopa County" schools RFP technology OR software filetype:pdf',
    '"Riverside County" schools RFP technology OR software filetype:pdf',
    '"San Bernardino County" schools RFP technology OR software filetype:pdf',
    '"Alameda County" schools RFP technology OR software filetype:pdf',
    '"Contra Costa County" schools RFP technology OR software filetype:pdf',
    '"Kern County" schools RFP technology OR software filetype:pdf',
    '"Ventura County" schools RFP technology OR software filetype:pdf',
    '"Fresno County" schools RFP technology OR software filetype:pdf',
    '"Stanislaus County" schools RFP technology OR software filetype:pdf',
    '"Tulare County" schools RFP technology OR software filetype:pdf',
    '"Escambia County" schools RFP technology OR software filetype:pdf',
    '"Duval County" schools RFP technology OR software filetype:pdf',
    '"Alachua County" schools RFP technology OR software filetype:pdf',
    '"Leon County" schools RFP technology OR software filetype:pdf',
    '"Manatee County" schools RFP technology OR software filetype:pdf',
    '"Collier County" schools RFP technology OR software filetype:pdf',
    '"Marion County" schools RFP technology OR software filetype:pdf',
    '"Forsyth County" schools RFP technology OR software filetype:pdf',
    '"Clayton County" schools RFP technology OR software filetype:pdf',
    '"Muscogee County" schools RFP technology OR software filetype:pdf',
    '"Richmond County" schools RFP technology OR software filetype:pdf',
    '"Chatham County" schools RFP technology OR software filetype:pdf',
    '"Bibb County" schools RFP technology OR software filetype:pdf',
    '"Jefferson County" schools RFP technology OR software filetype:pdf',
    '"Shelby County" schools RFP technology OR software filetype:pdf',
    '"Davidson County" schools RFP technology OR software filetype:pdf',
    '"Rutherford County" schools RFP technology OR software filetype:pdf',
    '"Williamson County" schools RFP technology OR software filetype:pdf',
    '"Summit County" schools RFP technology OR software filetype:pdf',
    '"Cuyahoga County" schools RFP technology OR software filetype:pdf',

    # --- Specific software product searches ---
    'school district RFP "early warning" student dropout filetype:pdf',
    'school district RFP "benchmark assessment" OR "diagnostic assessment" filetype:pdf',
    'school district RFP "reading intervention" software filetype:pdf',
    'school district RFP "math intervention" software filetype:pdf',
    'school district RFP "teacher evaluation" software OR platform filetype:pdf',
    'school district RFP "substitute management" software filetype:pdf',
    'school district RFP "online registration" student enrollment filetype:pdf',
    'school district RFP "report card" software OR system filetype:pdf',
    'school district RFP "discipline management" OR "discipline tracking" filetype:pdf',
    'school district RFP "wellness" OR "mental health" screening software filetype:pdf',
    'school district RFP "threat assessment" software filetype:pdf',
    'school district RFP "career technical education" OR "CTE" software filetype:pdf',
    'school district RFP "work-based learning" tracking software filetype:pdf',
    'school district RFP "dual enrollment" management software filetype:pdf',
    'school district RFP "school improvement plan" software OR platform filetype:pdf',

    # --- More large districts not searched ---
    'Aldine ISD RFP "student data" OR technology filetype:pdf',
    'Spring ISD RFP software OR technology filetype:pdf',
    'Tomball ISD RFP software OR technology filetype:pdf',
    'Lamar CISD RFP software OR technology filetype:pdf',
    'Galveston ISD RFP software OR technology filetype:pdf',
    'Brazosport ISD RFP software OR technology filetype:pdf',
    'Temple ISD RFP software OR technology filetype:pdf',
    'Killeen ISD RFP software OR technology filetype:pdf',
    'Waco ISD RFP software OR technology filetype:pdf',
    'Tyler ISD RFP software OR technology filetype:pdf',
    'Longview ISD RFP software OR technology filetype:pdf',
    'Texarkana ISD RFP software OR technology filetype:pdf',
    'Beaumont ISD RFP software OR technology filetype:pdf',
    'Port Arthur ISD RFP software OR technology filetype:pdf',
    'Victoria ISD RFP software OR technology filetype:pdf',
    'Harlingen CISD RFP software OR technology filetype:pdf',
    'McAllen ISD RFP software OR technology filetype:pdf',
    'Edinburg CISD RFP software OR technology filetype:pdf',
    'Pharr-San Juan-Alamo ISD RFP software OR technology filetype:pdf',
    'Brownsville ISD RFP software OR technology filetype:pdf',

    # --- Non-Texas large districts ---
    'Fulton County Schools RFP "student information" OR "data" filetype:pdf',
    'Hennepin County schools RFP technology OR software filetype:pdf',
    'Prince George County schools RFP technology OR software filetype:pdf',
    'Fairfax County schools RFP "student data" OR technology filetype:pdf',
    'Baltimore County schools RFP technology OR software filetype:pdf',
    'Anne Arundel schools RFP "student data" OR technology filetype:pdf',
    'Loudoun County schools RFP "student data" OR technology filetype:pdf',
    'Chesterfield County schools RFP technology OR software filetype:pdf',
    'Henrico County schools RFP technology OR software filetype:pdf',
    'Virginia Beach schools RFP technology OR software filetype:pdf',

    # --- State education technology plans ---
    '"state education technology" plan RFP OR procurement filetype:pdf',
    '"education technology plan" "student data" OR "data system" filetype:pdf',
    '"statewide longitudinal data" system RFP OR procurement filetype:pdf',
    '"P-20" OR "P20" data system RFP school filetype:pdf',
    '"longitudinal data system" education RFP OR procurement filetype:pdf',

    # --- Cooperative purchasing (not yet searched) ---
    '"Region 4 ESC" OR "R4 ESC" RFP software OR technology filetype:pdf',
    '"Region 10 ESC" RFP software OR technology filetype:pdf',
    '"Region 13 ESC" RFP software OR technology filetype:pdf',
    '"Region 20 ESC" RFP software OR technology filetype:pdf',
    '"HGAC" school district software OR technology RFP filetype:pdf',
    '"Choice Partners" school district software filetype:pdf',
    '"DIR" "department of information resources" education software filetype:pdf',
    '"PEPPM" school district software OR technology filetype:pdf',
    '"AEPA" education procurement software OR technology filetype:pdf',

    # --- Specific RFP number patterns ---
    '"RFP 2025" school district "student information" OR software filetype:pdf',
    '"RFP 2024" school district "student information" OR technology filetype:pdf',
    '"RFP 2026" school district technology OR software filetype:pdf',
    '"CSP 2025" school district software OR technology filetype:pdf',
    '"IFB 2025" school district technology OR software filetype:pdf',
    '"RFQ 2025" school district software OR technology filetype:pdf',

    # --- Cloud/SaaS specific ---
    'school district RFP "cloud migration" OR "cloud hosting" student filetype:pdf',
    'school district RFP "managed services" technology student filetype:pdf',
    'school district RFP "hosting services" student OR learning filetype:pdf',
    'school district RFP "software as a service" OR SaaS education filetype:pdf',

    # --- Data governance / privacy ---
    'school district RFP "data governance" student filetype:pdf',
    'school district RFP "data privacy" OR "FERPA" software filetype:pdf',
    'school district RFP "cybersecurity" student data filetype:pdf',
    'school district RFP "identity management" OR "IAM" student filetype:pdf',

    # --- Performance/accountability ---
    'school district RFP "performance management" student OR teacher filetype:pdf',
    'school district RFP "accountability system" student data filetype:pdf',
    'school district RFP "strategic planning" software OR platform education filetype:pdf',
    'school district RFP "continuous improvement" software OR platform education filetype:pdf',

    # --- More state DOE procurement ---
    'site:tn.gov education RFP software OR technology',
    'site:nc.gov education RFP software OR technology',
    'site:in.gov education RFP software OR technology',
    'site:wa.gov education RFP software OR technology',
    'site:mi.gov education RFP software OR technology',
    'site:mo.gov education RFP software OR technology',
    'site:wi.gov education RFP software OR technology',
    'site:mn.gov education RFP software OR technology',

    # --- Non-PDF document types ---
    'school district RFP "student information system" 2025 filetype:doc',
    'school district "request for proposal" "data analytics" 2025 filetype:doc',
    'school district RFP "learning management" 2025 filetype:docx',
    'school district procurement "assessment platform" filetype:doc',
    'school district RFP "early warning" OR "MTSS" software filetype:doc',
    '"school district" bid "student data" OR "SIS" software filetype:doc',

    # --- Searches without filetype for HTML RFPs ---
    '"school district" RFP "student information system" site:gov 2025',
    '"school district" "request for proposal" "data warehouse" site:gov',
    '"school board" RFP "student data" platform site:gov 2025',
    '"public schools" RFP "assessment platform" OR "testing platform" 2025',
    '"school district" RFP "analytics" OR "dashboard" student 2025 2026',

    # --- International/Federal education ---
    'DoDEA "department of defense" schools RFP "student information" filetype:pdf',
    '"Bureau of Indian Education" RFP "student data" OR software filetype:pdf',
    '"Head Start" RFP "data system" OR "management information" filetype:pdf',
    '"early childhood" education RFP "data system" OR software filetype:pdf',

    # --- More platform site searches ---
    'site:vendorregistry.com school district software',
    'site:merx.com education software RFP',
    'site:bidsandtenders.ca school board software OR technology',
    'site:rfpdb.com "student information" OR "data analytics" OR "assessment"',
    'site:govwin.com school district software technology',
]


def load_existing_urls():
    urls = set()
    for csv_path in [MAIN_CSV, ROUND4_CSV,
                     os.path.join(OUTPUT_DIR, "STRIVED_ROUND3.csv"),
                     os.path.join(OUTPUT_DIR, "STRIVED_ACCUMULATED.csv")]:
        if os.path.exists(csv_path):
            try:
                df = pd.read_csv(csv_path)
                urls.update(df["pdf_url"].dropna().tolist())
            except Exception:
                pass
    return urls


def ddgs_search(query, timelimit=None):
    for attempt in range(DDGS_MAX_RETRIES):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=DDGS_MAX_RESULTS, timelimit=timelimit))
                return results
        except Exception as e:
            err = str(e).lower()
            if "ratelimit" in err or "dns" in err or "timeout" in err:
                wait = DDGS_ERROR_SLEEP * (attempt + 1)
                print(f"    Retry {attempt+1}/{DDGS_MAX_RETRIES} in {wait}s: {err[:50]}")
                time.sleep(wait)
            else:
                print(f"    Error: {err[:60]}")
                return []
    return []


def run_ddgs_queries():
    print("\n" + "=" * 60)
    print(f"ROUND 4: DDGS Queries ({len(QUERIES)} new queries)")
    print("=" * 60)

    existing = load_existing_urls()
    print(f"  Existing URLs to skip: {len(existing)}")

    all_leads = []
    seen = set(existing)
    total_new = 0

    for i, query in enumerate(QUERIES):
        print(f"    [{i+1}/{len(QUERIES)}] {query[:70]}...", end=" ", flush=True)
        results = ddgs_search(query)

        new_count = 0
        for r in results:
            url = r.get("href", "")
            if not url or url in seen:
                continue
            seen.add(url)

            is_pdf = url.lower().endswith(".pdf")
            title = r.get("title", "")
            body = r.get("body", "")

            school_match = re.search(
                r"([\w\s\.'-]+(?:ISD|CISD|School District|Public Schools|County Schools|Parish Schools|Academy|Board of Education))",
                f"{title} {body}", re.I)

            all_leads.append({
                "source": "DDGS-Round4",
                "school_name": school_match.group(1).strip() if school_match else "",
                "title": title[:200],
                "bid_url": url,
                "pdf_url": url,
                "due_date": "",
                "rfp_id": "",
                "is_pdf": is_pdf,
            })
            new_count += 1

        total_new += new_count
        print(f"{len(results)} results, {new_count} new")
        time.sleep(random.uniform(*DDGS_SLEEP))

    print(f"\n  DDGS Round 4 total: {total_new} new leads")
    return all_leads


def download_and_filter(row):
    url = row.get("pdf_url", "")
    if not url:
        return None

    is_pdf = url.lower().endswith(".pdf") or url.lower().endswith(".doc") or url.lower().endswith(".docx")
    if not is_pdf:
        return None

    if not url.lower().endswith(".pdf"):
        return None

    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        if resp.status_code != 200:
            return None

        content = resp.content
        if len(content) < 1000 or len(content) > 50_000_000:
            return None

        reader = PdfReader(io.BytesIO(content))
        text = ""
        for pg in reader.pages[:15]:
            text += pg.extract_text() or ""
            if len(text) > 20000:
                break

        if len(text) < 200:
            return None

        passed, reason = keyword_filter(text)
        emails = list(set(re.findall(EMAIL_REGEX, text)))

        return {
            "url": url,
            "text": text,
            "passed": passed,
            "reason": reason,
            "emails": "; ".join(emails[:5]),
            "row": row,
        }
    except Exception:
        return None


def verify_leads(leads):
    print(f"\n{'=' * 60}")
    print(f"VERIFICATION: {len(leads)} leads")
    print("=" * 60)

    pdf_leads = [l for l in leads if l.get("pdf_url", "").lower().endswith(".pdf")]
    print(f"  PDF leads to verify: {len(pdf_leads)}")

    print("\n  Phase 1: Download + Keyword Filter (parallel)...")
    passed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=PDF_VERIFY_WORKERS) as executor:
        futures = {executor.submit(download_and_filter, lead): lead for lead in pdf_leads}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            done += 1
            result = future.result()
            if result and result["passed"]:
                passed.append(result)
            if done % 50 == 0:
                print(f"    ... {done}/{len(pdf_leads)} processed, {len(passed)} passed keyword filter")

    print(f"  Keyword filter: {len(passed)} passed out of {len(pdf_leads)}")

    if not passed or not GROQ_API_KEY:
        if not GROQ_API_KEY:
            print("  WARNING: No GROQ_API_KEY — skipping LLM verification")
        return passed

    print(f"\n  Phase 2: LLM Verification ({len(passed)} items)...")
    client = Groq(api_key=GROQ_API_KEY)
    verified = []

    for i, item in enumerate(passed):
        row = item["row"]
        try:
            result = llm_classify(item["text"], client)
            row.update(result)
            row["contact_emails"] = item["emails"]
            row["is_relevant"] = result["llm_score"] >= 5
            verified.append(row)

            status = "RELEVANT" if row["is_relevant"] else "not relevant"
            print(f"    [{i+1}/{len(passed)}] Score={result['llm_score']} {status}: {row['title'][:60]}")
        except Exception as e:
            print(f"    [{i+1}/{len(passed)}] LLM error: {str(e)[:50]}")
            if "rate_limit" in str(e).lower():
                time.sleep(15)
        time.sleep(GROQ_SLEEP)

    relevant = [v for v in verified if v.get("is_relevant")]
    print(f"\n  LLM verified: {len(relevant)} relevant out of {len(verified)}")
    return verified


def save_and_merge(verified):
    if not verified:
        print("\n  No results to save.")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_df = pd.DataFrame(verified)
    out_df.to_csv(ROUND4_CSV, index=False)
    print(f"\n  Saved {len(out_df)} leads to {ROUND4_CSV}")

    new_relevant = out_df[(out_df.get("is_relevant", False) == True) &
                          (out_df.get("llm_score", 0) >= 5)]

    if os.path.exists(MAIN_CSV) and len(new_relevant) > 0:
        main_df = pd.read_csv(MAIN_CSV)
        main_urls = set(main_df["pdf_url"].dropna())
        to_add = new_relevant[~new_relevant["pdf_url"].isin(main_urls)]

        if len(to_add) > 0:
            for col in main_df.columns:
                if col not in to_add.columns:
                    to_add[col] = ""
            main_df = pd.concat([main_df, to_add[main_df.columns]], ignore_index=True)
            main_df.to_csv(MAIN_CSV, index=False)
            print(f"  Merged {len(to_add)} new relevant leads into {MAIN_CSV}")
            print(f"  Main CSV now has {len(main_df)} total leads")
        else:
            print("  No new unique relevant leads to merge")

    rel_count = len(new_relevant) if len(new_relevant) > 0 else 0
    print(f"\n  ROUND 4 SUMMARY: {len(verified)} verified, {rel_count} relevant")


def main():
    print(f"\n{'#' * 60}")
    print(f"  STRIVED Round 4 RFP Collection")
    print(f"{'#' * 60}")

    leads = run_ddgs_queries()

    if leads:
        verified = verify_leads(leads)
        save_and_merge(verified)
    else:
        print("\n  No leads found.")

    print(f"\n{'#' * 60}")
    print(f"  DONE")
    print(f"{'#' * 60}")


if __name__ == "__main__":
    main()
