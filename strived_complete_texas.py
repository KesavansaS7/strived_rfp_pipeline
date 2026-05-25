"""
strived_complete_texas.py — Complete Texas RFP Finder for Strived.io

Combines 5 sources:
  1. ESBD (Electronic State Business Daily)
  2. District Website Crawling (800+ districts)
  3. DDGS (DuckDuckGo Search, 270+ national queries across all 50 states)
  4. BidNet Direct (public bid listings)
  5. ESC Website Crawling (20 Education Service Centers)

Then filters for Strived.io relevance using:
  Layer 1: Keyword filter (free, instant) — parallel with ThreadPoolExecutor
  Layer 2: Groq LLM verification (AI classification) — sequential

Usage:
    python strived_complete_texas.py              # all sources
    python strived_complete_texas.py esbd         # ESBD only
    python strived_complete_texas.py portals      # District crawling only
    python strived_complete_texas.py ddgs         # DDGS only
    python strived_complete_texas.py bidnet       # BidNet Direct only
    python strived_complete_texas.py esc          # ESC crawling only
"""

import asyncio
import concurrent.futures
import io
import os
import random
import re
import sys
import time
from urllib.parse import urljoin

import aiohttp
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from ddgs import DDGS
from groq import Groq
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from pypdf import PdfReader

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# CONFIG
# ============================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_SLEEP = 5

ESBD_MAX_PAGES = 5
ESBD_NIGP_CODES = ["920", "208", "924"]

DDGS_MAX_RESULTS = 500
DDGS_SLEEP = (5.0, 8.0)
DDGS_ERROR_SLEEP = 30
DDGS_MAX_RETRIES = 2

PAGES_TO_PARSE = 15
REQUEST_TIMEOUT = 15

DISTRICT_CRAWL_CONCURRENCY = 25
MIN_ENROLLMENT = 1000

BIDNET_CONCURRENCY = 5
BIDNET_BASE = "https://www.bidnetdirect.com"
BIDNET_SEARCH_KEYWORDS = [
    "ISD", "school district", "student", "instructional",
    "assessment", "software", "technology", "curriculum",
    "data analytics", "education", "learning", "school",
]

PDF_VERIFY_WORKERS = 15

OUTPUT_DIR = "output"
OUTPUT_CSV = os.path.join(OUTPUT_DIR, "STRIVED_COMPLETE_TEXAS.csv")

EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]


# ============================================================
# KEYWORD FILTER (Layer 1) — density-based
# ============================================================
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


def quick_title_filter(title):
    title_lower = title.lower()
    tech_words = ["software", "saas", "technology", "data", "computer",
                  "digital", "cloud", "platform", "system", "online",
                  "learning management", "assessment", "edtech", "it services",
                  "programming", "internet", "network", "web", "analytics",
                  "curriculum", "instructional", "student", "education",
                  "sis", "lms", "erp", "information system", "management system",
                  "monitoring", "dashboard", "integration", "portal"]
    return any(w in title_lower for w in tech_words)


# ============================================================
# GROQ LLM (Layer 2)
# ============================================================
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


# ============================================================
# HELPER
# ============================================================
def is_school_district(name):
    upper = name.upper()
    markers = [" ISD", " CISD", "SCHOOL DISTRICT", "ACADEMY", "CHARTER SCHOOL",
               "PUBLIC SCHOOL", "COLLEGIATE", "PREPARATORY"]
    return any(m in upper for m in markers)


# ============================================================
# SOURCE 1: ESBD
# ============================================================
async def scrape_esbd():
    print("\n" + "=" * 60)
    print("SOURCE 1: ESBD (Electronic State Business Daily)")
    print("=" * 60)

    all_results = []
    seen = set()
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(user_agent=random.choice(USER_AGENTS))

    for nigp_code in ESBD_NIGP_CODES:
        print(f"\n  NIGP {nigp_code}...")
        for pg in range(1, ESBD_MAX_PAGES + 1):
            url = f"https://www.txsmartbuy.gov/esbd?page={pg}&nigp={nigp_code}"
            try:
                await page.goto(url, timeout=60000)
                await page.wait_for_timeout(4000)
            except PlaywrightTimeout:
                break

            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select(".esbd-result-row")

            if not rows:
                break

            page_hits = 0
            for row in rows:
                title_el = row.select_one(".esbd-result-title")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                link = title_el.find("a", href=True)
                href = ""
                if link:
                    h = link["href"]
                    href = h if h.startswith("http") else "https://www.txsmartbuy.com" + h

                text = row.get_text(separator=" ", strip=True)
                if not is_school_district(text):
                    continue

                if href in seen:
                    continue
                seen.add(href)

                agency_match = re.search(
                    r"([\w\s\.'-]+(?:ISD|CISD|School District|Academy|Public Schools))",
                    text, re.I)

                all_results.append({
                    "source": "ESBD",
                    "school_name": agency_match.group(1).strip() if agency_match else "",
                    "title": title,
                    "bid_url": href,
                    "pdf_url": href,
                    "due_date": "",
                    "rfp_id": "",
                })
                page_hits += 1

            print(f"    Page {pg}: {len(rows)} results, {page_hits} school bids")

            if len(rows) < 24:
                break
            await asyncio.sleep(random.uniform(1.5, 3.0))

    await browser.close()
    await pw.stop()

    print(f"\n  ESBD total: {len(all_results)} school technology bids")
    return all_results


