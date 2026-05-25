"""
strived_round7.py — Round 7 RFP Collection

250 new DDGS queries targeting completely untapped areas:
  - Charter school networks & management organizations (CMOs)
  - State longitudinal data systems (SLDS)
  - Regional cooperatives & purchasing consortiums
  - Specific incumbent vendor replacement queries
  - School board meeting minutes mentioning software procurement
  - State technology directors associations
  - More geographic coverage (Southeast, Midwest, Northeast)
  - Library system & media center management
  - Special education & IEP management
  - College/career readiness platforms
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
ROUND7_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ROUND7.csv")
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
    # === A: Charter school networks & CMOs (25) ===
    'KIPP charter school RFP software OR technology filetype:pdf',
    'Success Academy RFP software OR technology filetype:pdf',
    'Achievement First RFP software OR technology filetype:pdf',
    'Uncommon Schools RFP software OR technology filetype:pdf',
    'Aspire Public Schools RFP software OR technology filetype:pdf',
    'Green Dot Public Schools RFP software OR technology filetype:pdf',
    'Uplift Education RFP software OR technology filetype:pdf',
    'IDEA Public Schools RFP software OR technology filetype:pdf',
    'Harmony Public Schools RFP software OR technology filetype:pdf',
    'BASIS charter school RFP software filetype:pdf',
    '"charter management organization" RFP "student information" filetype:pdf',
    '"charter school" RFP "student information system" filetype:pdf',
    '"charter school" RFP "data platform" student filetype:pdf',
    '"charter network" RFP software student data filetype:pdf',
    '"charter school" procurement "student records" filetype:pdf',
    '"charter school" bid "assessment platform" filetype:pdf',
    '"charter school" RFP "learning management" filetype:pdf',
    '"charter school authorizer" data reporting requirements filetype:pdf',
    'Noble Network of Charter Schools RFP technology filetype:pdf',
    'Rocketship Education RFP technology filetype:pdf',
    'Great Hearts Academies RFP technology filetype:pdf',
    'Mastery Charter Schools RFP technology filetype:pdf',
    '"cyber charter school" RFP technology OR software filetype:pdf',
    '"virtual charter" RFP "learning management" filetype:pdf',
    '"online charter school" RFP technology platform filetype:pdf',

    # === B: State longitudinal data systems (SLDS) (20) ===
    '"state longitudinal data system" RFP OR procurement filetype:pdf',
    '"SLDS" RFP OR procurement education data filetype:pdf',
    '"P-20" data system RFP OR procurement education filetype:pdf',
    '"P-20W" data system RFP OR procurement filetype:pdf',
    '"statewide longitudinal" education data RFP filetype:pdf',
    '"data warehouse" RFP state education student filetype:pdf',
    '"education data warehouse" RFP OR procurement filetype:pdf',
    '"student data warehouse" RFP school district filetype:pdf',
    '"data lake" RFP education student K-12 filetype:pdf',
    '"operational data store" RFP education filetype:pdf',
    '"Ed-Fi" implementation RFP OR procurement filetype:pdf',
    '"Ed-Fi" data standard RFP school district filetype:pdf',
    '"CEDS" data RFP education filetype:pdf',
    '"SIF" "Schools Interoperability" RFP filetype:pdf',
    '"data interoperability" RFP school district filetype:pdf',
    '"OneRoster" implementation RFP school filetype:pdf',
    '"Clever" implementation school district RFP filetype:pdf',
    '"ClassLink" implementation school district RFP filetype:pdf',
    '"data governance" RFP school district student filetype:pdf',
    '"master data management" RFP education student filetype:pdf',

    # === C: Regional cooperatives & purchasing consortiums (25) ===
    '"cooperative purchasing" school district "student information" filetype:pdf',
    '"purchasing cooperative" school "data platform" filetype:pdf',
    '"consortium" school district RFP software technology filetype:pdf',
    '"buying consortium" school RFP software filetype:pdf',
    '"joint purchasing" school district technology filetype:pdf',
    '"piggyback" contract school district software filetype:pdf',
    'PEPPM educational technology RFP OR contract filetype:pdf',
    'NJPA school software technology contract filetype:pdf',
    'Sourcewell school software technology contract filetype:pdf',
    '"National IPA" school software technology contract filetype:pdf',
    'OMNIA Partners education software contract filetype:pdf',
    '"1GPA" school technology contract filetype:pdf',
    '"TCPN" school technology contract filetype:pdf',
    'TIPS TAPS school technology RFP filetype:pdf',
    '"Region 4" ESC cooperative technology contract filetype:pdf',
    '"Region 10" ESC cooperative technology contract filetype:pdf',
    '"BuyBoard" school technology contract filetype:pdf',
    '"DIR" Texas technology contract education filetype:pdf',
    'NJSBA technology contract school filetype:pdf',
    'CASBO technology contract school district filetype:pdf',
    '"OECN" Ohio education technology RFP filetype:pdf',
    '"LACA" Ohio school technology RFP filetype:pdf',
    '"ITC" Ohio education technology RFP filetype:pdf',
    'WSIPC school technology contract filetype:pdf',
    'NWRDC school technology contract filetype:pdf',

    # === D: Incumbent vendor replacement (20) ===
    'school district "replace" OR "replacement" "PowerSchool" RFP filetype:pdf',
    'school district "replace" OR "migration" "Infinite Campus" RFP filetype:pdf',
    'school district "replace" OR "migrate" "Skyward" RFP filetype:pdf',
    'school district "replace" OR "migrate" "Tyler SIS" RFP filetype:pdf',
    'school district "replace" OR "migrate" "Aeries" RFP filetype:pdf',
    'school district "transition from" "PowerSchool" filetype:pdf',
    'school district "transition from" "Infinite Campus" filetype:pdf',
    'school district RFP "current system" "student information" filetype:pdf',
    'school district RFP "existing system" "student information" replace filetype:pdf',
    'school district RFP "legacy system" student data replace filetype:pdf',
    'school district RFP "system replacement" student information filetype:pdf',
    'school district RFP "system migration" student data filetype:pdf',
    'school district "end of contract" "student information" RFP filetype:pdf',
    'school district "contract expiration" "student information" filetype:pdf',
    'school district "new SIS" RFP OR procurement filetype:pdf',
    '"SIS migration" school district RFP filetype:pdf',
    '"SIS replacement" school district RFP filetype:pdf',
    '"SIS implementation" school district RFP filetype:pdf',
    '"student information system" implementation RFP filetype:pdf',
    '"student information system" conversion RFP filetype:pdf',

    # === E: Special education & IEP management (20) ===
    'school district RFP "special education" software filetype:pdf',
    'school district RFP "IEP" management system filetype:pdf',
    'school district RFP "individualized education" software filetype:pdf',
    'school district RFP "504 plan" management software filetype:pdf',
    'school district RFP "special education" data management filetype:pdf',
    'school district RFP "SpEd" management system filetype:pdf',
    'school district RFP "SPED" tracking software filetype:pdf',
    '"special education" "case management" RFP school filetype:pdf',
    '"related services" tracking RFP school district filetype:pdf',
    '"Medicaid claiming" school district RFP software filetype:pdf',
    '"Medicaid reimbursement" school district RFP software filetype:pdf',
    '"Section 504" school district RFP software filetype:pdf',
    '"child find" tracking system RFP school filetype:pdf',
    '"early intervention" tracking system RFP school filetype:pdf',
    '"RTI" OR "MTSS" software RFP school district filetype:pdf',
    '"multi-tiered" system support RFP software school filetype:pdf',
    '"intervention tracking" RFP software school district filetype:pdf',
    '"behavior tracking" RFP software school district filetype:pdf',
    '"PBIS" tracking software RFP school district filetype:pdf',
    '"threat assessment" software RFP school district filetype:pdf',

    # === F: Southeast US school districts (25) ===
    'Gwinnett County Public Schools RFP software technology filetype:pdf',
    'Fulton County Schools RFP software technology filetype:pdf',
    'DeKalb County Schools RFP software technology filetype:pdf',
    'Cobb County School District RFP software technology filetype:pdf',
    'Cherokee County School District Georgia RFP software filetype:pdf',
    'Forsyth County Schools Georgia RFP software filetype:pdf',
    'Wake County Public Schools RFP software technology filetype:pdf',
    'Charlotte-Mecklenburg Schools RFP software technology filetype:pdf',
    'Guilford County Schools RFP software technology filetype:pdf',
    'Cumberland County Schools RFP software technology filetype:pdf',
    'Johnston County Schools RFP software technology filetype:pdf',
    'Greenville County Schools RFP software technology filetype:pdf',
    'Richland County School District RFP software filetype:pdf',
    'Charleston County School District RFP software filetype:pdf',
    'Knox County Schools Tennessee RFP software filetype:pdf',
    'Shelby County Schools Tennessee RFP software filetype:pdf',
    'Hamilton County Schools Tennessee RFP software filetype:pdf',
    'Williamson County Schools Tennessee RFP software filetype:pdf',
    'Jefferson County Public Schools Kentucky RFP software filetype:pdf',
    'Fayette County Public Schools Kentucky RFP software filetype:pdf',
    'Mobile County Public Schools Alabama RFP software filetype:pdf',
    'Madison County Schools Alabama RFP software filetype:pdf',
    'Baldwin County Public Schools Alabama RFP software filetype:pdf',
    'Jackson Public Schools Mississippi RFP software filetype:pdf',
    'DeSoto County Schools Mississippi RFP software filetype:pdf',

    # === G: Midwest school districts (25) ===
    'Columbus City Schools Ohio RFP software technology filetype:pdf',
    'Cleveland Metropolitan School District RFP software filetype:pdf',
    'Cincinnati Public Schools RFP software technology filetype:pdf',
    'Akron Public Schools RFP software technology filetype:pdf',
    'Dublin City Schools Ohio RFP software filetype:pdf',
    'Lakota Local School District RFP software filetype:pdf',
    'Indianapolis Public Schools RFP software technology filetype:pdf',
    'Fort Wayne Community Schools RFP software filetype:pdf',
    'Carmel Clay Schools RFP software technology filetype:pdf',
    'Hamilton Southeastern Schools RFP software filetype:pdf',
    'Detroit Public Schools RFP software technology filetype:pdf',
    'Grand Rapids Public Schools RFP software filetype:pdf',
    'Ann Arbor Public Schools RFP software filetype:pdf',
    'Minneapolis Public Schools RFP software technology filetype:pdf',
    'Saint Paul Public Schools RFP software technology filetype:pdf',
    'Anoka-Hennepin School District RFP software filetype:pdf',
    'Madison Metropolitan School District RFP software filetype:pdf',
    'Milwaukee Public Schools RFP software technology filetype:pdf',
    'Waukesha School District RFP software filetype:pdf',
    'Des Moines Public Schools RFP software technology filetype:pdf',
    'West Des Moines Schools RFP software filetype:pdf',
    'Omaha Public Schools RFP software technology filetype:pdf',
    'Lincoln Public Schools Nebraska RFP software filetype:pdf',
    'Kansas City Public Schools RFP software technology filetype:pdf',
    'Blue Valley School District Kansas RFP software filetype:pdf',

    # === H: Northeast school districts (25) ===
    'Boston Public Schools RFP software technology filetype:pdf',
    'Springfield Public Schools Massachusetts RFP software filetype:pdf',
    'Worcester Public Schools RFP software technology filetype:pdf',
    'Providence Public Schools RFP software technology filetype:pdf',
    'Hartford Public Schools RFP software technology filetype:pdf',
    'New Haven Public Schools RFP software filetype:pdf',
    'Fairfield Public Schools Connecticut RFP software filetype:pdf',
    'Buffalo Public Schools RFP software technology filetype:pdf',
    'Rochester City School District RFP software filetype:pdf',
    'Syracuse City School District RFP software filetype:pdf',
    'Yonkers Public Schools RFP software filetype:pdf',
    'Newark Public Schools RFP software technology filetype:pdf',
    'Jersey City Public Schools RFP software filetype:pdf',
    'Paterson Public Schools RFP software filetype:pdf',
    'Camden City School District RFP software filetype:pdf',
    'Philadelphia School District RFP software technology filetype:pdf',
    'Pittsburgh Public Schools RFP software technology filetype:pdf',
    'Allentown School District RFP software filetype:pdf',
    'Reading School District Pennsylvania RFP software filetype:pdf',
    'Baltimore City Public Schools RFP software technology filetype:pdf',
    'Anne Arundel County Public Schools RFP software filetype:pdf',
    'Montgomery County Public Schools Maryland RFP software filetype:pdf',
    'Prince Georges County Public Schools RFP software filetype:pdf',
    'Fairfax County Public Schools RFP software technology filetype:pdf',
    'Loudoun County Public Schools RFP software filetype:pdf',

    # === I: College & career readiness / workforce (15) ===
    'school district RFP "college readiness" platform filetype:pdf',
    'school district RFP "career readiness" software filetype:pdf',
    'school district RFP "college and career" platform filetype:pdf',
    'school district RFP "graduation tracking" software filetype:pdf',
    'school district RFP "credit recovery" software filetype:pdf',
    'school district RFP "dropout prevention" software filetype:pdf',
    'school district RFP "early warning" system student filetype:pdf',
    'school district RFP "CTE" management software filetype:pdf',
    'school district RFP "career technical education" software filetype:pdf',
    'school district RFP "work-based learning" platform filetype:pdf',
    'school district RFP "dual enrollment" management software filetype:pdf',
    'school district RFP "transcript evaluation" software filetype:pdf',
    'school district RFP "postsecondary" tracking software filetype:pdf',
    '"Perkins V" RFP school district software filetype:pdf',
    '"CLNA" school district software platform filetype:pdf',

    # === J: Assessment & testing platforms (20) ===
    'school district RFP "formative assessment" platform filetype:pdf',
    'school district RFP "summative assessment" platform filetype:pdf',
    'school district RFP "benchmark assessment" software filetype:pdf',
    'school district RFP "interim assessment" system filetype:pdf',
    'school district RFP "universal screening" software filetype:pdf',
    'school district RFP "diagnostic assessment" software filetype:pdf',
    'school district RFP "test administration" software filetype:pdf',
    'school district RFP "online testing" platform filetype:pdf',
    'school district RFP "item bank" assessment filetype:pdf',
    'school district RFP "assessment management" system filetype:pdf',
    'state "department of education" RFP "assessment" platform filetype:pdf',
    'state RFP "statewide assessment" technology platform filetype:pdf',
    'state RFP "computer adaptive testing" education filetype:pdf',
    '"MAP testing" OR "NWEA" RFP school district filetype:pdf',
    '"iReady" OR "i-Ready" RFP school district filetype:pdf',
    '"Renaissance" "Star assessment" RFP school district filetype:pdf',
    '"DIBELS" OR "mCLASS" RFP school district filetype:pdf',
    '"Lexia" OR "Dreambox" RFP school district filetype:pdf',
    '"Amplify" RFP school district assessment filetype:pdf',
    '"Illuminate Education" RFP school district filetype:pdf',

    # === K: Grading, reporting & parent portals (15) ===
    'school district RFP "gradebook" software filetype:pdf',
    'school district RFP "grade reporting" system filetype:pdf',
    'school district RFP "report card" software filetype:pdf',
    'school district RFP "standards-based grading" software filetype:pdf',
    'school district RFP "parent portal" software filetype:pdf',
    'school district RFP "family engagement" platform filetype:pdf',
    'school district RFP "parent notification" system filetype:pdf',
    'school district RFP "student portal" software filetype:pdf',
    'school district RFP "teacher gradebook" software filetype:pdf',
    'school district RFP "competency-based" grading software filetype:pdf',
    'school district RFP "standards-based" reporting software filetype:pdf',
    'school district RFP "student records" reporting filetype:pdf',
    'school district RFP "electronic grade" reporting filetype:pdf',
    'school district RFP "online grades" parent access filetype:pdf',
    'school district RFP "progress report" software system filetype:pdf',

    # === L: Scheduling, enrollment & registration (15) ===
    'school district RFP "master schedule" software filetype:pdf',
    'school district RFP "scheduling software" student filetype:pdf',
    'school district RFP "class scheduling" software filetype:pdf',
    'school district RFP "online registration" student filetype:pdf',
    'school district RFP "enrollment system" student filetype:pdf',
    'school district RFP "lottery" enrollment management filetype:pdf',
    'school district RFP "school choice" enrollment software filetype:pdf',
    'school district RFP "open enrollment" management software filetype:pdf',
    'school district RFP "student registration" system filetype:pdf',
    'school district RFP "kindergarten registration" online filetype:pdf',
    'school district RFP "pre-registration" student online filetype:pdf',
    'school district RFP "intradistrict transfer" management filetype:pdf',
    'school district RFP "boundary planning" student filetype:pdf',
    'school district RFP "enrollment forecasting" software filetype:pdf',
    'school district RFP "demographic" student enrollment filetype:pdf',

    # === M: Board meeting minutes & agenda items (10) ===
    'school board minutes "approved" "student information system" filetype:pdf',
    'school board agenda "student information system" procurement filetype:pdf',
    'school board minutes "approved" "data platform" contract filetype:pdf',
    'school board agenda "software" "contract" student data filetype:pdf',
    'school board minutes "approved" "technology" software contract filetype:pdf',
    'school board "action item" "student information" software filetype:pdf',
    'school board "consent agenda" "student information" contract filetype:pdf',
    'school board minutes "PowerSchool" OR "Infinite Campus" contract filetype:pdf',
    'school board "recommendation" "student data" software award filetype:pdf',
    'school board minutes "approved" "SIS" software contract filetype:pdf',

    # === N: Multi-state searches without filetype (10) ===
    'school district RFP "student information system" 2024 OR 2025',
    'school district procurement "student data platform" 2024 OR 2025',
    'K-12 RFP "data analytics" student 2024 OR 2025',
    'school district "request for proposal" "student information" 2024',
    '"education" RFP "data warehouse" student K-12 2024 OR 2025',
    'school district bid "student information" technology 2024 OR 2025',
    '"student information system" solicitation school district 2024',
    'school district RFQ "student data" OR "student information" 2024',
    '"unified school district" RFP technology "student data" 2024',
    '"independent school district" RFP "student information" 2024 OR 2025',
]

def load_existing_urls():
    urls = set()
    for f in ["STRIVED_COMPLETE_TEXAS.csv", "STRIVED_ROUND3.csv", "STRIVED_ROUND4.csv",
              "STRIVED_ROUND5.csv", "STRIVED_ROUND6.csv", "STRIVED_ROUND7.csv", "STRIVED_ACCUMULATED.csv"]:
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
    out_df.to_csv(ROUND7_CSV, index=False)
    print(f"\n  Saved {len(out_df)} leads to {ROUND7_CSV}")
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
    print(f"\n  ROUND 7 SUMMARY: {len(verified)} verified, {rel_count} relevant")

def main():
    print(f"\n{'#' * 60}")
    print(f"  STRIVED Round 7 RFP Collection ({len(QUERIES)} queries)")
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
                r"([\w\s\.'-]+(?:ISD|CISD|School District|Public Schools|County Schools|Parish Schools|Academy|Board of Education|School Board|Charter School|Charter Schools))",
                f"{title} {body}", re.I)
            all_leads.append({
                "source": "DDGS-Round7", "school_name": school_match.group(1).strip() if school_match else "",
                "title": title[:200], "bid_url": url, "pdf_url": url, "due_date": "", "rfp_id": "",
            })
            new_count += 1
        total_new += new_count
        print(f"{len(results)} results, {new_count} new")
        time.sleep(random.uniform(*DDGS_SLEEP))
    print(f"\n  DDGS Round 7 total: {total_new} new leads")
    if all_leads:
        verified = verify_leads(all_leads)
        save_and_merge(verified)
    else: print("\n  No leads found.")
    print(f"\n{'#' * 60}")
    print(f"  DONE")
    print(f"{'#' * 60}")

if __name__ == "__main__":
    main()
