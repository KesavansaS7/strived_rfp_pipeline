"""
strived_multipass.py — Multi-pass RFP collector with accumulation

Runs DDGS queries multiple times with different parameters to maximize unique results.
Each pass uses a different timelimit (None, 'y', 'm') and accumulates results.
Then runs a single verification pass (keyword + LLM) on all unique leads.
Merges with any previous results from STRIVED_COMPLETE_TEXAS.csv.

Usage:
    python strived_multipass.py              # 3 DDGS passes + verify
    python strived_multipass.py --passes 5   # 5 DDGS passes + verify
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
DDGS_MAX_RETRIES = 2

PAGES_TO_PARSE = 15
REQUEST_TIMEOUT = 15
PDF_VERIFY_WORKERS = 15

OUTPUT_DIR = "output"
MAIN_CSV = os.path.join(OUTPUT_DIR, "STRIVED_COMPLETE_TEXAS.csv")
ACCUMULATOR_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ACCUMULATED.csv")

EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
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
    if sw < 1 or ed < 1:
        return False, f"Failed (sw={sw}, ed={ed})"
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


# High-value queries focused on actual RFP documents
QUERIES = [
    # Core SIS/Data queries
    'school district RFP "student information system" filetype:pdf',
    'school district RFP "student data" platform software filetype:pdf',
    'school district RFP "data analytics" student filetype:pdf',
    'school district RFP "data dashboard" OR "data warehouse" filetype:pdf',
    'school district RFP "learning management system" filetype:pdf',
    'school district RFP "assessment platform" filetype:pdf',
    'school district RFP "edtech" OR "educational technology" software filetype:pdf',
    'school district RFP "SaaS" student OR learning filetype:pdf',
    'school district RFP "data integration" student filetype:pdf',
    'school district RFP "student enrollment" software filetype:pdf',
    'school district RFP "SIS" OR "student information" software filetype:pdf',
    'school district RFP "early warning system" OR "dropout prevention" filetype:pdf',
    'school district RFP "formative assessment" OR "interim assessment" filetype:pdf',
    'school district RFP "parent portal" OR "family engagement" platform filetype:pdf',
    'school district RFP "special education" software OR platform filetype:pdf',
    'school district RFP "gradebook" OR "grading" software filetype:pdf',
    'school district RFP "professional development" platform filetype:pdf',
    'school district RFP "RTI" OR "MTSS" software filetype:pdf',
    'school district RFP "ERP" OR "enterprise" school system filetype:pdf',
    'school district RFP "single sign-on" OR "SSO" OR "identity" filetype:pdf',
    'public schools RFP "data warehouse" OR "data integration" filetype:pdf',
    'public schools RFP "progress monitoring" OR "benchmark assessment" filetype:pdf',
    'school district RFP "digital curriculum" OR "online learning" platform filetype:pdf',
    'school district RFP "behavior management" OR "PBIS" software filetype:pdf',
    'school district RFP "college readiness" OR "career planning" filetype:pdf',
    'school district RFP "SEL" OR "social emotional learning" platform filetype:pdf',
    'school district RFP "IEP" software OR "special education" filetype:pdf',
    '"student information system" RFP "PowerSchool" OR "Infinite Campus" filetype:pdf',
    'school district RFP "Clever" OR "ClassLink" filetype:pdf',
    'school district RFP "NWEA" OR "MAP Growth" OR "iReady" filetype:pdf',

    # Procurement variants
    'school district RFQ "student data" OR "student information" software filetype:pdf',
    'school district "invitation to bid" "student information" filetype:pdf',
    'school district "solicitation" "student data" OR "assessment" software filetype:pdf',
    'public schools "request for proposal" "SIS" OR "student information system" filetype:pdf',
    'K-12 "request for qualifications" software OR "data platform" filetype:pdf',

    # State-specific (all 50)
    'Texas school district RFP "student information" OR "data platform" software filetype:pdf',
    'Texas ISD RFP software OR technology OR "student data" filetype:pdf',
    'California school district RFP "student information" OR "data platform" filetype:pdf',
    'Florida school district RFP "student data" OR "assessment" software filetype:pdf',
    'New York school district RFP "student data" OR "learning management" filetype:pdf',
    'Illinois school district RFP "student information" OR "edtech" filetype:pdf',
    'Ohio school district RFP "student data" OR "assessment platform" filetype:pdf',
    'Pennsylvania school district RFP "student information" software filetype:pdf',
    'Georgia school district RFP "student data" OR "learning management" filetype:pdf',
    'North Carolina school district RFP "educational technology" OR "SIS" filetype:pdf',
    'Michigan school district RFP "student data" OR "data analytics" filetype:pdf',
    'Virginia school district RFP "student information" OR "assessment" filetype:pdf',
    'New Jersey school district RFP "student data" OR "technology" filetype:pdf',
    'Washington school district RFP "student information" OR "LMS" filetype:pdf',
    'Colorado school district RFP "student data" OR "edtech" filetype:pdf',
    'Massachusetts school district RFP "student information" OR "data platform" filetype:pdf',
    'Maryland school district RFP "student data" OR "assessment" filetype:pdf',
    'Indiana school district RFP "student information" OR "edtech" software filetype:pdf',
    'Tennessee school district RFP "student data" OR "assessment" software filetype:pdf',
    'Arizona school district RFP "student information" OR "data platform" filetype:pdf',
    'Missouri school district RFP "student data" OR "data platform" filetype:pdf',
    'Wisconsin school district RFP "student data" OR "assessment platform" filetype:pdf',
    'Minnesota school district RFP "student data" OR "assessment" software filetype:pdf',
    'South Carolina school district RFP "student data" OR "edtech" software filetype:pdf',
    'Alabama school district RFP "student data" OR "student information" software filetype:pdf',
    'Oregon school district RFP "student data" OR "data platform" filetype:pdf',
    'Louisiana school district RFP "student data" OR "learning management" filetype:pdf',
    'Kentucky school district RFP "student information" OR "data analytics" filetype:pdf',
    'Oklahoma school district RFP "student information" OR "assessment" software filetype:pdf',
    'Connecticut school district RFP "student information" OR "edtech" filetype:pdf',
    'Iowa school district RFP "student data" OR "assessment platform" filetype:pdf',
    'Mississippi school district RFP "student information" OR "edtech" filetype:pdf',
    'Kansas school district RFP "student data" OR "technology" software filetype:pdf',
    'Arkansas school district RFP "student data" OR "assessment" software filetype:pdf',
    'Nevada school district RFP "student information" OR "data platform" filetype:pdf',
    'Utah school district RFP "student information" OR "data platform" filetype:pdf',
    'New Mexico school district RFP "student information" OR "edtech" filetype:pdf',
    'Nebraska school district RFP "student data" OR "assessment" filetype:pdf',
    'West Virginia school district RFP "student information" OR "edtech" filetype:pdf',
    'Idaho school district RFP "student data" OR "assessment" filetype:pdf',
    'Hawaii school district RFP "student information" OR "data platform" filetype:pdf',
    'Maine school district RFP "student information" OR "technology" filetype:pdf',
    'New Hampshire school district RFP "student data" OR "technology" filetype:pdf',
    'Montana school district RFP "student information" OR "technology" filetype:pdf',
    'Delaware school district RFP "student data" OR "technology" filetype:pdf',
    'Rhode Island school district RFP "student information" OR "technology" filetype:pdf',
    'South Dakota school district RFP "student information" OR "technology" filetype:pdf',
    'North Dakota school district RFP "student data" OR "technology" filetype:pdf',
    'Alaska school district RFP "student data" OR "technology" software filetype:pdf',
    'Vermont school district RFP "student data" OR "technology" filetype:pdf',
    'Wyoming school district RFP "student information" OR "technology" filetype:pdf',

    # Big district queries
    'Houston ISD RFP software OR technology OR "student data" filetype:pdf',
    'Dallas ISD RFP software OR technology OR "student data" filetype:pdf',
    'Los Angeles Unified RFP software OR technology OR "student data" filetype:pdf',
    'Chicago Public Schools RFP software OR "student data" filetype:pdf',
    'Miami-Dade County Public Schools RFP software OR "student data" filetype:pdf',
    'Clark County School District RFP software OR "student data" filetype:pdf',
    'Broward County Public Schools RFP software OR "student data" filetype:pdf',
    'Hillsborough County Public Schools RFP software OR "student data" filetype:pdf',
    'Orange County Public Schools RFP software OR "student data" filetype:pdf',
    'Fairfax County Public Schools RFP software OR "student data" filetype:pdf',
    'Gwinnett County Public Schools RFP software OR "student data" filetype:pdf',
    'Wake County Public Schools RFP software OR "student data" filetype:pdf',
    'Montgomery County Public Schools RFP software OR "student data" filetype:pdf',
    'San Diego Unified School District RFP software OR "student data" filetype:pdf',
    'San Francisco Unified School District RFP software OR "student data" filetype:pdf',
    'Denver Public Schools RFP software OR "student data" filetype:pdf',
    'Baltimore City Public Schools RFP software OR "student data" filetype:pdf',
    'Milwaukee Public Schools RFP software OR "student data" filetype:pdf',
    'Memphis-Shelby County Schools RFP software OR "student data" filetype:pdf',
    'Jefferson County Public Schools RFP software OR "student data" filetype:pdf',
    'Palm Beach County Schools RFP software OR "student data" filetype:pdf',
    'DeKalb County School District RFP software OR "student data" filetype:pdf',
    'Cobb County School District RFP software OR "student data" filetype:pdf',
    'Philadelphia School District RFP software OR "student data" filetype:pdf',
    'Detroit Public Schools RFP software OR "student data" filetype:pdf',
    'Seattle Public Schools RFP software OR "student data" filetype:pdf',
    'Portland Public Schools RFP software OR "student data" filetype:pdf',
    'Boston Public Schools RFP software OR "student data" filetype:pdf',
    'Atlanta Public Schools RFP software OR "student data" filetype:pdf',
    'Jackson Public School District RFP software OR "student data" filetype:pdf',

    # Aggregator/site searches
    'site:findrfp.com school district software OR technology',
    'site:vendorregistry.com school district RFP software filetype:pdf',
    'site:bonfirehub.com school district RFP technology',
    'site:planetbids.com school district software OR technology',
    'site:governmentnavigator.com school district software',
    'site:rfpdb.com school district software OR technology',

    # Year-specific
    'school district RFP "student information system" 2025 filetype:pdf',
    'school district RFP "student information system" 2026 filetype:pdf',
    'school district RFP "data analytics" student 2025 filetype:pdf',
    'school district RFP "learning management system" 2025 filetype:pdf',
    'school district RFP "assessment platform" 2025 OR 2026 filetype:pdf',

    # --- NEW: Cooperative purchasing organizations ---
    '"BuyBoard" school district RFP software OR technology filetype:pdf',
    '"TIPS" OR "TAPS" school district RFP software OR technology filetype:pdf',
    '"Sourcewell" school district RFP "student data" OR software filetype:pdf',
    '"OMNIA Partners" school district software OR technology filetype:pdf',
    '"E&I Cooperative" education RFP software OR technology filetype:pdf',
    '"National IPA" school district software OR technology filetype:pdf',
    '"NJPA" school district RFP software filetype:pdf',
    '"cooperative purchasing" school district software OR technology RFP filetype:pdf',
    '"purchasing cooperative" school district "data platform" OR software filetype:pdf',
    '"consortium" school district RFP "student information" OR software filetype:pdf',

    # --- NEW: Regional education agencies (BOCES, IU, RESA, ESC) ---
    'BOCES RFP "student information" OR "data platform" software filetype:pdf',
    '"intermediate unit" RFP "student data" OR software filetype:pdf',
    'RESA RFP "student information" OR "educational technology" filetype:pdf',
    '"education service center" RFP software OR technology filetype:pdf',
    '"education service district" RFP "student data" OR software filetype:pdf',
    '"regional education" RFP "student information" OR software filetype:pdf',
    '"education cooperative" RFP software OR technology filetype:pdf',

    # --- NEW: State education department RFPs ---
    '"department of education" RFP "student information system" filetype:pdf',
    '"department of education" RFP "data analytics" student filetype:pdf',
    '"state education agency" RFP "student data" software filetype:pdf',
    'TEA RFP "student data" OR "student information" software filetype:pdf',
    'NYSED RFP "student data" OR "data platform" filetype:pdf',

    # --- NEW: Charter school networks ---
    '"charter school" RFP "student information system" filetype:pdf',
    '"charter school" RFP "student data" OR "data platform" filetype:pdf',
    '"charter school" RFP "learning management" OR "assessment" filetype:pdf',
    '"charter management" RFP software OR technology filetype:pdf',
    'KIPP RFP software OR "student data" filetype:pdf',

    # --- NEW: More big districts (next 40) ---
    'Duval County Public Schools RFP software OR "student data" filetype:pdf',
    'Shelby County Schools RFP software OR "student data" filetype:pdf',
    'Prince George County Public Schools RFP software OR "student data" filetype:pdf',
    'Henrico County Public Schools RFP software OR "student data" filetype:pdf',
    'Loudoun County Public Schools RFP software OR "student data" filetype:pdf',
    'Pinellas County Schools RFP software OR "student data" filetype:pdf',
    'Polk County Schools RFP software OR "student data" filetype:pdf',
    'Lee County School District RFP software OR "student data" filetype:pdf',
    'Volusia County Schools RFP software OR "student data" filetype:pdf',
    'Pasco County Schools RFP software OR "student data" filetype:pdf',
    'Sacramento City Unified RFP software OR "student data" filetype:pdf',
    'Long Beach Unified School District RFP software OR "student data" filetype:pdf',
    'Fresno Unified School District RFP software OR "student data" filetype:pdf',
    'Santa Ana Unified School District RFP software OR "student data" filetype:pdf',
    'Mesa Public Schools RFP software OR "student data" filetype:pdf',
    'Tucson Unified School District RFP software OR "student data" filetype:pdf',
    'Albuquerque Public Schools RFP software OR "student data" filetype:pdf',
    'Columbus City Schools RFP software OR "student data" filetype:pdf',
    'Cleveland Metropolitan School District RFP software OR "student data" filetype:pdf',
    'Cincinnati Public Schools RFP software OR "student data" filetype:pdf',
    'Indianapolis Public Schools RFP software OR "student data" filetype:pdf',
    'Fort Wayne Community Schools RFP software OR "student data" filetype:pdf',
    'Minneapolis Public Schools RFP software OR "student data" filetype:pdf',
    'St. Paul Public Schools RFP software OR "student data" filetype:pdf',
    'Omaha Public Schools RFP software OR "student data" filetype:pdf',
    'Nashville Metropolitan Public Schools RFP software OR "student data" filetype:pdf',
    'Oklahoma City Public Schools RFP software OR "student data" filetype:pdf',
    'Tulsa Public Schools RFP software OR "student data" filetype:pdf',
    'Kansas City Public Schools RFP software OR "student data" filetype:pdf',
    'St. Louis Public Schools RFP software OR "student data" filetype:pdf',
    'Richmond Public Schools RFP software OR "student data" filetype:pdf',
    'Norfolk Public Schools RFP software OR "student data" filetype:pdf',
    'Charlotte-Mecklenburg Schools RFP software OR "student data" filetype:pdf',
    'Guilford County Schools RFP software OR "student data" filetype:pdf',
    'Charleston County School District RFP software OR "student data" filetype:pdf',
    'Greenville County Schools RFP software OR "student data" filetype:pdf',
    'Jefferson Parish Schools RFP software OR "student data" filetype:pdf',
    'East Baton Rouge Parish Schools RFP software OR "student data" filetype:pdf',
    'Little Rock School District RFP software OR "student data" filetype:pdf',
    'Wichita Public Schools RFP software OR "student data" filetype:pdf',

    # --- NEW: Specific product/platform RFPs ---
    'school district RFP "PowerSchool" replacement OR migration filetype:pdf',
    'school district RFP "Infinite Campus" OR "Tyler SIS" filetype:pdf',
    'school district RFP "Skyward" OR "Aeries" student information filetype:pdf',
    'school district RFP "Canvas" OR "Schoology" OR "Google Classroom" filetype:pdf',
    'school district RFP "Illuminate" OR "DnA" OR "Renaissance" assessment filetype:pdf',
    'school district RFP "SchoolMint" OR "Lotterease" enrollment filetype:pdf',
    'school district RFP "Frontline" OR "Applitrack" HR software filetype:pdf',
    'school district RFP "Tableau" OR "Power BI" education data filetype:pdf',
    'school district RFP "Kickboard" OR "SWIS" OR "Hero" behavior filetype:pdf',
    'school district RFP "Naviance" OR "Xello" college career filetype:pdf',

    # --- NEW: Functional area RFPs ---
    'school district RFP "school safety" software OR platform filetype:pdf',
    'school district RFP "transportation management" software filetype:pdf',
    'school district RFP "food service" OR "nutrition" software filetype:pdf',
    'school district RFP "facilities management" software filetype:pdf',
    'school district RFP "human capital" OR "talent management" software filetype:pdf',
    'school district RFP "financial management" OR "accounting" software filetype:pdf',
    'school district RFP "communication" OR "notification" platform filetype:pdf',
    'school district RFP "library management" OR "media center" software filetype:pdf',
    'school district RFP "visitor management" software filetype:pdf',
    'school district RFP "helpdesk" OR "IT service management" filetype:pdf',

    # --- NEW: Alternative search terms ---
    '"public school" procurement "data platform" OR "SIS" filetype:pdf',
    '"unified school district" RFP software OR "data analytics" filetype:pdf',
    '"county schools" RFP "student information" OR "data" software filetype:pdf',
    '"parish schools" RFP software OR "student data" filetype:pdf',
    '"city schools" RFP "student information" OR software filetype:pdf',
    '"independent school district" RFP "data analytics" OR software filetype:pdf',
    '"public schools" bid "student information system" filetype:pdf',
    '"school board" RFP "student data" OR "educational technology" filetype:pdf',

    # --- NEW: More aggregator/platform searches ---
    'site:publicpurchase.com school district software OR technology',
    'site:opengov.com school district procurement software',
    'site:procurenow.com school district software OR technology',
    'site:ionwave.net school district software',
    'site:negometrix.com school OR education software',
    'site:merx.com school district software OR education',
    'site:bidsync.com school district software OR education',

    # --- NEW: More year-specific ---
    'school district RFP "student data" 2024 filetype:pdf',
    'school district RFP "data analytics" 2024 OR 2025 filetype:pdf',
    'school district RFP edtech 2025 OR 2026 filetype:pdf',
    'school district "request for proposal" technology 2025 filetype:pdf',
    'school district RFP "SIS" 2024 OR 2025 OR 2026 filetype:pdf',
    '"school district" RFP "student information" 2023 filetype:pdf',
    '"school district" RFP "assessment" software 2024 filetype:pdf',

    # --- NEW: Specific Texas districts not in main script ---
    'Aldine ISD RFP software OR technology filetype:pdf',
    'Conroe ISD RFP software OR technology filetype:pdf',
    'Round Rock ISD RFP software OR technology filetype:pdf',
    'Leander ISD RFP software OR technology filetype:pdf',
    'Klein ISD RFP software OR technology filetype:pdf',
    'Spring Branch ISD RFP software OR technology filetype:pdf',
    'Pasadena ISD RFP software OR technology filetype:pdf',
    'Humble ISD RFP software OR technology filetype:pdf',
    'Clear Creek ISD RFP software OR technology filetype:pdf',
    'Lamar CISD RFP software OR technology filetype:pdf',
    'Richardson ISD RFP software OR technology filetype:pdf',
    'Mesquite ISD RFP software OR technology filetype:pdf',
    'Grand Prairie ISD RFP software OR technology filetype:pdf',
    'Garland ISD RFP software OR technology filetype:pdf',
    'Irving ISD RFP software OR technology filetype:pdf',
]


def run_ddgs_pass(pass_num, timelimit, seen_urls):
    """Run all queries with a given timelimit, skip already-seen URLs."""
    label = f"Pass {pass_num}" + (f" (timelimit={timelimit})" if timelimit else " (no timelimit)")
    print(f"\n{'='*60}")
    print(f"DDGS {label} — {len(QUERIES)} queries")
    print(f"{'='*60}\n")

    new_results = []
    total = len(QUERIES)
    consecutive_errors = 0

    with DDGS() as ddgs:
        for i, query in enumerate(QUERIES):
            print(f"  [{i+1}/{total}] {query[:60]}...")
            time.sleep(random.uniform(*DDGS_SLEEP))

            for attempt in range(DDGS_MAX_RETRIES + 1):
                try:
                    if timelimit:
                        search_results = list(ddgs.text(query, max_results=DDGS_MAX_RESULTS, timelimit=timelimit))
                    else:
                        search_results = list(ddgs.text(query, max_results=DDGS_MAX_RESULTS))
                    new_count = 0

                    for r in search_results:
                        url = r.get("href", "")
                        if not url or url in seen_urls:
                            continue
                        if not url.lower().endswith(".pdf"):
                            continue
                        seen_urls.add(url)
                        new_count += 1
                        new_results.append({
                            "source": "DDGS",
                            "school_name": "",
                            "title": r.get("title", ""),
                            "bid_url": url,
                            "pdf_url": url,
                            "due_date": "",
                            "rfp_id": "",
                        })

                    print(f"          Raw: {len(search_results)} | New: {new_count} | Total: {len(new_results)}")
                    consecutive_errors = 0
                    break

                except Exception as e:
                    if attempt < DDGS_MAX_RETRIES:
                        wait = DDGS_ERROR_SLEEP * (attempt + 1)
                        print(f"          Retry {attempt+1}: sleeping {wait}s...")
                        time.sleep(wait)
                    else:
                        print(f"          Failed: {str(e)[:60]}")
                        consecutive_errors += 1

            if consecutive_errors >= 5:
                print(f"\n  *** 5 consecutive failures — pausing 120s ***")
                time.sleep(120)
                consecutive_errors = 0

    print(f"\n  {label} done: {len(new_results)} new unique PDFs")
    return new_results


def download_and_keyword_filter(row):
    url = row.get("pdf_url", "")
    result = {
        **row,
        "emails": "",
        "keyword_pass": False,
        "keyword_reason": "",
        "llm_software": "",
        "llm_education": "",
        "llm_strived_fit": "",
        "llm_score": 0,
        "llm_summary": "",
        "is_relevant": False,
        "status": "",
        "_text": "",
    }

    if not url or not url.startswith("http"):
        result["status"] = "No URL"
        return result

    if not url.lower().endswith(".pdf"):
        result["status"] = "Not a PDF"
        return result

    for attempt in range(3):
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT + (attempt * 10),
                                    headers={"User-Agent": USER_AGENTS[attempt % len(USER_AGENTS)]})
            response.raise_for_status()

            reader = PdfReader(io.BytesIO(response.content))
            text = ""
            for p in reader.pages[:PAGES_TO_PARSE]:
                extracted = p.extract_text()
                if extracted:
                    text += extracted + "\n"

            if not text.strip():
                result["status"] = "PDF is blank or unreadable"
                return result

            result["emails"] = ", ".join(set(re.findall(EMAIL_REGEX, text)))

            if not result["school_name"]:
                match = re.search(
                    r"([A-Z][A-Za-z\s]+(?:Independent School District|ISD|CISD|School District|Academy|Public Schools))",
                    text[:3000])
                if match:
                    result["school_name"] = match.group(1).strip()

            passed, reason = keyword_filter(text)
            result["keyword_pass"] = passed
            result["keyword_reason"] = reason
            result["_text"] = text

            if not passed:
                result["status"] = f"Keyword: {reason}"
            break

        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            result["status"] = "Download Failed"
        except Exception as e:
            result["status"] = f"Error: {type(e).__name__}"
            break

    return result


def verify_all(leads, client):
    print(f"\nPhase 1: Downloading & keyword filtering {len(leads)} leads ({PDF_VERIFY_WORKERS} workers)...\n")

    all_results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=PDF_VERIFY_WORKERS) as executor:
        futures = {executor.submit(download_and_keyword_filter, lead): i
                   for i, lead in enumerate(leads)}
        done_count = 0
        for future in concurrent.futures.as_completed(futures):
            done_count += 1
            result = future.result()
            all_results.append((futures[future], result))
            if done_count % 50 == 0 or done_count == len(leads):
                kw_passed = sum(1 for _, r in all_results if r["keyword_pass"])
                print(f"  [{done_count}/{len(leads)}] processed, {kw_passed} keyword-passed")

    all_results.sort(key=lambda x: x[0])
    all_results = [r for _, r in all_results]

    keyword_passed = [r for r in all_results if r["keyword_pass"] and r["_text"]]
    print(f"\nPhase 2: LLM verification for {len(keyword_passed)} keyword-passed leads...\n")

    for i, result in enumerate(keyword_passed):
        title_preview = result.get("title", "")[:45] or result.get("pdf_url", "")[:45]
        print(f"  [{i+1}/{len(keyword_passed)}] {title_preview}")

        try:
            llm = llm_classify(result["_text"], client)
            result.update(llm)

            if result["llm_software"] == "YES" and result["llm_education"] == "YES" and result["llm_score"] >= 5:
                result["is_relevant"] = True
            elif result["llm_score"] >= 7 and result["llm_education"] == "YES":
                result["is_relevant"] = True

            result["status"] = "Success"
            tag = "*** RELEVANT ***" if result["is_relevant"] else f"Score: {result['llm_score']}/10"
            print(f"        -> {tag} | {result['llm_summary'][:60]}")

        except Exception as e:
            result["status"] = f"LLM Error: {type(e).__name__}"
            print(f"        -> LLM Error: {e}")

        time.sleep(GROQ_SLEEP)

    for r in all_results:
        r.pop("_text", None)

    return all_results


def main():
    num_passes = 3
    if "--passes" in sys.argv:
        idx = sys.argv.index("--passes")
        if idx + 1 < len(sys.argv):
            num_passes = int(sys.argv[idx + 1])

    print("=" * 60)
    print(f"STRIVED MULTI-PASS RFP COLLECTOR — {num_passes} passes")
    print("=" * 60)

    # Load previously-seen URLs to avoid re-downloading
    seen_urls = set()
    prev_relevant = pd.DataFrame()

    for csv_path in [MAIN_CSV, ACCUMULATOR_CSV]:
        if os.path.exists(csv_path):
            try:
                prev_df = pd.read_csv(csv_path)
                for url in prev_df["pdf_url"].dropna():
                    seen_urls.add(url)
                prev_rel = prev_df[prev_df["is_relevant"] == True]
                if len(prev_rel) > 0:
                    prev_relevant = pd.concat([prev_relevant, prev_rel], ignore_index=True)
            except Exception:
                pass

    if seen_urls:
        print(f"\nLoaded {len(seen_urls)} previously-seen URLs (will skip these in search)")
        print(f"Previously found {len(prev_relevant)} relevant RFPs (will merge at end)")

    # Run multiple DDGS passes
    all_new_leads = []
    timelimits = [None, 'y', 'm', None, 'y']

    for pass_num in range(1, num_passes + 1):
        tl = timelimits[(pass_num - 1) % len(timelimits)]
        new_leads = run_ddgs_pass(pass_num, tl, seen_urls)
        all_new_leads.extend(new_leads)

        if pass_num < num_passes:
            pause = random.uniform(30, 60)
            print(f"\n  Pausing {pause:.0f}s between passes...")
            time.sleep(pause)

    print(f"\n{'='*60}")
    print(f"COLLECTION COMPLETE")
    print(f"  New unique PDFs from {num_passes} passes: {len(all_new_leads)}")
    print(f"{'='*60}")

    if not all_new_leads:
        print("No new leads found. Exiting.")
        return

    # Verify new leads only
    client = Groq(api_key=GROQ_API_KEY)
    all_results = verify_all(all_new_leads, client)

    # Save this run's results
    out_df = pd.DataFrame(all_results)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    save_path = ACCUMULATOR_CSV
    try:
        out_df.to_csv(save_path, index=False)
    except PermissionError:
        save_path = ACCUMULATOR_CSV.replace(".csv", f"_{int(time.time())}.csv")
        out_df.to_csv(save_path, index=False)

    # Merge new relevant results INTO the main CSV
    new_relevant = out_df[(out_df["is_relevant"] == True) & (out_df["llm_score"] >= 5)]

    if os.path.exists(MAIN_CSV) and len(new_relevant) > 0:
        try:
            main_df = pd.read_csv(MAIN_CSV)
            main_urls = set(main_df["pdf_url"].dropna())
            to_add = new_relevant[~new_relevant["pdf_url"].isin(main_urls)]
            if len(to_add) > 0:
                for col in main_df.columns:
                    if col not in to_add.columns:
                        to_add[col] = ""
                main_df = pd.concat([main_df, to_add[main_df.columns]], ignore_index=True)
                main_df.to_csv(MAIN_CSV, index=False)
                print(f"\n  Added {len(to_add)} new relevant RFPs to {MAIN_CSV}")
            main_rel = main_df[(main_df["is_relevant"] == True) & (main_df["llm_score"] >= 5)]
            print(f"  Main CSV now: {len(main_df)} rows, {len(main_rel)} relevant")
        except Exception as e:
            print(f"\n  Warning: Could not update main CSV: {e}")

    new_rel_count = len(new_relevant)
    total_kw = out_df['keyword_pass'].sum() if 'keyword_pass' in out_df.columns else 0

    print(f"\n{'='*60}")
    print(f"FINAL RESULTS")
    print(f"{'='*60}")
    print(f"  New leads this run:      {len(all_new_leads)}")
    print(f"  Keyword passed:          {total_kw}")
    print(f"  NEW relevant found:      {new_rel_count}")
    print(f"{'='*60}")

    if new_rel_count > 0:
        print(f"\nNew Relevant RFPs:")
        for _, r in new_relevant.iterrows():
            print(f"  [{r['source']}] {r['school_name']}")
            print(f"    Score: {r.get('llm_score', 'N/A')}/10 | {r.get('llm_summary', '')}")
            print(f"    URL:   {r.get('pdf_url', '')}")
            if r['emails'] and str(r['emails']) != 'nan':
                print(f"    Email: {r['emails']}")
            print()

    print(f"Saved to {save_path}")


if __name__ == "__main__":
    main()