# ============================================================
# SOURCE 2: DISTRICT WEBSITE CRAWLING (expanded to 800+)
# ============================================================
PROCUREMENT_PATHS = [
    "/purchasing", "/procurement", "/bids", "/rfps", "/solicitations",
    "/departments/purchasing", "/departments/finance/purchasing",
    "/business-services/purchasing", "/about/purchasing",
    "/departments/business-services", "/finance/purchasing",
    "/business/purchasing", "/rfp", "/current-bids",
    "/business-office/purchasing", "/purchasing-bids",
    "/about-us/departments/purchasing", "/vendor-information",
    "/departments/financial-services/purchasing",
]

TECH_TERMS = ["software", "technology", "data", "computer", "digital", "platform",
              "system", "saas", "cloud", "online", "network", "edtech",
              "learning management", "assessment", "student", "instructional",
              "curriculum", "analytics", "dashboard"]


def load_top_districts():
    askted = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "askted.csv")
    if not os.path.exists(askted):
        return []
    df = pd.read_csv(askted, low_memory=False)
    districts = df.drop_duplicates(subset="District Name")[
        ["District Name", "District Web Page Address", "District Enrollment as of Oct 2024"]
    ].copy()
    districts["enrollment"] = pd.to_numeric(districts["District Enrollment as of Oct 2024"], errors="coerce")
    districts = districts[districts["enrollment"] >= MIN_ENROLLMENT].sort_values("enrollment", ascending=False)
    return districts[["District Name", "District Web Page Address"]].values.tolist()


async def crawl_district_site(session, district_name, base_url):
    results = []

    if not base_url or not isinstance(base_url, str):
        return results

    if not base_url.startswith("http"):
        base_url = "https://" + base_url

    base_url = base_url.rstrip("/")
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    timeout = aiohttp.ClientTimeout(total=10)

    for path in PROCUREMENT_PATHS:
        url = base_url + path
        try:
            async with session.get(url, headers=headers, timeout=timeout,
                                   allow_redirects=True, ssl=False) as resp:
                if resp.status != 200:
                    continue
                html = await resp.text(errors="replace")
        except Exception:
            continue

        soup = BeautifulSoup(html, "lxml")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            combined = f"{text} {href}".lower()

            if not href.lower().endswith(".pdf"):
                continue

            has_tech = any(t in combined for t in TECH_TERMS)

            if has_tech:
                full_url = urljoin(url, href)
                results.append({
                    "source": "DistrictSite",
                    "school_name": district_name,
                    "title": text[:200] if text else href.split("/")[-1],
                    "bid_url": full_url,
                    "pdf_url": full_url,
                    "due_date": "",
                    "rfp_id": "",
                })

    return results


async def crawl_district_websites():
    print("\n" + "=" * 60)
    print("SOURCE 2: District Website Crawling")
    print("=" * 60)

    districts = load_top_districts()
    if not districts:
        print("  No district data found (data/askted.csv missing)")
        return []

    print(f"\n  Crawling {len(districts)} districts (enrollment >= {MIN_ENROLLMENT})...\n")

    all_results = []
    seen = set()
    sem = asyncio.Semaphore(DISTRICT_CRAWL_CONCURRENCY)

    async def crawl_one(name, url):
        async with sem:
            try:
                return await asyncio.wait_for(
                    crawl_district_site(session, name, url), timeout=60
                )
            except asyncio.TimeoutError:
                return []

    connector = aiohttp.TCPConnector(limit=50, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [crawl_one(name, url) for name, url in districts]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results_list):
            name = districts[i][0]
            if isinstance(result, Exception):
                continue
            if not result:
                continue
            new = 0
            for r in result:
                pdf_url = r.get("pdf_url", "")
                if pdf_url not in seen:
                    seen.add(pdf_url)
                    all_results.append(r)
                    new += 1
            if new:
                print(f"    {name}: {new} PDFs")

    print(f"\n  District crawling total: {len(all_results)} unique PDFs")
    return all_results


