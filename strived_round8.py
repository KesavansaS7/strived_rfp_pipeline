"""
strived_round8.py — Round 8 RFP Collection

250 new DDGS queries targeting final untapped areas:
  - Large city school districts (top 100 US cities)
  - Specific edtech product procurement (LMS, SIS, assessment)
  - International (UK, Australia, New Zealand)
  - Private/independent school associations
  - University-managed lab schools
  - County offices of education
  - Tribal schools
  - DOD/DoDEA schools
  - Library/media center tech
  - Finance/HR systems adjacent to student data
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
ROUND8_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ROUND8.csv")
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
    # === A: Top US city school districts (30) ===
    'Chicago Public Schools RFP software technology student filetype:pdf',
    'Los Angeles Unified School District RFP software technology filetype:pdf',
    'New York City Department of Education RFP software technology filetype:pdf',
    'Houston Independent School District RFP software technology filetype:pdf',
    'Miami-Dade County Public Schools RFP software technology filetype:pdf',
    'Broward County Public Schools RFP software technology filetype:pdf',
    'Hillsborough County Public Schools RFP software technology filetype:pdf',
    'Orange County Public Schools Florida RFP software technology filetype:pdf',
    'Palm Beach County Public Schools RFP software technology filetype:pdf',
    'Duval County Public Schools RFP software technology filetype:pdf',
    'San Diego Unified School District RFP software technology filetype:pdf',
    'San Francisco Unified School District RFP software technology filetype:pdf',
    'Oakland Unified School District RFP software technology filetype:pdf',
    'Long Beach Unified School District RFP software filetype:pdf',
    'Fresno Unified School District RFP software filetype:pdf',
    'Sacramento City Unified School District RFP software filetype:pdf',
    'San Bernardino City Unified RFP software technology filetype:pdf',
    'Riverside Unified School District RFP software filetype:pdf',
    'Denver Public Schools RFP software technology filetype:pdf',
    'Austin Independent School District RFP software technology filetype:pdf',
    'San Antonio Independent School District RFP software filetype:pdf',
    'Fort Worth Independent School District RFP software filetype:pdf',
    'Dallas Independent School District RFP software technology filetype:pdf',
    'El Paso Independent School District RFP software filetype:pdf',
    'Northside Independent School District RFP software filetype:pdf',
    'Aldine Independent School District RFP software filetype:pdf',
    'Cypress-Fairbanks Independent School District RFP software filetype:pdf',
    'Spring Branch Independent School District RFP software filetype:pdf',
    'Pasadena Independent School District Texas RFP software filetype:pdf',
    'Arlington Independent School District RFP software filetype:pdf',

    # === B: County offices of education (20) ===
    '"county office of education" RFP "student information" filetype:pdf',
    '"county office of education" RFP "data platform" filetype:pdf',
    '"county superintendent" RFP student software filetype:pdf',
    'Los Angeles County Office of Education RFP technology filetype:pdf',
    'San Diego County Office of Education RFP technology filetype:pdf',
    'Orange County Department of Education RFP technology filetype:pdf',
    'Sacramento County Office of Education RFP technology filetype:pdf',
    'San Bernardino County Superintendent RFP technology filetype:pdf',
    'Riverside County Office of Education RFP technology filetype:pdf',
    'Kern County Superintendent of Schools RFP technology filetype:pdf',
    'Fresno County Office of Education RFP technology filetype:pdf',
    'Contra Costa County Office of Education RFP technology filetype:pdf',
    'Alameda County Office of Education RFP technology filetype:pdf',
    'Santa Clara County Office of Education RFP technology filetype:pdf',
    'Ventura County Office of Education RFP technology filetype:pdf',
    '"intermediate school district" RFP software technology filetype:pdf',
    '"intermediate unit" RFP software technology education filetype:pdf',
    '"educational service district" RFP software technology filetype:pdf',
    '"regional education service agency" RFP software filetype:pdf',
    '"area education agency" RFP software technology filetype:pdf',

    # === C: International — UK (20) ===
    '"multi-academy trust" tender "management information" filetype:pdf',
    '"academy trust" procurement "MIS" software filetype:pdf',
    '"local authority" tender school "management information" filetype:pdf',
    '"school MIS" tender OR procurement UK filetype:pdf',
    '"pupil tracking" software tender UK filetype:pdf',
    '"SIMS" OR "Arbor" OR "Bromcom" school tender filetype:pdf',
    '"school management information system" tender UK filetype:pdf',
    'DfE "management information" procurement school filetype:pdf',
    '"ISAMS" OR "iSAMS" school procurement filetype:pdf',
    '"school cloud" "management information" procurement filetype:pdf',
    '"academy" "student tracking" software procurement UK filetype:pdf',
    '"multi-academy" "data management" procurement filetype:pdf',
    '"school" "progress tracking" software procurement UK filetype:pdf',
    '"pupil data" management system procurement UK filetype:pdf',
    '"assessment tracking" software procurement school UK filetype:pdf',
    '"local authority" "education" "data platform" procurement filetype:pdf',
    '"academy trust" "student data" platform procurement filetype:pdf',
    '"GDPR" school "data management" procurement filetype:pdf',
    '"education" "cloud platform" tender UK school filetype:pdf',
    '"school" "analytics" platform procurement UK filetype:pdf',

    # === D: International — Australia & NZ (15) ===
    '"department of education" tender "student management" Australia filetype:pdf',
    '"school" "student management system" tender Australia filetype:pdf',
    '"education department" "information system" tender Queensland filetype:pdf',
    '"education" "management system" tender "New South Wales" filetype:pdf',
    '"school management" system tender Victoria Australia filetype:pdf',
    '"student information" system tender Australia OR "New Zealand" filetype:pdf',
    '"KAMAR" OR "eTAP" school procurement "New Zealand" filetype:pdf',
    '"education" "cloud platform" tender Australia school filetype:pdf',
    '"NAPLAN" technology platform tender Australia filetype:pdf',
    '"ministry of education" "student management" tender "New Zealand" filetype:pdf',
    '"school" "learning management" system tender Australia filetype:pdf',
    '"education department" "data analytics" platform tender Australia filetype:pdf',
    '"student wellbeing" platform tender school Australia filetype:pdf',
    '"school administration" system tender Australia filetype:pdf',
    '"Catholic education" "student management" tender Australia filetype:pdf',

    # === E: Private/independent schools (15) ===
    '"independent school" RFP "student information" filetype:pdf',
    '"private school" RFP "student information system" filetype:pdf',
    '"independent school" RFP "learning management" filetype:pdf',
    '"NAIS" school technology RFP OR procurement filetype:pdf',
    '"boarding school" RFP technology software student filetype:pdf',
    '"preparatory school" RFP technology software student filetype:pdf',
    '"diocesan" school RFP "student information" software filetype:pdf',
    '"Catholic school" OR "Catholic diocese" RFP "student information" filetype:pdf',
    '"archdiocese" school RFP "student information" software filetype:pdf',
    '"Lutheran school" RFP technology software student filetype:pdf',
    '"Christian school" RFP "student information" software filetype:pdf',
    '"Montessori" school RFP technology software student filetype:pdf',
    '"Waldorf" school RFP technology software student filetype:pdf',
    '"Jewish day school" RFP technology software filetype:pdf',
    '"Islamic school" RFP technology software student filetype:pdf',

    # === F: Tribal & DOD schools (10) ===
    '"Bureau of Indian Education" RFP software technology filetype:pdf',
    '"tribal school" RFP "student information" software filetype:pdf',
    '"BIE" school RFP "student data" software filetype:pdf',
    '"Native American" school RFP technology software filetype:pdf',
    '"DoDEA" RFP software technology student filetype:pdf',
    '"Department of Defense Education" RFP software filetype:pdf',
    '"military school" RFP "student information" software filetype:pdf',
    '"overseas schools" RFP "student information" filetype:pdf',
    '"Indian education" RFP "data management" software filetype:pdf',
    '"tribal education" RFP technology platform filetype:pdf',

    # === G: Finance/HR adjacent to student data (15) ===
    'school district RFP "enterprise resource planning" filetype:pdf',
    'school district RFP "financial management" software filetype:pdf',
    'school district RFP "human resources" software technology filetype:pdf',
    'school district RFP "payroll" software technology filetype:pdf',
    'school district RFP "accounting" software technology filetype:pdf',
    'school district RFP "ERP" software technology filetype:pdf',
    'school district RFP "business management" software filetype:pdf',
    'school district RFP "fund accounting" software filetype:pdf',
    'school district RFP "integrated" ERP student finance filetype:pdf',
    'school district RFP "Munis" OR "Tyler" finance software filetype:pdf',
    'school district RFP "Infinite Visions" OR "Alio" finance filetype:pdf',
    'school district RFP "Frontline" OR "Absence Management" filetype:pdf',
    'school district RFP "substitute management" software filetype:pdf',
    'school district RFP "professional development" tracking software filetype:pdf',
    'school district RFP "staff evaluation" software filetype:pdf',

    # === H: Library/media center management (10) ===
    'school district RFP "library management" software filetype:pdf',
    'school district RFP "library automation" software filetype:pdf',
    'school district RFP "media center" management software filetype:pdf',
    'school district RFP "digital library" platform filetype:pdf',
    'school district RFP "library catalog" software filetype:pdf',
    '"Follett" OR "Destiny" library school RFP filetype:pdf',
    '"Alexandria" library school software RFP filetype:pdf',
    'school district RFP "textbook management" software filetype:pdf',
    'school district RFP "instructional materials" management filetype:pdf',
    'school district RFP "curriculum materials" management software filetype:pdf',

    # === I: Health/wellness & counseling (15) ===
    'school district RFP "counseling" management software filetype:pdf',
    'school district RFP "guidance counselor" software filetype:pdf',
    'school district RFP "mental health" tracking software filetype:pdf',
    'school district RFP "SEL" OR "social emotional" platform filetype:pdf',
    'school district RFP "wellness" tracking software student filetype:pdf',
    'school district RFP "health records" management software filetype:pdf',
    'school district RFP "school health" management system filetype:pdf',
    'school district RFP "vision screening" software filetype:pdf',
    'school district RFP "dental screening" software filetype:pdf',
    'school district RFP "nursing" management software filetype:pdf',
    'school district RFP "crisis management" software filetype:pdf',
    'school district RFP "suicide prevention" software filetype:pdf',
    'school district RFP "bullying prevention" software filetype:pdf',
    'school district RFP "character education" platform filetype:pdf',
    'school district RFP "restorative justice" software filetype:pdf',

    # === J: Attendance & discipline management (15) ===
    'school district RFP "attendance tracking" software filetype:pdf',
    'school district RFP "chronic absenteeism" software filetype:pdf',
    'school district RFP "truancy" management software filetype:pdf',
    'school district RFP "discipline management" software filetype:pdf',
    'school district RFP "behavior management" software filetype:pdf',
    'school district RFP "visitor management" school filetype:pdf',
    'school district RFP "volunteer management" software filetype:pdf',
    'school district RFP "hall pass" management software filetype:pdf',
    'school district RFP "tardy tracking" software filetype:pdf',
    'school district RFP "student check-in" software filetype:pdf',
    'school district RFP "student ID" OR "identification" system filetype:pdf',
    'school district RFP "cafeteria" point-of-sale student filetype:pdf',
    'school district RFP "lunch" management student software filetype:pdf',
    'school district RFP "free reduced lunch" management software filetype:pdf',
    'school district RFP "meal tracking" student software filetype:pdf',

    # === K: Transportation & facilities with student data (10) ===
    'school district RFP "bus routing" software student filetype:pdf',
    'school district RFP "transportation management" software student filetype:pdf',
    'school district RFP "fleet management" school bus software filetype:pdf',
    'school district RFP "GPS tracking" school bus software filetype:pdf',
    'school district RFP "parent notification" bus software filetype:pdf',
    'school district RFP "facilities" management software filetype:pdf',
    'school district RFP "space utilization" software filetype:pdf',
    'school district RFP "energy management" school software filetype:pdf',
    'school district RFP "work order" management school software filetype:pdf',
    'school district RFP "asset tracking" school software filetype:pdf',

    # === L: Safety & security with student data (10) ===
    'school district RFP "school safety" software platform filetype:pdf',
    'school district RFP "emergency management" school software filetype:pdf',
    'school district RFP "reunification" school software filetype:pdf',
    'school district RFP "access control" school software filetype:pdf',
    'school district RFP "camera" OR "surveillance" school software filetype:pdf',
    'school district RFP "panic button" school software filetype:pdf',
    'school district RFP "lockdown" notification school software filetype:pdf',
    'school district RFP "anonymous reporting" school software filetype:pdf',
    'school district RFP "weapon detection" school technology filetype:pdf',
    'school district RFP "vape detection" school technology filetype:pdf',

    # === M: Communication platforms (10) ===
    'school district RFP "communication platform" parent filetype:pdf',
    'school district RFP "messaging" platform parent teacher filetype:pdf',
    'school district RFP "robocall" OR "auto-dialer" school filetype:pdf',
    'school district RFP "email" management school community filetype:pdf',
    'school district RFP "social media" management school filetype:pdf',
    'school district RFP "newsletter" platform school filetype:pdf',
    'school district RFP "website" CMS school filetype:pdf',
    'school district RFP "mobile app" school parent filetype:pdf',
    'school district RFP "translation" service school parent filetype:pdf',
    'school district RFP "interpreter" service school technology filetype:pdf',

    # === N: Emerging / AI / analytics (15) ===
    'school district RFP "artificial intelligence" education filetype:pdf',
    'school district RFP "machine learning" education student filetype:pdf',
    'school district RFP "predictive analytics" student filetype:pdf',
    'school district RFP "data science" education student filetype:pdf',
    'school district RFP "learning analytics" platform filetype:pdf',
    'school district RFP "adaptive learning" platform filetype:pdf',
    'school district RFP "personalized learning" platform filetype:pdf',
    'school district RFP "competency-based education" platform filetype:pdf',
    'school district RFP "micro-credentialing" platform filetype:pdf',
    'school district RFP "digital badges" platform education filetype:pdf',
    'school district RFP "blockchain" education credentials filetype:pdf',
    'school district RFP "virtual reality" OR "VR" education filetype:pdf',
    'school district RFP "augmented reality" OR "AR" education filetype:pdf',
    'school district RFP "esports" management school filetype:pdf',
    'school district RFP "robotics" program management school filetype:pdf',

    # === O: Multi-state without filetype restriction (10) ===
    '"student information system" RFP 2024 OR 2025 school district',
    '"student data platform" procurement 2024 OR 2025 K-12',
    '"learning management system" RFP 2024 school district',
    '"assessment platform" RFP 2024 OR 2025 school district',
    '"early warning system" RFP 2024 school district student',
    '"data dashboard" RFP 2024 school district student',
    '"student analytics" RFP 2024 OR 2025 school district',
    '"MTSS platform" RFP 2024 school district',
    '"RTI platform" RFP 2024 OR 2025 school district',
    '"EdTech" RFP 2024 OR 2025 school district procurement',

    # === P: More doc/docx format (10) ===
    'school district RFP "student information" system filetype:docx',
    'school district RFP "assessment platform" filetype:docx',
    'school district RFP "learning management" filetype:docx',
    'school district RFP "student data" analytics filetype:docx',
    'school district procurement "data platform" student filetype:docx',
    '"public schools" RFP "student management" filetype:doc',
    '"school board" RFP "data analytics" student filetype:doc',
    '"county schools" RFP "information system" student filetype:doc',
    '"independent school district" RFP "student data" filetype:docx',
    'K-12 RFP "data integration" student filetype:docx',
]

def load_existing_urls():
    urls = set()
    for f in ["STRIVED_COMPLETE_TEXAS.csv", "STRIVED_ROUND3.csv", "STRIVED_ROUND4.csv",
              "STRIVED_ROUND5.csv", "STRIVED_ROUND6.csv", "STRIVED_ROUND7.csv",
              "STRIVED_ROUND8.csv", "STRIVED_ACCUMULATED.csv"]:
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
    if not url: return None
    is_pdf = url.lower().endswith(".pdf")
    is_doc = any(url.lower().endswith(ext) for ext in [".doc", ".docx"])
    if not is_pdf and not is_doc: return None
    if is_doc:
        row["keyword_pass"] = True
        row["keyword_reason"] = "doc/docx - skip PDF parse"
        return {"url": url, "text": "", "passed": False, "reason": "doc/docx skipped", "emails": "", "row": row}
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
    valid_leads = [l for l in leads if l.get("pdf_url", "").lower().endswith((".pdf", ".doc", ".docx"))]
    print(f"  Downloadable leads to verify: {len(valid_leads)}")
    print("\n  Phase 1: Download + Keyword Filter (parallel)...")
    passed = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=PDF_VERIFY_WORKERS) as executor:
        futures = {executor.submit(download_and_filter, lead): lead for lead in valid_leads}
        done = 0
        for future in concurrent.futures.as_completed(futures):
            done += 1
            result = future.result()
            if result and result["passed"]: passed.append(result)
            if done % 50 == 0:
                print(f"    ... {done}/{len(valid_leads)} processed, {len(passed)} passed", flush=True)
    print(f"  Keyword filter: {len(passed)} passed out of {len(valid_leads)}")
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
    out_df.to_csv(ROUND8_CSV, index=False)
    print(f"\n  Saved {len(out_df)} leads to {ROUND8_CSV}")
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
    print(f"\n  ROUND 8 SUMMARY: {len(verified)} verified, {rel_count} relevant")

def main():
    print(f"\n{'#' * 60}")
    print(f"  STRIVED Round 8 RFP Collection ({len(QUERIES)} queries)")
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
                r"([\w\s\.'-]+(?:ISD|CISD|School District|Public Schools|County Schools|Parish Schools|Academy|Board of Education|School Board|Charter School|Charter Schools|Unified School|City Schools))",
                f"{title} {body}", re.I)
            all_leads.append({
                "source": "DDGS-Round8", "school_name": school_match.group(1).strip() if school_match else "",
                "title": title[:200], "bid_url": url, "pdf_url": url, "due_date": "", "rfp_id": "",
            })
            new_count += 1
        total_new += new_count
        print(f"{len(results)} results, {new_count} new")
        time.sleep(random.uniform(*DDGS_SLEEP))
    print(f"\n  DDGS Round 8 total: {total_new} new leads")
    if all_leads:
        verified = verify_leads(all_leads)
        save_and_merge(verified)
    else: print("\n  No leads found.")
    print(f"\n{'#' * 60}")
    print(f"  DONE")
    print(f"{'#' * 60}")

if __name__ == "__main__":
    main()
