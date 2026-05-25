"""
strived_round3.py — Round 3 RFP Collection

Two approaches:
  1. 120 NEW DDGS queries with completely different phrasings
  2. Aggressive Texas district crawling (ALL 1219 districts, ALL PDFs)

Merges relevant results into STRIVED_COMPLETE_TEXAS.csv.

Usage:
    python strived_round3.py              # both sources
    python strived_round3.py ddgs         # DDGS only
    python strived_round3.py districts    # district crawl only
"""

import asyncio
import concurrent.futures
import io
import os
import random
import re
import sys
import time
from urllib.parse import urljoin, urlparse

import aiohttp
import pandas as pd
import requests
from bs4 import BeautifulSoup
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
ROUND3_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ROUND3.csv")

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


# ============================================================
# NEW DDGS QUERIES — completely different phrasings
# ============================================================
QUERIES = [
    # --- Bid/IFB/RFI terminology (not just RFP) ---
    '"invitation for bid" school district "student information" filetype:pdf',
    '"request for information" school district "data platform" filetype:pdf',
    '"request for interest" school district edtech filetype:pdf',
    '"letter of interest" school district "student data" software filetype:pdf',
    '"sole source" school district "student information system" filetype:pdf',
    '"competitive sealed proposal" school district software technology filetype:pdf',
    'school district "notice of intent" "student information" OR "SIS" filetype:pdf',
    '"bid tabulation" school district "student information" OR "SIS" filetype:pdf',
    '"contract award" school district "student data" OR "data analytics" filetype:pdf',
    '"vendor selection" school district "student information" software filetype:pdf',

    # --- Scope of work / requirements docs ---
    '"scope of work" school district "student information system" filetype:pdf',
    '"scope of services" school district "data analytics" OR "data platform" filetype:pdf',
    '"functional requirements" school district "student information" filetype:pdf',
    '"technical requirements" school district "student data" platform filetype:pdf',
    '"system requirements" school district "SIS" OR "student information" filetype:pdf',
    '"statement of work" school district "data warehouse" OR "SIS" filetype:pdf',
    '"evaluation criteria" school district "student information" software filetype:pdf',
    '"vendor questionnaire" school district "student data" OR technology filetype:pdf',

    # --- ESSA / Title I / federal funding ---
    'school district "ESSA" "evidence-based" technology RFP filetype:pdf',
    '"Title I" school district RFP "data system" OR software filetype:pdf',
    '"Title IV" school district technology "request for proposal" filetype:pdf',
    '"ESSER" school district RFP technology OR "data platform" filetype:pdf',
    '"ESSER funds" school district software procurement filetype:pdf',
    '"ARP ESSER" school district technology OR software RFP filetype:pdf',

    # --- Specific functional areas not covered ---
    'school district RFP "chronic absenteeism" software OR system filetype:pdf',
    'school district RFP "truancy" tracking software OR system filetype:pdf',
    'school district RFP "English learner" OR "ELL" software filetype:pdf',
    'school district RFP "gifted and talented" tracking software filetype:pdf',
    'school district RFP "504 plan" OR "Section 504" software filetype:pdf',
    'school district RFP "transcript" OR "records management" software filetype:pdf',
    'school district RFP "school improvement" data software filetype:pdf',
    'school district RFP "accountability" reporting software filetype:pdf',
    'school district RFP "state reporting" software OR platform filetype:pdf',
    'school district RFP "compliance reporting" student data filetype:pdf',
    'school district RFP "master schedule" OR "scheduling" software filetype:pdf',
    'school district RFP "course catalog" OR "registration" student filetype:pdf',

    # --- Data interoperability ---
    'school district RFP "Ed-Fi" data filetype:pdf',
    'school district RFP "SIF" OR "Schools Interoperability" filetype:pdf',
    'school district RFP "data interoperability" OR "data exchange" filetype:pdf',
    'school district RFP "API integration" student data filetype:pdf',
    'school district RFP "roster" OR "rostering" "OneRoster" filetype:pdf',

    # --- Multi-tiered support ---
    'school district RFP "tiered intervention" OR "RTI" software filetype:pdf',
    'school district RFP "multi-tiered" support system software filetype:pdf',
    'school district RFP "intervention tracking" student filetype:pdf',
    'school district RFP "progress monitoring" student software filetype:pdf',
    'school district RFP "universal screening" student filetype:pdf',

    # --- Alternative document types ---
    'school district RFP "student data" software filetype:doc',
    'school district RFP "student information system" filetype:docx',
    'school district RFP "data platform" student filetype:doc',
    '"school district" procurement "data analytics" student filetype:doc',
    'school district RFP "assessment" platform filetype:docx',

    # --- State DOE site-specific searches ---
    'site:tea.texas.gov RFP software OR technology OR "student data"',
    'site:doe.state.fl.us RFP software OR "student data"',
    'site:cde.ca.gov RFP "student information" OR software',
    'site:nysed.gov RFP "student data" OR software',
    'site:isbe.net RFP "student data" OR software',
    'site:education.ohio.gov RFP "student data" OR software',
    'site:doe.virginia.gov RFP "student data" OR software',
    'site:education.pa.gov RFP "student data" OR software',
    'site:gadoe.org RFP "student data" OR software',

    # --- Large metro areas not individually searched ---
    'Nashville public schools RFP "student information" OR "data" software filetype:pdf',
    'Austin ISD RFP "student data" OR "assessment" software filetype:pdf',
    'San Antonio ISD RFP "student data" OR "data analytics" filetype:pdf',
    'Northside ISD RFP software OR technology OR "student data" filetype:pdf',
    'Cypress-Fairbanks ISD RFP software OR technology filetype:pdf',
    'Katy ISD RFP software OR technology OR "student data" filetype:pdf',
    'Fort Bend ISD RFP software OR technology filetype:pdf',
    'Arlington ISD RFP software OR technology filetype:pdf',
    'Garland ISD RFP software OR technology filetype:pdf',
    'Plano ISD RFP software OR technology filetype:pdf',
    'Frisco ISD RFP software OR technology filetype:pdf',
    'McKinney ISD RFP software OR technology filetype:pdf',
    'Mansfield ISD RFP software OR technology filetype:pdf',
    'Lewisville ISD RFP software OR technology filetype:pdf',
    'Denton ISD RFP software OR technology filetype:pdf',
    'Midland ISD RFP software OR technology filetype:pdf',
    'Amarillo ISD RFP software OR technology filetype:pdf',
    'Lubbock ISD RFP software OR technology filetype:pdf',
    'El Paso ISD RFP software OR technology filetype:pdf',
    'Socorro ISD RFP software OR technology filetype:pdf',
    'Ysleta ISD RFP software OR technology filetype:pdf',

    # --- Substitute "RFP" with different terms ---
    'school district procurement "student information system" 2024 2025 filetype:pdf',
    'school district solicitation "data analytics" student filetype:pdf',
    'school district "competitive bid" software "student data" filetype:pdf',
    'school district "purchase order" "student information" software filetype:pdf',
    '"board approved" school district "student information" software contract filetype:pdf',
    '"board agenda" school district "student information system" contract filetype:pdf',
    '"board minutes" school district "student information" software award filetype:pdf',

    # --- Vendor/product evaluation ---
    '"vendor demonstration" school district "student information" filetype:pdf',
    '"product evaluation" school district "student data" software filetype:pdf',
    '"proof of concept" school district "student information" filetype:pdf',
    '"pilot program" school district "student data" platform filetype:pdf',
    '"technology review" school district "student information" filetype:pdf',

    # --- Budget/appropriation docs that mention RFPs ---
    '"technology budget" school district "student information" OR "SIS" filetype:pdf',
    '"capital improvement" school district technology "student data" filetype:pdf',
    '"technology plan" school district "student information system" procurement filetype:pdf',

    # --- Regional specific (not state-wide) ---
    '"metro Nashville" public schools RFP technology filetype:pdf',
    '"Prince William County" schools RFP technology OR software filetype:pdf',
    '"Anne Arundel County" schools RFP technology OR software filetype:pdf',
    '"Howard County" public schools RFP technology OR software filetype:pdf',
    '"Fulton County" schools RFP technology OR software filetype:pdf',
    '"Hamilton County" schools RFP technology OR software filetype:pdf',
    '"Knox County" schools RFP technology OR software filetype:pdf',
    '"Brevard County" schools RFP technology OR software filetype:pdf',
    '"Seminole County" schools RFP technology OR software filetype:pdf',
    '"Osceola County" schools RFP technology OR software filetype:pdf',

    # --- Searches without "filetype:pdf" to catch HTML RFPs ---
    'school district RFP "student information system" 2025 2026',
    'school district "request for proposal" "data analytics platform" 2025',
    '"ISD" "request for proposal" "student data" software 2025',
    '"school district" "request for proposals" edtech platform 2025',
    'K-12 RFP "student information system" replacement 2025',
    '"public schools" RFP "student data platform" 2025',

    # --- Specific platform searches ---
    'site:boarddocs.com "request for proposal" software technology',
    'site:boarddocs.com RFP "student information" OR "data analytics"',
    'site:demandstar.com school district software OR technology',
    'site:bidsync.com school district "student data" OR software',
    'site:publicpurchase.com school district technology',
    'site:ciplist.com school district software OR technology',
]