# ============================================================
# SOURCE 3: DDGS
# ============================================================
BROAD_QUERIES = [
    # --- A. Original core queries (student data / analytics) ---
    'Texas school district "request for proposal" "student information system" filetype:pdf',
    'Texas school district RFP "student data" platform filetype:pdf',
    'Texas school district RFP "data analytics" "student" filetype:pdf',
    'Texas K-12 RFP "data dashboard" OR "data warehouse" filetype:pdf',

    # --- B. Learning management ---
    'Texas school district RFP "learning management system" filetype:pdf',
    'Texas school RFP "LMS" OR "learning platform" filetype:pdf',
    'Texas ISD RFP "online learning" OR "virtual learning" filetype:pdf',

    # --- C. EdTech / Software ---
    'Texas school district RFP "educational technology" filetype:pdf',
    'Texas school district RFP "edtech" software filetype:pdf',
    'Texas ISD "request for proposal" software "SaaS" filetype:pdf',
    'Texas school RFP "cloud" "software" "student" filetype:pdf',

    # --- D. Assessment / Curriculum ---
    'Texas school district RFP "assessment platform" OR "assessment system" filetype:pdf',
    'Texas ISD RFP "curriculum" software OR platform filetype:pdf',
    'Texas school RFP "progress monitoring" OR "benchmark assessment" filetype:pdf',

    # --- E. School Admin / ERP ---
    'Texas school district RFP "student enrollment" OR "student records" software filetype:pdf',
    'Texas ISD RFP "school management" software OR system filetype:pdf',
    'Texas school district RFP "attendance" software OR system filetype:pdf',

    # --- F. Integration / Data ---
    'Texas school district RFP "data integration" OR "system integration" filetype:pdf',
    'Texas ISD RFP "API" OR "interoperability" student data filetype:pdf',

    # --- G. E-Rate / IT ---
    'Texas school district "E-Rate" RFP "managed services" OR "network" filetype:pdf',
    'Texas ISD RFP "technology services" OR "IT services" filetype:pdf',

    # --- H. Specific product categories ---
    'Texas school district RFP "SIS" OR "student information" software filetype:pdf',
    'Texas ISD RFP "early warning system" OR "dropout prevention" filetype:pdf',
    'Texas school RFP "data visualization" OR "reporting" student filetype:pdf',
    'Texas ISD RFP "formative assessment" OR "interim assessment" filetype:pdf',
    'Texas school RFP "parent portal" OR "family engagement" platform filetype:pdf',
    'Texas school RFP "special education" software OR platform filetype:pdf',
    'Texas ISD RFP "grading" OR "gradebook" software filetype:pdf',
    'Texas school RFP "enrollment" OR "registration" online system filetype:pdf',
    'Texas ISD RFP "professional development" platform OR system filetype:pdf',
    'Texas school RFP "RTI" OR "MTSS" software filetype:pdf',
    'Texas school RFP "college readiness" OR "career readiness" platform filetype:pdf',
    'Texas ISD RFP "ERP" OR "enterprise" school system filetype:pdf',
    'Texas school RFP "TEKS" OR "STAAR" assessment platform filetype:pdf',
    'Texas ISD RFP "cybersecurity" OR "network security" school filetype:pdf',
    'Texas school RFP "one-to-one" OR "1:1" device OR chromebook filetype:pdf',
    'Texas ISD RFP "single sign-on" OR "SSO" OR "identity management" filetype:pdf',
    'Texas school RFP "content management" OR "website" OR "CMS" filetype:pdf',

    # --- I. Procurement-term variants ---
    'Texas school district RFQ software OR technology filetype:pdf',
    'Texas ISD "invitation to bid" technology OR software filetype:pdf',
    'Texas school district RFI "student data" OR "education technology" filetype:pdf',
    'Texas school "invitation for bid" software filetype:pdf',
    'Texas ISD "competitive sealed proposal" software filetype:pdf',
    'Texas school district "request for information" "data platform" filetype:pdf',
    'Texas ISD "request for qualifications" technology OR software filetype:pdf',
    'Texas school district "bid solicitation" software filetype:pdf',

    # --- J. ESC Region queries ---
    'Texas "Region 4" school district RFP software OR technology filetype:pdf',
    'Texas "Region 10" school district RFP software OR technology filetype:pdf',
    'Texas "Region 11" school district RFP software OR technology filetype:pdf',
    'Texas "Region 13" school district RFP software OR technology filetype:pdf',
    'Texas "Region 20" school district RFP software OR technology filetype:pdf',
    'Texas "Region 1" school district RFP software OR technology filetype:pdf',
    'Texas "Region 6" school district RFP software OR technology filetype:pdf',
    'Texas "Region 7" school district RFP software OR technology filetype:pdf',
    'Texas "Region 12" school district RFP software OR technology filetype:pdf',
    'Texas "Region 19" school district RFP software OR technology filetype:pdf',

    # --- K. Big districts ---
    'Houston ISD RFP software OR technology OR "student data" filetype:pdf',
    'Dallas ISD RFP software OR technology OR "student data" filetype:pdf',
    'Austin ISD RFP software OR technology OR "student data" filetype:pdf',
    'Fort Worth ISD RFP software OR technology OR "student data" filetype:pdf',
    'San Antonio ISD RFP software OR technology OR "student data" filetype:pdf',
    'Katy ISD RFP software OR technology OR "student data" filetype:pdf',
    'Cypress-Fairbanks ISD RFP software OR technology OR "student data" filetype:pdf',
    'Northside ISD RFP software OR technology OR "student data" filetype:pdf',
    'Plano ISD RFP software OR technology OR "student data" filetype:pdf',
    'Frisco ISD RFP software OR technology OR "student data" filetype:pdf',

    # --- L. More big/mid-size districts ---
    'Fort Bend ISD RFP software OR technology filetype:pdf',
    'Conroe ISD RFP software OR technology filetype:pdf',
    'North East ISD RFP software OR technology filetype:pdf',
    'Aldine ISD RFP software OR technology filetype:pdf',
    'Arlington ISD RFP software OR technology filetype:pdf',
    'Klein ISD RFP software OR technology filetype:pdf',
    'Garland ISD RFP software OR technology filetype:pdf',
    'Humble ISD RFP software OR technology filetype:pdf',
    'El Paso ISD RFP software OR technology filetype:pdf',
    'Lewisville ISD RFP software OR technology filetype:pdf',
    'Round Rock ISD RFP software OR technology filetype:pdf',
    'Lamar CISD RFP software OR technology filetype:pdf',
    'Socorro ISD RFP software OR technology filetype:pdf',
    'Pasadena ISD RFP software OR technology filetype:pdf',
    'Killeen ISD RFP software OR technology filetype:pdf',
    'Leander ISD RFP software OR technology filetype:pdf',
    'United ISD RFP software OR technology filetype:pdf',
    'Clear Creek ISD RFP software OR technology filetype:pdf',
    'Alief ISD RFP software OR technology filetype:pdf',
    'IDEA Public Schools RFP software OR technology filetype:pdf',

    # --- M. Site-specific searches ---
    'site:k12.tx.us RFP software OR technology filetype:pdf',
    'site:boarddocs.com Texas ISD RFP technology',
    'site:govspend.com Texas school district software',
    'Texas school district procurement "bid tabulation" software filetype:pdf',
    'Texas ISD "sole source" software OR "data platform" filetype:pdf',

    # --- N. NATIONAL: Multi-state RFP queries ---
    'school district RFP "student information system" filetype:pdf',
    'school district RFP "learning management system" filetype:pdf',
    'school district RFP "data analytics" student filetype:pdf',
    'school district RFP "assessment platform" filetype:pdf',
    'school district RFP "student data" platform software filetype:pdf',
    'K-12 RFP "data dashboard" OR "data warehouse" filetype:pdf',
    'school district RFP "edtech" OR "educational technology" software filetype:pdf',
    'school district RFP "SaaS" student OR learning filetype:pdf',
    'school district RFP "data integration" student filetype:pdf',
    'school district RFP "student enrollment" software filetype:pdf',

    # --- O. National by state ---
    'California school district RFP "student information system" OR "data platform" filetype:pdf',
    'Florida school district RFP "student data" OR "assessment" software filetype:pdf',
    'New York school district RFP "learning management" OR "student data" filetype:pdf',
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

    # --- P. National big districts ---
    'Los Angeles Unified RFP software OR technology OR "student data" filetype:pdf',
    'Chicago Public Schools RFP software OR "student data" OR "data platform" filetype:pdf',
    'Miami-Dade County Public Schools RFP software OR "student data" filetype:pdf',
    'Clark County School District RFP software OR "student data" filetype:pdf',
    'Broward County Public Schools RFP software OR "student data" filetype:pdf',
    'Hillsborough County Public Schools RFP software OR "student data" filetype:pdf',
    'Orange County Public Schools RFP software OR "student data" filetype:pdf',
    'Fairfax County Public Schools RFP software OR "student data" filetype:pdf',
    'Hawaii Department of Education RFP software OR "student data" filetype:pdf',
    'Gwinnett County Public Schools RFP software OR "student data" filetype:pdf',
    'Wake County Public Schools RFP software OR "student data" filetype:pdf',
    'Charlotte-Mecklenburg Schools RFP software OR "student data" filetype:pdf',
    'Montgomery County Public Schools RFP software OR "student data" filetype:pdf',
    'Duval County Public Schools RFP software OR "student data" filetype:pdf',
    'Prince George County Public Schools RFP software OR "student data" filetype:pdf',

    # --- Q. Bid aggregator site searches ---
    'site:findrfp.com school district software OR technology',
    'site:findrfp.com school district "student data" OR "student information"',
    'site:bidnetdirect.com school district software OR "student data" filetype:pdf',
    'site:vendorregistry.com school district RFP software filetype:pdf',
    'site:bonfirehub.com school district RFP technology',
    'site:governmentnavigator.com school district software',
    'site:rfpdb.com school district software OR technology',

    # --- R. Non-PDF queries (HTML-posted RFPs) ---
    'school district "request for proposal" "student information system" 2024 OR 2025 OR 2026',
    'school district RFP "learning management system" 2024 OR 2025 OR 2026',
    'school district RFP "data analytics" "student data" 2024 OR 2025 OR 2026',
    'school district RFP "assessment platform" software 2025 OR 2026',
    'K-12 RFP "data dashboard" software 2025 OR 2026',

    # --- S. Remaining US states (35 more) ---
    'Alabama school district RFP "student data" OR "student information" software filetype:pdf',
    'Alaska school district RFP "student data" OR "technology" software filetype:pdf',
    'Arizona school district RFP "student information" OR "data platform" filetype:pdf',
    'Arkansas school district RFP "student data" OR "assessment" software filetype:pdf',
    'Connecticut school district RFP "student information" OR "edtech" filetype:pdf',
    'Delaware school district RFP "student data" OR "technology" filetype:pdf',
    'Hawaii school district RFP "student information" OR "data platform" filetype:pdf',
    'Idaho school district RFP "student data" OR "assessment" filetype:pdf',
    'Indiana school district RFP "student information" OR "edtech" software filetype:pdf',
    'Iowa school district RFP "student data" OR "assessment platform" filetype:pdf',
    'Kansas school district RFP "student data" OR "technology" software filetype:pdf',
    'Kentucky school district RFP "student information" OR "data analytics" filetype:pdf',
    'Louisiana school district RFP "student data" OR "learning management" filetype:pdf',
    'Maine school district RFP "student information" OR "technology" filetype:pdf',
    'Minnesota school district RFP "student data" OR "assessment" software filetype:pdf',
    'Mississippi school district RFP "student information" OR "edtech" filetype:pdf',
    'Missouri school district RFP "student data" OR "data platform" filetype:pdf',
    'Montana school district RFP "student information" OR "technology" filetype:pdf',
    'Nebraska school district RFP "student data" OR "assessment" filetype:pdf',
    'Nevada school district RFP "student information" OR "data platform" filetype:pdf',
    'New Hampshire school district RFP "student data" OR "technology" filetype:pdf',
    'New Mexico school district RFP "student information" OR "edtech" filetype:pdf',
    'North Dakota school district RFP "student data" OR "technology" filetype:pdf',
    'Oklahoma school district RFP "student information" OR "assessment" software filetype:pdf',
    'Oregon school district RFP "student data" OR "data platform" filetype:pdf',
    'Rhode Island school district RFP "student information" OR "technology" filetype:pdf',
    'South Carolina school district RFP "student data" OR "edtech" software filetype:pdf',
    'South Dakota school district RFP "student information" OR "technology" filetype:pdf',
    'Tennessee school district RFP "student data" OR "assessment" software filetype:pdf',
    'Utah school district RFP "student information" OR "data platform" filetype:pdf',
    'Vermont school district RFP "student data" OR "technology" filetype:pdf',
    'West Virginia school district RFP "student information" OR "edtech" filetype:pdf',
    'Wisconsin school district RFP "student data" OR "assessment platform" filetype:pdf',
    'Wyoming school district RFP "student information" OR "technology" filetype:pdf',

    # --- T. More national big districts (50+) ---
    'Denver Public Schools RFP software OR "student data" filetype:pdf',
    'Baltimore City Public Schools RFP software OR "student data" filetype:pdf',
    'Milwaukee Public Schools RFP software OR "student data" filetype:pdf',
    'Memphis-Shelby County Schools RFP software OR "student data" filetype:pdf',
    'Nashville Metro Schools RFP software OR "student data" filetype:pdf',
    'Jefferson County Public Schools RFP software OR "student data" filetype:pdf',
    'Palm Beach County Schools RFP software OR "student data" filetype:pdf',
    'Pinellas County Schools RFP software OR "student data" filetype:pdf',
    'Polk County Public Schools RFP software OR "student data" filetype:pdf',
    'DeKalb County School District RFP software OR "student data" filetype:pdf',
    'Cobb County School District RFP software OR "student data" filetype:pdf',
    'Fulton County Schools RFP software OR "student data" filetype:pdf',
    'Anne Arundel County Public Schools RFP software OR "student data" filetype:pdf',
    'Loudoun County Public Schools RFP software OR "student data" filetype:pdf',
    'Prince William County Schools RFP software OR "student data" filetype:pdf',
    'Henrico County Public Schools RFP software OR "student data" filetype:pdf',
    'Mesa Public Schools RFP software OR "student data" filetype:pdf',
    'Tucson Unified School District RFP software OR "student data" filetype:pdf',
    'Gilbert Public Schools RFP software OR "student data" filetype:pdf',
    'Chandler Unified School District RFP software OR "student data" filetype:pdf',
    'Columbus City Schools RFP software OR "student data" filetype:pdf',
    'Cleveland Metropolitan School District RFP software OR "student data" filetype:pdf',
    'Cincinnati Public Schools RFP software OR "student data" filetype:pdf',
    'Philadelphia School District RFP software OR "student data" filetype:pdf',
    'Pittsburgh Public Schools RFP software OR "student data" filetype:pdf',
    'Detroit Public Schools RFP software OR "student data" filetype:pdf',
    'Grand Rapids Public Schools RFP software OR "student data" filetype:pdf',
    'Minneapolis Public Schools RFP software OR "student data" filetype:pdf',
    'St. Paul Public Schools RFP software OR "student data" filetype:pdf',
    'Seattle Public Schools RFP software OR "student data" filetype:pdf',
    'Portland Public Schools RFP software OR "student data" filetype:pdf',
    'San Diego Unified School District RFP software OR "student data" filetype:pdf',
    'San Francisco Unified School District RFP software OR "student data" filetype:pdf',
    'Sacramento City Unified School District RFP software OR "student data" filetype:pdf',
    'Fresno Unified School District RFP software OR "student data" filetype:pdf',
    'Long Beach Unified School District RFP software OR "student data" filetype:pdf',
    'Oakland Unified School District RFP software OR "student data" filetype:pdf',
    'Boston Public Schools RFP software OR "student data" filetype:pdf',
    'Worcester Public Schools RFP software OR "student data" filetype:pdf',
    'Newark Public Schools RFP software OR "student data" filetype:pdf',
    'Indianapolis Public Schools RFP software OR "student data" filetype:pdf',
    'St. Louis Public Schools RFP software OR "student data" filetype:pdf',
    'Kansas City Public Schools RFP software OR "student data" filetype:pdf',
    'Omaha Public Schools RFP software OR "student data" filetype:pdf',
    'Atlanta Public Schools RFP software OR "student data" filetype:pdf',
    'Raleigh-Durham schools RFP software OR "student data" filetype:pdf',
    'Charlotte-Mecklenburg Schools RFP "student information system" filetype:pdf',
    'Shelby County Schools RFP "assessment" OR "data analytics" software filetype:pdf',
    'Albuquerque Public Schools RFP software OR "student data" filetype:pdf',
    'Anchorage School District RFP software OR "student data" filetype:pdf',

    # --- U. Procurement variants (national) ---
    'school district RFQ "student data" OR "student information" software filetype:pdf',
    'school district "invitation to bid" "student information" OR "data platform" filetype:pdf',
    'school district "solicitation" "student data" OR "assessment" software filetype:pdf',
    'school district RFI "data analytics" OR "student information" filetype:pdf',
    'K-12 "request for qualifications" software OR "data platform" filetype:pdf',
    'school district "competitive sealed proposal" software OR technology filetype:pdf',
    'public schools "request for proposal" "SIS" OR "student information system" filetype:pdf',
    'public schools RFP "data warehouse" OR "data integration" filetype:pdf',
    'public schools RFP "early warning" OR "dropout prevention" software filetype:pdf',
    'public schools RFP "progress monitoring" OR "benchmark assessment" filetype:pdf',

    # --- V. Edtech product/platform specific ---
    '"student information system" RFP "PowerSchool" OR "Infinite Campus" OR "Tyler SIS" filetype:pdf',
    '"learning management" RFP "Canvas" OR "Schoology" OR "Google Classroom" filetype:pdf',
    'school district RFP "Clever" OR "ClassLink" OR "single sign-on" filetype:pdf',
    'school district RFP "NWEA" OR "MAP Growth" OR "iReady" assessment filetype:pdf',
    'school district RFP "Naviance" OR "college readiness" OR "career planning" filetype:pdf',
    'school district RFP "SEL" OR "social emotional learning" platform filetype:pdf',
    'school district RFP "special education" OR "IEP" software filetype:pdf',
    'school district RFP "PBIS" OR "behavior management" software filetype:pdf',
    'school district RFP "digital curriculum" OR "online learning" platform filetype:pdf',
    'school district RFP "parent communication" OR "family engagement" app filetype:pdf',
    'school district RFP "transportation management" software OR "routing" filetype:pdf',
    'school district RFP "food service" OR "meal tracking" software filetype:pdf',
    'school district RFP "facility management" OR "work order" software filetype:pdf',
    'school district RFP "HR" OR "human resources" OR "payroll" school software filetype:pdf',
    'school district RFP "finance" OR "ERP" OR "accounting" school software filetype:pdf',

    # --- W. More aggregator and government portal searches ---
    'site:publicpurchase.com school district software OR technology',
    'site:bidsync.com school district "student data" OR software',
    'site:demandstar.com school district RFP software',
    'site:ionwave.net school district software OR technology',
    'site:govbidspec.com school district software',
    'site:opengov.com school district RFP technology',
    'site:procurenow.com school district software',
    'site:planetbids.com school district software OR technology',

    # --- X. Year-specific current RFPs ---
    'school district RFP "student information system" 2025 filetype:pdf',
    'school district RFP "learning management system" 2025 filetype:pdf',
    'school district RFP "data analytics" student 2025 filetype:pdf',
    'school district RFP "assessment platform" 2025 filetype:pdf',
    'school district RFP "student data" platform 2025 filetype:pdf',
    'school district RFP "student information system" 2026 filetype:pdf',
    'school district RFP "data analytics" student 2026 filetype:pdf',
    'school district RFP "SIS" 2025 OR 2026 filetype:pdf',
    'school district RFP "edtech" 2025 OR 2026 filetype:pdf',
    'school district RFP "cloud" "student" software 2025 OR 2026 filetype:pdf',
]


