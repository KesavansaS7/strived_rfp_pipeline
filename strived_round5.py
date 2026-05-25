"""
strived_round5.py — Round 5 RFP Collection (massive expansion)

200+ new DDGS queries targeting:
  - 80+ more districts/counties not yet searched
  - Specific vendor replacement RFPs
  - State assessment/testing contracts
  - Regional education agencies
  - Specific NAICS/NIGP searches
  - Alternative phrasings and document types
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
ROUND5_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ROUND5.csv")
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
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Analyze this RFP:\n\n{truncated}"},
        ],
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
    # === BATCH A: More US counties (30) ===
    '"Lake County" schools RFP technology OR software filetype:pdf',
    '"Orange County" schools RFP technology OR "student data" filetype:pdf',
    '"Cook County" schools RFP technology OR software filetype:pdf',
    '"Harris County" schools RFP technology OR software filetype:pdf',
    '"Tarrant County" schools RFP technology OR software filetype:pdf',
    '"Bexar County" schools RFP technology OR software filetype:pdf',
    '"Travis County" schools RFP technology OR software filetype:pdf',
    '"El Paso County" schools RFP technology OR software filetype:pdf',
    '"Hidalgo County" schools RFP technology OR software filetype:pdf',
    '"Collin County" schools RFP technology OR software filetype:pdf',
    '"Denton County" schools RFP technology OR software filetype:pdf',
    '"Williamson County" schools RFP technology OR software filetype:pdf',
    '"Fort Bend County" schools RFP technology OR software filetype:pdf',
    '"Montgomery County" schools RFP technology OR software filetype:pdf',
    '"Brazoria County" schools RFP technology OR software filetype:pdf',
    '"Galveston County" schools RFP technology OR software filetype:pdf',
    '"Nueces County" schools RFP technology OR software filetype:pdf',
    '"Webb County" schools RFP technology OR software filetype:pdf',
    '"Cameron County" schools RFP technology OR software filetype:pdf',
    '"Lubbock County" schools RFP technology OR software filetype:pdf',
    '"McLennan County" schools RFP technology OR software filetype:pdf',
    '"Smith County" schools RFP technology OR software filetype:pdf',
    '"Ector County" schools RFP technology OR software filetype:pdf',
    '"Midland County" schools RFP technology OR software filetype:pdf',
    '"Hays County" schools RFP technology OR software filetype:pdf',
    '"Bell County" schools RFP technology OR software filetype:pdf',
    '"Johnson County" schools RFP technology OR software filetype:pdf',
    '"Parker County" schools RFP technology OR software filetype:pdf',
    '"Guadalupe County" schools RFP technology OR software filetype:pdf',
    '"Comal County" schools RFP technology OR software filetype:pdf',

    # === BATCH B: Vendor replacement/migration RFPs (20) ===
    'school district "replacing PowerSchool" OR "PowerSchool replacement" filetype:pdf',
    'school district "replacing Infinite Campus" OR "migrate from Infinite Campus" filetype:pdf',
    'school district "migrate" OR "migration" "student information system" RFP filetype:pdf',
    'school district "system replacement" "student information" RFP filetype:pdf',
    'school district "new SIS" OR "SIS replacement" RFP filetype:pdf',
    'school district "transition" "student information system" RFP filetype:pdf',
    'school district RFP "legacy system" replacement education filetype:pdf',
    'school district "converting from" OR "switching from" student information filetype:pdf',
    'school district "RFP" "implementation services" "student information" filetype:pdf',
    'school district RFP "data conversion" "student information" filetype:pdf',
    'school district RFP "system implementation" student data filetype:pdf',
    'school district "new system" procurement "student data" platform filetype:pdf',
    'school district "needs assessment" "student information system" filetype:pdf',
    'school district "gap analysis" "student information" OR "SIS" filetype:pdf',
    'school district "market research" "student information system" filetype:pdf',
    'school district "vendor evaluation" "student information system" filetype:pdf',
    'school district "due diligence" "student information" software filetype:pdf',
    'school district "selection committee" "student information system" filetype:pdf',
    'school district "scoring rubric" "student information" OR "SIS" filetype:pdf',
    'school district "cost proposal" "student information system" filetype:pdf',

    # === BATCH C: State assessment/testing contracts (15) ===
    '"state assessment" contract RFP "student data" filetype:pdf',
    '"statewide assessment" RFP technology OR platform filetype:pdf',
    '"state testing" contract RFP software OR platform filetype:pdf',
    '"summative assessment" RFP school district software filetype:pdf',
    '"test administration" RFP school district software filetype:pdf',
    '"item bank" RFP education OR school filetype:pdf',
    '"test scoring" RFP school district technology filetype:pdf',
    '"computer adaptive testing" RFP school OR education filetype:pdf',
    '"online testing" platform RFP school district filetype:pdf',
    '"assessment management" system RFP education filetype:pdf',
    '"test delivery" platform RFP school OR education filetype:pdf',
    '"psychometric" services RFP education OR school filetype:pdf',
    '"assessment reporting" system RFP school district filetype:pdf',
    '"standards-based" assessment RFP technology OR platform filetype:pdf',
    '"growth model" OR "growth measure" RFP school district filetype:pdf',

    # === BATCH D: BOCES / IU / RESA / regional agencies (15) ===
    'BOCES RFP technology OR software "student data" filetype:pdf',
    '"intermediate unit" RFP technology OR software filetype:pdf',
    '"education service agency" RFP software OR technology filetype:pdf',
    '"regional education service" RFP software OR technology filetype:pdf',
    'CESA RFP software OR technology education filetype:pdf',
    '"cooperative educational" RFP software OR technology filetype:pdf',
    'ESD RFP "student information" OR "data platform" filetype:pdf',
    '"area education agency" RFP software OR technology filetype:pdf',
    '"educational service unit" RFP software OR technology filetype:pdf',
    '"regional office of education" RFP software OR technology filetype:pdf',
    'RESA RFP technology OR software "student data" filetype:pdf',
    '"service center" education RFP software OR technology filetype:pdf',
    '"joint powers" education RFP software OR technology filetype:pdf',
    'SELPA RFP software OR technology "special education" filetype:pdf',
    '"charter authorizer" RFP software OR technology filetype:pdf',

    # === BATCH E: Specific use cases (20) ===
    'school district RFP "response to intervention" software filetype:pdf',
    'school district RFP "positive behavior" software OR platform filetype:pdf',
    'school district RFP "social emotional" screening OR assessment filetype:pdf',
    'school district RFP "college readiness" platform OR software filetype:pdf',
    'school district RFP "workforce readiness" OR "career readiness" software filetype:pdf',
    'school district RFP "multi-classroom leader" OR "staffing" software filetype:pdf',
    'school district RFP "professional learning" platform OR software filetype:pdf',
    'school district RFP "coaching" platform OR software education filetype:pdf',
    'school district RFP "equity audit" OR "equity" data platform filetype:pdf',
    'school district RFP "after school" OR "extended learning" software filetype:pdf',
    'school district RFP "summer school" management software filetype:pdf',
    'school district RFP "credit recovery" software OR platform filetype:pdf',
    'school district RFP "tutoring" management platform filetype:pdf',
    'school district RFP "mentoring" program management software filetype:pdf',
    'school district RFP "family engagement" platform OR software filetype:pdf',
    'school district RFP "volunteer management" software education filetype:pdf',
    'school district RFP "community partnerships" platform education filetype:pdf',
    'school district RFP "grant management" software education filetype:pdf',
    'school district RFP "accreditation" management software filetype:pdf',
    'school district RFP "program evaluation" software education filetype:pdf',

    # === BATCH F: Different phrasings without filetype (20) ===
    '"unified school district" "request for proposal" "student data" 2025',
    '"independent school district" RFP "assessment platform" 2025',
    '"county school district" RFP "student information" technology 2025',
    '"parish school board" RFP technology OR software 2025',
    '"borough school district" RFP technology OR software 2025',
    '"city school district" RFP "student data" OR technology 2025',
    '"consolidated school district" RFP technology OR software 2025',
    '"community school district" RFP technology OR software 2025',
    '"regional school district" RFP technology OR software 2025',
    '"area school district" RFP technology OR software 2025',
    'K-12 "data warehouse" RFP OR procurement 2025 filetype:pdf',
    'K-12 "student analytics" RFP OR procurement filetype:pdf',
    'K-12 "predictive analytics" RFP OR procurement education filetype:pdf',
    'K-12 "business intelligence" RFP school district filetype:pdf',
    'K-12 "reporting system" RFP school district student filetype:pdf',
    '"school board" approved contract "student information" 2025 filetype:pdf',
    '"school board" approved contract "data analytics" 2025 filetype:pdf',
    '"school board" minutes "student information system" award filetype:pdf',
    '"superintendent" recommendation "student information" contract filetype:pdf',
    '"board resolution" "student information system" contract filetype:pdf',

    # === BATCH G: More medium districts (30) ===
    'Rowan-Salisbury Schools RFP software OR technology filetype:pdf',
    'Cumberland County Schools RFP software OR technology filetype:pdf',
    'Gaston County Schools RFP software OR technology filetype:pdf',
    'Cabarrus County Schools RFP software OR technology filetype:pdf',
    'Iredell-Statesville Schools RFP software OR technology filetype:pdf',
    'Buncombe County Schools RFP software OR technology filetype:pdf',
    'New Hanover County Schools RFP software OR technology filetype:pdf',
    'Pitt County Schools RFP software OR technology filetype:pdf',
    'Onslow County Schools RFP software OR technology filetype:pdf',
    'Greenville County Schools RFP software OR technology filetype:pdf',
    'Richland County Schools RFP software OR technology filetype:pdf',
    'Horry County Schools RFP software OR technology filetype:pdf',
    'Lexington County Schools RFP software OR technology filetype:pdf',
    'Berkeley County Schools RFP software OR technology filetype:pdf',
    'Dorchester County Schools RFP software OR technology filetype:pdf',
    'Mobile County Schools RFP software OR technology filetype:pdf',
    'Madison County Schools Alabama RFP software filetype:pdf',
    'Tuscaloosa City Schools RFP software OR technology filetype:pdf',
    'Hoover City Schools RFP software OR technology filetype:pdf',
    'Baldwin County Schools Alabama RFP software filetype:pdf',
    'Fayette County Schools Kentucky RFP software filetype:pdf',
    'Kenton County Schools RFP software OR technology filetype:pdf',
    'Boone County Schools Kentucky RFP software filetype:pdf',
    'Warren County Schools Kentucky RFP software filetype:pdf',
    'Hardin County Schools Kentucky RFP software filetype:pdf',
    'Hamilton County Schools Tennessee RFP software filetype:pdf',
    'Knox County Schools Tennessee RFP software filetype:pdf',
    'Rutherford County Schools Tennessee RFP software filetype:pdf',
    'Sumner County Schools Tennessee RFP software filetype:pdf',
    'Williamson County Schools Tennessee RFP software filetype:pdf',

    # === BATCH H: Doc/docx and HTML catches (15) ===
    '"school district" RFP "student information" 2024 OR 2025 filetype:doc',
    '"school district" RFP "assessment" platform 2025 filetype:doc',
    '"school district" RFP "data warehouse" 2024 OR 2025 filetype:doc',
    '"school district" RFP "learning management" 2025 filetype:doc',
    '"school board" RFP "student data" 2025 filetype:doc',
    'K-12 RFP "student information system" 2024 2025 filetype:docx',
    'K-12 RFP "data analytics" education 2025 filetype:docx',
    '"public schools" RFP "student information" 2025 filetype:doc',
    '"public schools" RFP "data platform" 2025 filetype:docx',
    '"school district" "invitation to bid" "student data" filetype:doc',
    '"school district" solicitation "assessment" platform filetype:doc',
    '"school district" procurement "data system" student filetype:doc',
    '"school district" RFP "MTSS" OR "RTI" software 2025 filetype:doc',
    '"school district" RFP "early warning" student 2025 filetype:doc',
    '"school district" RFP "enrollment" system 2025 filetype:doc',
]

def load_existing_urls():
    urls = set()
    for f in ["STRIVED_COMPLETE_TEXAS.csv", "STRIVED_ROUND3.csv", "STRIVED_ROUND4.csv", "STRIVED_ROUND5.csv", "STRIVED_ACCUMULATED.csv"]:
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
    if not url or not url.lower().endswith(".pdf"):
        return None
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
    out_df.to_csv(ROUND5_CSV, index=False)
    print(f"\n  Saved {len(out_df)} leads to {ROUND5_CSV}")
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
    print(f"\n  ROUND 5 SUMMARY: {len(verified)} verified, {rel_count} relevant")

def main():
    print(f"\n{'#' * 60}")
    print(f"  STRIVED Round 5 RFP Collection ({len(QUERIES)} queries)")
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
                r"([\w\s\.'-]+(?:ISD|CISD|School District|Public Schools|County Schools|Parish Schools|Academy|Board of Education))",
                f"{title} {body}", re.I)
            all_leads.append({
                "source": "DDGS-Round5", "school_name": school_match.group(1).strip() if school_match else "",
                "title": title[:200], "bid_url": url, "pdf_url": url, "due_date": "", "rfp_id": "",
            })
            new_count += 1
        total_new += new_count
        print(f"{len(results)} results, {new_count} new")
        time.sleep(random.uniform(*DDGS_SLEEP))
    print(f"\n  DDGS Round 5 total: {total_new} new leads")
    if all_leads:
        verified = verify_leads(all_leads)
        save_and_merge(verified)
    else: print("\n  No leads found.")
    print(f"\n{'#' * 60}")
    print(f"  DONE")
    print(f"{'#' * 60}")

if __name__ == "__main__":
    main()