def load_existing_urls():
    urls = set()
    if os.path.exists(MAIN_CSV):
        df = pd.read_csv(MAIN_CSV)
        urls.update(df["pdf_url"].dropna().tolist())
    if os.path.exists(ROUND3_CSV):
        df = pd.read_csv(ROUND3_CSV)
        urls.update(df["pdf_url"].dropna().tolist())
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
    print("ROUND 3: DDGS Queries (120 new queries)")
    print("=" * 60)

    existing = load_existing_urls()
    print(f"  Existing URLs to skip: {len(existing)}")

    all_leads = []
    seen = set(existing)
    total_new = 0

    timelimits = [None, 'y']

    for tl_idx, timelimit in enumerate(timelimits):
        tl_name = timelimit or "all-time"
        print(f"\n  Pass {tl_idx+1}: timelimit={tl_name}")

        for i, query in enumerate(QUERIES):
            print(f"    [{i+1}/{len(QUERIES)}] {query[:70]}...", end=" ", flush=True)
            results = ddgs_search(query, timelimit=timelimit)

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
                    "source": "DDGS-Round3",
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

    print(f"\n  DDGS Round 3 total: {total_new} new leads")
    return all_leads


# ============================================================
# AGGRESSIVE DISTRICT CRAWLING
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
    "/purchasing/current-bids", "/finance/bids",
    "/departments/purchasing/bids", "/procurement/bids",
    "/bid-opportunities", "/open-bids", "/active-bids",
    "/for-vendors", "/vendor", "/doing-business",
    "/departments/finance", "/business-office",
]