def search_ddgs():
    print("\n" + "=" * 60)
    print("SOURCE 3: DDGS (DuckDuckGo Search)")
    print("=" * 60)

    results = []
    seen = set()
    total = len(BROAD_QUERIES)
    consecutive_errors = 0

    print(f"\n  Running {total} queries...\n")

    with DDGS() as ddgs:
        for i, query in enumerate(BROAD_QUERIES):
            print(f"  [{i+1}/{total}] {query[:65]}...")
            time.sleep(random.uniform(*DDGS_SLEEP))

            for attempt in range(DDGS_MAX_RETRIES + 1):
                try:
                    search_results = list(ddgs.text(query, max_results=DDGS_MAX_RESULTS))
                    pdf_count = 0

                    is_non_pdf_query = "filetype:pdf" not in query
                    for r in search_results:
                        url = r.get("href", "")
                        if not url or url in seen:
                            continue
                        is_pdf = url.lower().endswith(".pdf")
                        if not is_pdf and not is_non_pdf_query:
                            continue
                        if not is_pdf and not quick_title_filter(r.get("title", "")):
                            continue
                        seen.add(url)
                        pdf_count += 1
                        results.append({
                            "source": "DDGS",
                            "school_name": "",
                            "title": r.get("title", ""),
                            "bid_url": url,
                            "pdf_url": url,
                            "due_date": "",
                            "rfp_id": "",
                        })

                    print(f"          Raw: {len(search_results)} | New: {pdf_count} | Total: {len(results)}")
                    consecutive_errors = 0
                    break

                except Exception as e:
                    if attempt < DDGS_MAX_RETRIES:
                        wait = DDGS_ERROR_SLEEP * (attempt + 1)
                        print(f"          Retry {attempt+1}: sleeping {wait}s...")
                        time.sleep(wait)
                    else:
                        print(f"          Failed after {DDGS_MAX_RETRIES + 1} attempts: {str(e)[:80]}")
                        consecutive_errors += 1

            if consecutive_errors >= 5:
                print(f"\n  *** 5 consecutive failures — pausing 120s ***")
                time.sleep(120)
                consecutive_errors = 0

    print(f"\n  DDGS total: {len(results)} unique PDFs")
    return results


