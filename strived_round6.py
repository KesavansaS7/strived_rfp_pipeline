"""
strived_round6.py — Round 6 RFP Collection (massive expansion)

250+ new DDGS queries targeting completely untapped areas:
  - 50 more US school districts (mid-size, suburban)
  - State-level DOE/agency procurement portals
  - Specific edtech procurement categories
  - Canadian school boards
  - International schools
  - Different terminology and document types
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
ROUND6_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ROUND6.csv")
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
        return False, f"Negative dominant"
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
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"Analyze this RFP:\n\n{truncated}"}],
        model=GROQ_MODEL, temperature=0.0, max_tokens=100,
    )
    reply = chat.choices[0].message.content.strip()
    result = {"llm_software": "", "llm_education": "", "llm_strived_fit": "", "llm_score": 0, "llm_summary": ""}
    for line in reply.split("\n"):
        line = line.strip()
        if line.startswith("SOFTWARE:"): result["llm_software"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("EDUCATION:"): result["llm_education"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("STRIVED_FIT:"): result["llm_strived_fit"] = line.split(":", 1)[1].strip().upper()
        elif line.startswith("SCORE:"):
            try: result["llm_score"] = int(line.split(":", 1)[1].strip())
            except ValueError: result["llm_score"] = 0
        elif line.startswith("SUMMARY:"): result["llm_summary"] = line.split(":", 1)[1].strip()
    return result

QUERIES = [
    # === A: Suburban/mid-size districts not yet searched (50) ===
    'Chandler Unified School District RFP software OR technology filetype:pdf',
    'Gilbert Public Schools RFP software OR technology filetype:pdf',
    'Scottsdale Unified School District RFP software OR technology filetype:pdf',
    'Tempe Union High School District RFP software filetype:pdf',
    'Peoria Unified School District RFP software OR technology filetype:pdf',
    'Paradise Valley Unified School District RFP software filetype:pdf',
    'Deer Valley Unified School District RFP software filetype:pdf',
    'Kyrene School District RFP software OR technology filetype:pdf',
    'Washoe County School District RFP software OR technology filetype:pdf',
    'Douglas County School District RFP software filetype:pdf',
    'Cherry Creek School District RFP software OR technology filetype:pdf',
    'Jefferson County Schools Colorado RFP software filetype:pdf',
    'Adams 12 School District RFP software OR technology filetype:pdf',
    'Aurora Public Schools RFP software OR technology filetype:pdf',
    'St. Vrain Valley School District RFP software filetype:pdf',
    'Poudre School District RFP software OR technology filetype:pdf',
    'Academy District 20 RFP software OR technology filetype:pdf',
    'Boulder Valley School District RFP software filetype:pdf',
    'Northshore School District RFP software OR technology filetype:pdf',
    'Lake Washington School District RFP software filetype:pdf',
    'Bellevue School District RFP software OR technology filetype:pdf',
    'Kent School District RFP software OR technology filetype:pdf',
    'Federal Way Public Schools RFP software filetype:pdf',
    'Tacoma Public Schools RFP software OR technology filetype:pdf',
    'Spokane Public Schools RFP software OR technology filetype:pdf',
    'Bethel School District RFP software OR technology filetype:pdf',
    'Clover Park School District RFP software filetype:pdf',
    'Beaverton School District RFP software OR technology filetype:pdf',
    'Salem-Keizer School District RFP software filetype:pdf',
    'Eugene School District RFP software OR technology filetype:pdf',
    'Hillsboro School District RFP software OR technology filetype:pdf',
    'Tigard-Tualatin School District RFP software filetype:pdf',
    'North Clackamas School District RFP software filetype:pdf',
    'Elk Grove Unified School District RFP software filetype:pdf',
    'Capistrano Unified School District RFP software filetype:pdf',
    'Irvine Unified School District RFP software filetype:pdf',
    'Poway Unified School District RFP software filetype:pdf',
    'Grossmont Union High School District RFP software filetype:pdf',
    'Vista Unified School District RFP software filetype:pdf',
    'Oceanside Unified School District RFP software filetype:pdf',
    'Clovis Unified School District RFP software filetype:pdf',
    'Visalia Unified School District RFP software filetype:pdf',
    'Stockton Unified School District RFP software filetype:pdf',
    'Modesto City Schools RFP software OR technology filetype:pdf',
    'Lodi Unified School District RFP software filetype:pdf',
    'Tracy Unified School District RFP software filetype:pdf',
    'Manteca Unified School District RFP software filetype:pdf',
    'Roseville Joint Union High School District RFP software filetype:pdf',
    'Rocklin Unified School District RFP software filetype:pdf',
    'Placer Union High School District RFP software filetype:pdf',

    # === B: Completely new product/service categories (25) ===
    'school district RFP "student records" management system filetype:pdf',
    'school district RFP "transcript" management system filetype:pdf',
    'school district RFP "document management" student records filetype:pdf',
    'school district RFP "student health" records management filetype:pdf',
    'school district RFP "immunization tracking" software filetype:pdf',
    'school district RFP "school nurse" software OR system filetype:pdf',
    'school district RFP "incident reporting" student safety filetype:pdf',
    'school district RFP "anonymous reporting" OR "tip line" filetype:pdf',
    'school district RFP "student survey" platform filetype:pdf',
    'school district RFP "climate survey" school platform filetype:pdf',
    'school district RFP "perception survey" education filetype:pdf',
    'school district RFP "parent communication" platform filetype:pdf',
    'school district RFP "notification system" school filetype:pdf',
    'school district RFP "mass notification" school safety filetype:pdf',
    'school district RFP "website" OR "content management" school filetype:pdf',
    'school district RFP "mobile app" student OR parent filetype:pdf',
    'school district RFP "digital signage" school filetype:pdf',
    'school district RFP "media management" school filetype:pdf',
    'school district RFP "asset management" technology school filetype:pdf',
    'school district RFP "device management" OR "MDM" school filetype:pdf',
    'school district RFP "content filtering" school filetype:pdf',
    'school district RFP "internet filtering" school filetype:pdf',
    'school district RFP "e-rate" technology school filetype:pdf',
    'school district RFP "managed print" school filetype:pdf',
    'school district RFP "cloud backup" school data filetype:pdf',

    # === C: University/higher ed adjacent (K-20 data) (10) ===
    '"K-20" data system RFP OR procurement education filetype:pdf',
    '"pre-K through 12" data system RFP filetype:pdf',
    '"birth through grade 12" data system RFP filetype:pdf',
    '"early childhood" data system RFP OR procurement filetype:pdf',
    '"preschool" data system RFP school district filetype:pdf',
    '"Head Start" "management information system" RFP filetype:pdf',
    '"early learning" data platform RFP OR procurement filetype:pdf',
    '"child care" data system RFP state OR county filetype:pdf',
    '"after school program" data management RFP filetype:pdf',
    '"21st century" community learning center data RFP filetype:pdf',

    # === D: More state-specific with different terms (20) ===
    'Texas "education agency" procurement software technology filetype:pdf',
    'California "department of education" procurement edtech filetype:pdf',
    'Florida "department of education" procurement software filetype:pdf',
    'New York "education department" procurement technology filetype:pdf',
    'Illinois "state board of education" procurement software filetype:pdf',
    'Ohio "department of education" procurement technology filetype:pdf',
    'Pennsylvania "department of education" procurement software filetype:pdf',
    'Georgia "department of education" procurement technology filetype:pdf',
    'Michigan "department of education" procurement software filetype:pdf',
    'Virginia "department of education" procurement technology filetype:pdf',
    'North Carolina "department of instruction" procurement software filetype:pdf',
    'New Jersey "department of education" procurement technology filetype:pdf',
    'Massachusetts "department of education" procurement software filetype:pdf',
    'Maryland "department of education" procurement technology filetype:pdf',
    'Indiana "department of education" procurement software filetype:pdf',
    'Tennessee "department of education" procurement technology filetype:pdf',
    'Washington "superintendent of public instruction" procurement software filetype:pdf',
    'Colorado "department of education" procurement technology filetype:pdf',
    'Minnesota "department of education" procurement software filetype:pdf',
    'Wisconsin "department of instruction" procurement technology filetype:pdf',

    # === E: Specific competitive processes (20) ===
    '"evaluation matrix" school district "student information" filetype:pdf',
    '"scoring matrix" school district software technology filetype:pdf',
    '"weighted criteria" school district RFP software filetype:pdf',
    '"best value" school district RFP software technology filetype:pdf',
    '"lowest responsive" school district bid technology filetype:pdf',
    '"notice of award" school district "student information" filetype:pdf',
    '"intent to award" school district software technology filetype:pdf',
    '"recommendation of award" school district software filetype:pdf',
    '"vendor response" school district "student information" filetype:pdf',
    '"proposal response" school district "student data" filetype:pdf',
    '"pre-proposal conference" school district software filetype:pdf',
    '"pre-bid meeting" school district technology filetype:pdf',
    '"addendum" school district RFP "student information" filetype:pdf',
    '"amendment" school district RFP software technology filetype:pdf',
    '"questions and answers" school district RFP software filetype:pdf',
    '"Q&A" school district RFP "student information" filetype:pdf',
    '"vendor presentation" school district "student data" filetype:pdf',
    '"demo day" school district software technology filetype:pdf',
    '"finalist" school district "student information" software filetype:pdf',
    '"shortlist" school district RFP software technology filetype:pdf',

    # === F: Federal grants requiring tech procurement (15) ===
    '"Race to the Top" technology procurement school filetype:pdf',
    '"School Improvement Grant" technology RFP filetype:pdf',
    '"Title II" technology professional development RFP filetype:pdf',
    '"E-Rate" "student data" OR "student information" RFP filetype:pdf',
    '"Perkins" grant technology RFP school filetype:pdf',
    '"IDEA" grant technology RFP "special education" filetype:pdf',
    '"McKinney-Vento" technology OR software school filetype:pdf',
    '"magnet school" grant technology RFP filetype:pdf',
    '"charter school" grant technology RFP filetype:pdf',
    '"turnaround" school technology RFP filetype:pdf',
    '"priority school" technology procurement RFP filetype:pdf',
    '"focus school" technology procurement RFP filetype:pdf',
    '"comprehensive support" school technology RFP filetype:pdf',
    '"targeted support" school technology RFP filetype:pdf',
    '"school redesign" technology platform RFP filetype:pdf',

    # === G: More procurement platforms and aggregators (15) ===
    'site:naspovaluepoint.org school software technology',
    'site:savvybuyer.com school district software',
    'site:txsmartbuy.gov school software technology',
    'site:purchasing.idaho.gov education software',
    'site:das.ohio.gov education software technology',
    'site:doa.alaska.gov education software technology',
    'site:bid4michigan.com school software',
    'site:ebid.nc.gov school software technology',
    'site:vendorlink.state.fl.us school software',
    'site:procurement.virginia.gov education software',
    'site:purchase.state.mn.us education software',
    'site:osp.state.nc.us education software technology',
    'site:dms.myflorida.com education software technology',
    'site:idoa.in.gov education software technology',
    'site:comptroller.tn.gov education software technology',

    # === H: Alternative terminology (20) ===
    '"educational management" system RFP OR procurement filetype:pdf',
    '"school management system" RFP OR procurement filetype:pdf',
    '"student management system" RFP OR procurement filetype:pdf',
    '"pupil management" system RFP school filetype:pdf',
    '"education management information" system RFP filetype:pdf',
    '"EMIS" RFP school district software filetype:pdf',
    '"school ERP" RFP OR procurement filetype:pdf',
    '"education ERP" RFP OR procurement filetype:pdf',
    '"classroom management" software RFP school district filetype:pdf',
    '"instruction management" system RFP school filetype:pdf',
    '"curriculum management" system RFP school district filetype:pdf',
    '"academic management" system RFP school filetype:pdf',
    '"enrollment management" system RFP school district filetype:pdf',
    '"registration system" RFP school district student filetype:pdf',
    '"admissions system" RFP school district filetype:pdf',
    '"reporting system" RFP school district student data filetype:pdf',
    '"analytics platform" RFP school district student filetype:pdf',
    '"data visualization" RFP school district student filetype:pdf',
    '"business intelligence" school district RFP student filetype:pdf',
    '"decision support" system RFP school district filetype:pdf',

    # === I: Canadian school boards (10) ===
    '"school board" RFP "student information" Canada filetype:pdf',
    '"district school board" RFP software OR technology Ontario filetype:pdf',
    '"school division" RFP software technology Saskatchewan filetype:pdf',
    '"school district" RFP software technology "British Columbia" filetype:pdf',
    '"school board" RFP "student data" Alberta filetype:pdf',
    '"conseil scolaire" RFP software technology filetype:pdf',
    'Toronto District School Board RFP technology OR software filetype:pdf',
    'Peel District School Board RFP technology OR software filetype:pdf',
    'York Region District School Board RFP technology filetype:pdf',
    'Ottawa-Carleton District School Board RFP technology filetype:pdf',

    # === J: Doc/docx format catches (15) ===
    '"school district" RFP "student records" system filetype:doc',
    '"school district" RFP "enrollment management" filetype:doc',
    '"school district" procurement "data integration" filetype:doc',
    '"school district" RFP "student portal" filetype:doc',
    '"school district" RFP "parent portal" filetype:doc',
    '"school district" RFP "reporting system" student filetype:doc',
    '"school district" RFP "scheduling" software filetype:doc',
    '"school district" bid "assessment" software filetype:doc',
    '"school board" RFP "student information" filetype:docx',
    '"public schools" RFP "student data" platform filetype:docx',
    'K-12 RFP "early warning" system filetype:doc',
    'K-12 RFP "progress monitoring" software filetype:doc',
    'K-12 RFP "MTSS" platform filetype:docx',
    '"county schools" RFP technology software filetype:doc',
    '"parish schools" RFP technology software filetype:doc',
]

def load_existing_urls():
    urls = set()
    for f in ["STRIVED_COMPLETE_TEXAS.csv", "STRIVED_ROUND3.csv", "STRIVED_ROUND4.csv", "STRIVED_ROUND5.csv", "STRIVED_ROUND6.csv", "STRIVED_ACCUMULATED.csv"]:
        path = os.path.join(OUTPUT_DIR, f)
        if os.path.exists(path):
            try:
                df = pd.read_csv(path)
                urls.update(df["pdf_url"].dropna().tolist())
            except: pass
    return urls

def ddgs_search(query, timelimit=None):
    for attempt in range(DDGS_MAX_RETRIES):
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=DDGS_MAX_RESULTS, timelimit=timelimit))
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

def download_and_filter(row):
    url = row.get("pdf_url", "")
    if not url or not url.lower().endswith(".pdf"): return None
    try:
        headers = {"User-Agent": random.choice(USER_AGENTS)}
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        if resp.status_code != 200: return None
        content = resp.content
        if len(content) < 1000 or len(content) > 50_000_000: return None
        reader = PdfReader(io.BytesIO(content))
        text = ""
        for pg in reader.pages[:15]:
            text += pg.extract_text() or ""
            if len(text) > 20000: break
        if len(text) < 200: return None
        passed, reason = keyword_filter(text)
        emails = list(set(re.findall(EMAIL_REGEX, text)))
        return {"url": url, "text": text, "passed": passed, "reason": reason, "emails": "; ".join(emails[:5]), "row": row}
    except: return None

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
            if result and result["passed"]: passed.append(result)
            if done % 50 == 0:
                print(f"    ... {done}/{len(pdf_leads)} processed, {len(passed)} passed", flush=True)
    print(f"  Keyword filter: {len(passed)} passed out of {len(pdf_leads)}")
    if not passed or not GROQ_API_KEY:
        if not GROQ_API_KEY: print("  WARNING: No GROQ_API_KEY")
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
            if "rate_limit" in str(e).lower(): time.sleep(15)
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
    out_df.to_csv(ROUND6_CSV, index=False)
    print(f"\n  Saved {len(out_df)} leads to {ROUND6_CSV}")
    new_relevant = out_df[(out_df.get("is_relevant", False) == True) & (out_df.get("llm_score", 0) >= 5)]
    if os.path.exists(MAIN_CSV) and len(new_relevant) > 0:
        main_df = pd.read_csv(MAIN_CSV)
        main_urls = set(main_df["pdf_url"].dropna())
        to_add = new_relevant[~new_relevant["pdf_url"].isin(main_urls)]
        if len(to_add) > 0:
            for col in main_df.columns:
                if col not in to_add.columns: to_add[col] = ""
            main_df = pd.concat([main_df, to_add[main_df.columns]], ignore_index=True)
            main_df.to_csv(MAIN_CSV, index=False)
            print(f"  Merged {len(to_add)} new relevant leads into {MAIN_CSV}")
            print(f"  Main CSV now has {len(main_df)} total leads")
        else: print("  No new unique relevant leads to merge")
    rel_count = len(new_relevant)
    print(f"\n  ROUND 6 SUMMARY: {len(verified)} verified, {rel_count} relevant")

def main():
    print(f"\n{'#' * 60}")
    print(f"  STRIVED Round 6 RFP Collection ({len(QUERIES)} queries)")
    print(f"{'#' * 60}")
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
            if not url or url in seen: continue
            seen.add(url)
            title = r.get("title", "")
            body = r.get("body", "")
            school_match = re.search(
                r"([\w\s\.'-]+(?:ISD|CISD|School District|Public Schools|County Schools|Parish Schools|Academy|Board of Education|School Board))",
                f"{title} {body}", re.I)
            all_leads.append({
                "source": "DDGS-Round6", "school_name": school_match.group(1).strip() if school_match else "",
                "title": title[:200], "bid_url": url, "pdf_url": url, "due_date": "", "rfp_id": "",
            })
            new_count += 1
        total_new += new_count
        print(f"{len(results)} results, {new_count} new")
        time.sleep(random.uniform(*DDGS_SLEEP))
    print(f"\n  DDGS Round 6 total: {total_new} new leads")
    if all_leads:
        verified = verify_leads(all_leads)
        save_and_merge(verified)
    else: print("\n  No leads found.")
    print(f"\n{'#' * 60}")
    print(f"  DONE")
    print(f"{'#' * 60}")

if __name__ == "__main__":
    main()