async def crawl_district_all_pdfs(session, district_name, base_url):
    results = []
    if not base_url or not isinstance(base_url, str):
        return results
    if not base_url.startswith("http"):
        base_url = "https://" + base_url
    base_url = base_url.rstrip("/")
    headers = {"User-Agent": random.choice(USER_AGENTS)}
    timeout = aiohttp.ClientTimeout(total=8)

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
            if not href.lower().endswith(".pdf"):
                continue
            full_url = urljoin(url, href)
            text = a.get_text(strip=True)
            results.append({
                "source": "DistrictCrawl-R3",
                "school_name": district_name,
                "title": text[:200] if text else href.split("/")[-1],
                "bid_url": full_url,
                "pdf_url": full_url,
                "due_date": "",
                "rfp_id": "",
            })

    return results


async def crawl_all_districts():
    print("\n" + "=" * 60)
    print("ROUND 3: Aggressive District Crawling (ALL districts)")
    print("=" * 60)

    askted = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "askted.csv")
    if not os.path.exists(askted):
        print("  data/askted.csv not found")
        return []

    df = pd.read_csv(askted, low_memory=False)
    districts = df.drop_duplicates(subset="District Name")[
        ["District Name", "District Web Page Address"]
    ].copy()
    districts = districts[districts["District Web Page Address"].notna() &
                          (districts["District Web Page Address"].str.len() > 5)]

    existing = load_existing_urls()
    district_list = districts.values.tolist()
    print(f"  Districts to crawl: {len(district_list)}")
    print(f"  Existing URLs to skip: {len(existing)}")

    all_results = []
    seen = set(existing)
    found_count = 0
    batch_size = 100

    for batch_start in range(0, len(district_list), batch_size):
        batch = district_list[batch_start:batch_start + batch_size]
        batch_end = min(batch_start + batch_size, len(district_list))
        print(f"\n  Batch {batch_start//batch_size + 1}: districts {batch_start+1}-{batch_end}", flush=True)

        sem = asyncio.Semaphore(30)

        async def crawl_one(session, name, url):
            async with sem:
                try:
                    return await asyncio.wait_for(
                        crawl_district_all_pdfs(session, name, url), timeout=30
                    )
                except asyncio.TimeoutError:
                    return []

        connector = aiohttp.TCPConnector(limit=50, force_close=True)
        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [crawl_one(session, name, url) for name, url in batch]
            results_list = await asyncio.gather(*tasks, return_exceptions=True)

            batch_new = 0
            for i, result in enumerate(results_list):
                name = batch[i][0]
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
                    found_count += new
                    batch_new += new
                    print(f"    {name}: {new} PDFs", flush=True)

            print(f"    Batch done: {batch_new} new PDFs (total: {found_count})", flush=True)

    print(f"\n  District crawling total: {len(all_results)} new PDFs from {len(district_list)} districts")
    return all_results


# ============================================================
# VERIFICATION
# ============================================================
def download_and_filter(row):
    url = row.get("pdf_url", "")
    if not url:
        return None

    is_pdf = url.lower().endswith(".pdf") or row.get("is_pdf", False)
    if not is_pdf:
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
    out_df.to_csv(ROUND3_CSV, index=False)
    print(f"\n  Saved {len(out_df)} leads to {ROUND3_CSV}")

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
    elif len(new_relevant) > 0:
        new_relevant.to_csv(MAIN_CSV, index=False)
        print(f"  Created {MAIN_CSV} with {len(new_relevant)} relevant leads")

    rel_count = len(new_relevant) if len(new_relevant) > 0 else 0
    print(f"\n  ROUND 3 SUMMARY: {len(verified)} verified, {rel_count} relevant")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    print(f"\n{'#' * 60}")
    print(f"  STRIVED Round 3 RFP Collection — mode: {mode}")
    print(f"{'#' * 60}")

    all_leads = []

    if mode in ("all", "ddgs"):
        ddgs_leads = run_ddgs_queries()
        all_leads.extend(ddgs_leads)

    if mode in ("all", "districts"):
        district_leads = asyncio.run(crawl_all_districts())
        all_leads.extend(district_leads)

    if all_leads:
        verified = verify_leads(all_leads)
        save_and_merge(verified)
    else:
        print("\n  No leads found.")

    print(f"\n{'#' * 60}")
    print(f"  DONE")
    print(f"{'#' * 60}")


if __name__ == "__main__":
    main()