# ============================================================
# SOURCE 4: BIDNET DIRECT
# ============================================================
async def scrape_bidnet():
    print("\n" + "=" * 60)
    print("SOURCE 4: BidNet Direct")
    print("=" * 60)

    all_results = []
    seen_urls = set()
    sem = asyncio.Semaphore(BIDNET_CONCURRENCY)
    headers = {"User-Agent": random.choice(USER_AGENTS)}

    async def fetch_page(session, keyword, page_num):
        url = f"{BIDNET_BASE}/texas/solicitations/open-bids?keywords={keyword}&page={page_num}"
        async with sem:
            try:
                timeout = aiohttp.ClientTimeout(total=15)
                async with session.get(url, headers=headers, timeout=timeout, ssl=False) as resp:
                    if resp.status != 200:
                        return []
                    html = await resp.text(errors="replace")
            except Exception:
                return []

        soup = BeautifulSoup(html, "lxml")
        bids = []

        for item in soup.select("a[href*='/solicitations/']"):
            href = item.get("href", "")
            title = item.get_text(strip=True)

            if not title or len(title) < 5:
                continue
            if "/open-bids" in href or "/closed-bids" in href:
                continue

            full_url = href if href.startswith("http") else BIDNET_BASE + href
            if full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            bids.append({
                "source": "BidNet",
                "school_name": "",
                "title": title[:200],
                "bid_url": full_url,
                "pdf_url": full_url,
                "due_date": "",
                "rfp_id": "",
            })

        return bids

    connector = aiohttp.TCPConnector(limit=20, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        for keyword in BIDNET_SEARCH_KEYWORDS:
            print(f"\n  Keyword: '{keyword}'...")

            page1_results = await fetch_page(session, keyword, 1)
            for r in page1_results:
                all_results.append(r)

            if len(page1_results) >= 20:
                page_tasks = [fetch_page(session, keyword, p) for p in range(2, 10)]
                page_results = await asyncio.gather(*page_tasks, return_exceptions=True)
                for pr in page_results:
                    if isinstance(pr, list):
                        for r in pr:
                            all_results.append(r)

            print(f"    Found {len(page1_results)}+ bids, running total: {len(all_results)}")
            await asyncio.sleep(random.uniform(1.0, 2.0))

    def bidnet_filter(title):
        t = title.lower()
        edu_words = ["school", "isd", "district", "student", "education",
                     "learning", "k-12", "k12", "academic", "teacher",
                     "classroom", "instruction", "curriculum", "assessment"]
        has_edu = any(w in t for w in edu_words)
        has_tech = quick_title_filter(title)
        return has_tech or has_edu

    filtered = [r for r in all_results if bidnet_filter(r["title"])]
    print(f"\n  BidNet total: {len(all_results)} bids, {len(filtered)} edu/tech-relevant")
    return filtered


# ============================================================
# SOURCE 5: ESC WEBSITE CRAWLING
# ============================================================
ESC_SITES = [
    ("Region 1 ESC", "https://www.esc1.net"),
    ("Region 2 ESC", "https://www.esc2.net"),
    ("Region 3 ESC", "https://www.esc3.net"),
    ("Region 4 ESC", "https://www.esc4.net"),
    ("Region 5 ESC", "https://www.esc5.net"),
    ("Region 6 ESC", "https://www.esc6.net"),
    ("Region 7 ESC", "https://www.esc7.net"),
    ("Region 8 ESC", "https://www.esc8.net"),
    ("Region 9 ESC", "https://www.esc9.net"),
    ("Region 10 ESC", "https://www.region10.org"),
    ("Region 11 ESC", "https://www.esc11.net"),
    ("Region 12 ESC", "https://www.esc12.net"),
    ("Region 13 ESC", "https://www.esc13.net"),
    ("Region 14 ESC", "https://www.esc14.net"),
    ("Region 15 ESC", "https://www.esc15.net"),
    ("Region 16 ESC", "https://www.esc16.net"),
    ("Region 17 ESC", "https://www.esc17.net"),
    ("Region 18 ESC", "https://www.esc18.net"),
    ("Region 19 ESC", "https://www.esc19.net"),
    ("Region 20 ESC", "https://www.esc20.net"),
]

ESC_PATHS = PROCUREMENT_PATHS + [
    "/purchasing-cooperative", "/bid-opportunities",
    "/vendor-opportunities", "/cooperative-purchasing",
    "/services/purchasing", "/services/cooperative-purchasing",
]


async def crawl_esc_websites():
    print("\n" + "=" * 60)
    print("SOURCE 5: ESC Website Crawling (20 regions)")
    print("=" * 60)

    all_results = []
    seen = set()
    sem = asyncio.Semaphore(10)

    async def crawl_one(session, name, base_url):
        async with sem:
            results = []
            if not base_url.startswith("http"):
                base_url = "https://" + base_url
            base_url = base_url.rstrip("/")
            headers = {"User-Agent": random.choice(USER_AGENTS)}
            timeout = aiohttp.ClientTimeout(total=10)

            for path in ESC_PATHS:
                url = base_url + path
                try:
                    async with session.get(url, headers=headers, timeout=timeout,
                                           allow_redirects=True, ssl=False) as resp:
                        if resp.status != 200:
                            continue
                        html = await resp.text(errors="replace")
                except Exception:
                    continue

                soup = BeautifulSoup(html, "lxml")
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    text = a.get_text(strip=True)
                    combined = f"{text} {href}".lower()

                    if not href.lower().endswith(".pdf"):
                        continue

                    has_tech = any(t in combined for t in TECH_TERMS)
                    if has_tech:
                        full_url = urljoin(url, href)
                        results.append({
                            "source": "ESC",
                            "school_name": name,
                            "title": text[:200] if text else href.split("/")[-1],
                            "bid_url": full_url,
                            "pdf_url": full_url,
                            "due_date": "",
                            "rfp_id": "",
                        })
            return results

    connector = aiohttp.TCPConnector(limit=20, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [crawl_one(session, name, url) for name, url in ESC_SITES]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results_list):
            name = ESC_SITES[i][0]
            if isinstance(result, Exception):
                continue
            if not result:
                continue
            new = 0
            for r in result:
                pdf_url = r.get("pdf_url", "")
                if pdf_url not in seen:
                    seen.add(pdf_url)
                    all_results.append(r)
                    new += 1
            if new:
                print(f"    {name}: {new} PDFs")

    print(f"\n  ESC crawling total: {len(all_results)} unique PDFs")
    return all_results


# ============================================================
# VERIFICATION: Parallel download + keyword, then sequential LLM
# ============================================================
def download_and_keyword_filter(row):
    """Phase 1: Download PDF, extract text, run keyword filter. Runs in parallel."""
    url = row.get("pdf_url") or row.get("bid_url", "")
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

    is_pdf = url.lower().endswith(".pdf")
    if not is_pdf:
        passed, reason = keyword_filter(f"{row.get('title', '')} {row.get('school_name', '')}")
        result["keyword_pass"] = passed
        result["keyword_reason"] = reason
        if passed:
            result["status"] = "Portal link (no PDF to verify)"
        else:
            result["status"] = f"Title keyword: {reason}"
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

        except requests.exceptions.RequestException as e:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            result["status"] = f"Download Failed: {type(e).__name__}"
        except Exception as e:
            result["status"] = f"Error: {type(e).__name__}"
            break

    return result


def verify_all(unique_leads, client):
    """Two-phase verification: parallel download+keyword, sequential LLM."""
    print(f"\nPhase 1: Downloading & keyword filtering {len(unique_leads)} leads ({PDF_VERIFY_WORKERS} workers)...\n")

    all_results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=PDF_VERIFY_WORKERS) as executor:
        futures = {executor.submit(download_and_keyword_filter, lead): i
                   for i, lead in enumerate(unique_leads)}

        done_count = 0
        for future in concurrent.futures.as_completed(futures):
            done_count += 1
            result = future.result()
            all_results.append((futures[future], result))

            if done_count % 50 == 0 or done_count == len(unique_leads):
                kw_passed = sum(1 for _, r in all_results if r["keyword_pass"])
                print(f"  [{done_count}/{len(unique_leads)}] processed, {kw_passed} keyword-passed so far")

    all_results.sort(key=lambda x: x[0])
    all_results = [r for _, r in all_results]

    keyword_passed = [r for r in all_results if r["keyword_pass"] and r["_text"]]
    print(f"\nPhase 2: LLM verification for {len(keyword_passed)} keyword-passed leads...\n")

    for i, result in enumerate(keyword_passed):
        title_preview = result.get("title", "")[:45] or result.get("bid_url", "")[:45]
        print(f"  [{i+1}/{len(keyword_passed)}] [{result['source']}] {title_preview}")

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


# ============================================================
# MAIN
# ============================================================
async def async_main():
    all_sources = ["esbd", "portals", "ddgs", "bidnet", "esc"]
    selected = [s.lower() for s in sys.argv[1:]] if len(sys.argv) > 1 else all_sources

    print("=" * 60)
    print("STRIVED COMPLETE TEXAS RFP FINDER")
    print(f"Sources: {', '.join(selected)}")
    print("=" * 60)

    all_leads = []

    if "esbd" in selected:
        esbd_results = await scrape_esbd()
        all_leads.extend(esbd_results)

    if "portals" in selected:
        portal_results = await crawl_district_websites()
        all_leads.extend(portal_results)

    if "ddgs" in selected:
        ddgs_results = search_ddgs()
        all_leads.extend(ddgs_results)

    if "bidnet" in selected:
        bidnet_results = await scrape_bidnet()
        all_leads.extend(bidnet_results)

    if "esc" in selected:
        esc_results = await crawl_esc_websites()
        all_leads.extend(esc_results)

    # Deduplicate
    seen_urls = set()
    unique_leads = []
    for lead in all_leads:
        url = lead.get("pdf_url") or lead.get("bid_url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_leads.append(lead)

    print(f"\n{'='*60}")
    print(f"COLLECTION COMPLETE")
    print(f"  Total raw leads: {len(all_leads)}")
    print(f"  After dedup:     {len(unique_leads)}")
    print(f"{'='*60}")

    if not unique_leads:
        print("No leads found. Exiting.")
        return

    # Verification
    client = Groq(api_key=GROQ_API_KEY)
    all_results = verify_all(unique_leads, client)

    # Save — merge with previous results to accumulate across runs
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    out_df = pd.DataFrame(all_results)

    if os.path.exists(OUTPUT_CSV):
        try:
            prev_df = pd.read_csv(OUTPUT_CSV)
            prev_relevant = prev_df[prev_df["is_relevant"] == True]
            new_urls = set(out_df["pdf_url"].dropna()) | set(out_df.get("bid_url", pd.Series()).dropna())
            extra = prev_relevant[~prev_relevant["pdf_url"].isin(new_urls)]
            if len(extra) > 0:
                out_df = pd.concat([out_df, extra], ignore_index=True)
                print(f"  Merged {len(extra)} previously-found relevant RFPs from prior runs")
        except Exception:
            pass

    save_path = OUTPUT_CSV
    try:
        out_df.to_csv(save_path, index=False)
    except PermissionError:
        save_path = OUTPUT_CSV.replace(".csv", f"_{int(time.time())}.csv")
        out_df.to_csv(save_path, index=False)
        print(f"  (Primary file locked, saved to {save_path})")

    relevant = out_df[out_df["is_relevant"] == True]

    print(f"\n{'='*60}")
    print(f"FINAL RESULTS")
    print(f"{'='*60}")
    print(f"  Total leads collected:  {len(unique_leads)}")
    print(f"  Keyword passed:         {out_df['keyword_pass'].sum()}")
    print(f"  RELEVANT to Strived:    {len(relevant)}")
    print(f"{'='*60}")

    if len(relevant) > 0:
        print(f"\nRelevant RFPs:")
        for _, r in relevant.iterrows():
            print(f"  [{r['source']}] {r['school_name']}")
            print(f"    Title: {r['title'][:80]}")
            print(f"    Score: {r.get('llm_score', 'N/A')}/10 | {r.get('llm_summary', '')}")
            print(f"    URL:   {r.get('bid_url', r.get('pdf_url', ''))}")
            print(f"    Email: {r['emails'] if r['emails'] else 'None'}")
            print()

    print(f"Saved to {save_path}")


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
