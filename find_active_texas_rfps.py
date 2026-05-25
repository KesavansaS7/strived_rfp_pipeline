"""
Find Active Texas RFPs for Strived.io — Maximum Coverage

Searches 5 sources for currently-open K-12 education technology RFPs:
  1. ESBD (Texas Electronic State Business Daily)
  2. BidNet Direct (open bids)
  3. DuckDuckGo Search (50+ targeted queries)
  4. Google Custom Search API (if configured)
  5. ESC Website Crawling (20 Education Service Centers)

Then verifies Strived relevance, extracts deadlines, and filters for active-only.

Output: output/STRIVED_ACTIVE_TEXAS_RFPS.csv

Usage:
    python find_active_texas_rfps.py
"""

import asyncio
import os
import re
import random
import sys
import time
from datetime import datetime
from urllib.parse import urljoin

import aiohttp
import pandas as pd
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from groq import Groq
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
from pypdf import PdfReader

sys.path.insert(0, os.path.dirname(__file__))

from config import (
    GROQ_API_KEY, GROQ_MODEL, GROQ_SLEEP, OUTPUT_DIR, NCES_FILE,
    REQUEST_TIMEOUT, SEARCH_KEYWORDS,
    GOOGLE_API_KEY, GOOGLE_CX, GOOGLE_CSE_URL,
    RFP_MANDATORY_TERMS, RFP_SUPPORTING_TERMS, RFP_NEGATIVE_TERMS,
    SOFTWARE_TERMS, EDUCATION_TERMS, PROJECT_TERMS, STRIVED_NEGATIVE_TERMS,
    LLM_SYSTEM_PROMPT,
)
from utils import (
    download_pdf_cached, extract_text_from_pdf,
    extract_emails_from_text, recover_school_name,
    clean_budget, validate_email,
)

ACTIVE_OUTPUT = os.path.join(OUTPUT_DIR, "STRIVED_ACTIVE_TEXAS_RFPS.csv")
PROGRESS_CSV = os.path.join(OUTPUT_DIR, "active_rfp_search_progress.csv")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15",
]

ESBD_NIGP_CODES = ["920", "208", "924", "209", "204", "918"]
ESBD_MAX_PAGES = 10

BIDNET_BASE = "https://www.bidnetdirect.com"
BIDNET_KEYWORDS = [
    "student", "assessment", "software", "technology",
    "data analytics", "education", "learning", "curriculum",
    "instructional", "school district", "ISD",
    "student information", "data platform", "SIS",
    "learning management", "LMS", "edtech",
    "student data", "dashboard", "analytics",
    "professional development", "intervention",
    "early warning", "progress monitoring",
]

DISTRICT_BID_PAGES = [
    ("Houston ISD", "https://www.houstonisd.org/Page/32475"),
    ("Dallas ISD", "https://www.dallasisd.org/Page/964"),
    ("Austin ISD", "https://www.austinisd.org/contractandprocurement/solicitations"),
    ("Fort Worth ISD", "https://www.fwisd.org/purchasing"),
    ("San Antonio ISD", "https://www.saisd.net/page/dept-procurement"),
    ("Cypress-Fairbanks ISD", "https://www.cfisd.net/en/parents-students/departments/purchasing"),
    ("Northside ISD", "https://www.nisd.net/purchasing"),
    ("Katy ISD", "https://www.katyisd.org/Page/4440"),
    ("Fort Bend ISD", "https://www.fortbendisd.com/Page/37529"),
    ("Conroe ISD", "https://www.conroeisd.net/department/purchasing/"),
    ("North East ISD", "https://www.neisd.net/Page/1079"),
    ("Frisco ISD", "https://www.friscoisd.org/departments/purchasing"),
    ("Plano ISD", "https://www.pisd.edu/Page/1927"),
    ("Arlington ISD", "https://www.aisd.net/district/departments/business-services/purchasing/current-bids/"),
    ("Round Rock ISD", "https://www.roundrockisd.org/departments/finance/purchasing/"),
    ("Leander ISD", "https://www.leanderisd.org/purchasing/"),
    ("Klein ISD", "https://www.kleinisd.net/apps/pages/index.jsp?uREC_ID=310285"),
    ("Humble ISD", "https://www.humbleisd.net/Page/66413"),
    ("Clear Creek ISD", "https://www.ccisd.net/departments/purchasing"),
    ("Aldine ISD", "https://www.aldineisd.org/departments/business-and-finance/purchasing/"),
    ("Pasadena ISD", "https://www1.pasadenaisd.org/departments/business___finance/purchasing/bid_information"),
    ("El Paso ISD", "https://www.episd.org/Page/1303"),
    ("Killeen ISD", "https://www.killeenisd.org/departments/finance/purchasing.php"),
    ("Spring Branch ISD", "https://www.springbranchisd.com/departments/business-services/purchasing/bid-opportunities"),
    ("Ector County ISD", "https://www.ectorcountyisd.org/Page/6750"),
    ("Laredo ISD", "https://www.laredoisd.org/Page/4162"),
    ("Garland ISD", "https://www.garlandisd.net/departments/business-financial-services/purchasing"),
    ("Midland ISD", "https://www.midlandisd.net/Page/6891"),
    ("San Angelo ISD", "https://www.saisd.org/Page/27041"),
    ("Waco ISD", "https://www.wacoisd.org/Page/1862"),
    ("Richardson ISD", "https://www.risd.org/departments/purchasing/"),
    ("Tyler ISD", "https://www.tylerisd.org/departments/business-financial-services/purchasing"),
    ("Lubbock ISD", "https://www.lubbockisd.org/Page/19614"),
    ("IDEA Public Schools", "https://ideapublicschools.org/our-story/financials-purchasing/"),
    ("Corpus Christi ISD", "https://www.ccisd.us/Page/6161"),
    ("Amarillo ISD", "https://www.amaisd.org/Page/2048"),
    ("Brownsville ISD", "https://www.bisd.us/Page/12268"),
    ("McAllen ISD", "https://www.mcallenisd.org/Page/4710"),
    ("Edinburg CISD", "https://www.ecisd.us/Page/5280"),
    ("Pharr-San Juan-Alamo ISD", "https://www.psjaisd.us/Page/3960"),
]

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

ESC_PATHS = [
    "/purchasing", "/procurement", "/bids", "/rfps", "/solicitations",
    "/departments/purchasing", "/departments/finance/purchasing",
    "/business-services/purchasing", "/current-bids",
    "/purchasing-cooperative", "/bid-opportunities",
    "/vendor-opportunities", "/cooperative-purchasing",
    "/services/purchasing", "/services/cooperative-purchasing",
]

TECH_TERMS = [
    "software", "technology", "data", "digital", "platform",
    "saas", "cloud", "online", "edtech", "learning management",
    "assessment", "student", "instructional", "curriculum",
    "analytics", "dashboard",
]

DDGS_QUERIES = [
    # Texas-specific 2025-2026
    'Texas school district RFP "student data" 2025 OR 2026 filetype:pdf',
    'Texas school district RFP "data analytics" student 2025 OR 2026 filetype:pdf',
    'Texas school district RFP "data dashboard" 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "student information system" 2025 OR 2026 filetype:pdf',
    'Texas school RFP "learning management system" 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "assessment platform" 2025 OR 2026 filetype:pdf',
    'Texas school district RFP "educational technology" 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP software "student" 2025 OR 2026 filetype:pdf',
    'Texas school RFP "edtech" 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "data integration" 2025 OR 2026 filetype:pdf',
    'Texas school district RFP "attendance" software 2025 OR 2026 filetype:pdf',
    'Texas school district RFP "progress monitoring" 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "formative assessment" 2025 OR 2026 filetype:pdf',
    'Texas school RFP "early warning" OR "intervention" system 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "data warehouse" 2025 OR 2026 filetype:pdf',
    'Texas school RFP "single sign-on" OR "SSO" 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "student enrollment" 2025 OR 2026 filetype:pdf',
    'Texas school district RFP "curriculum" software 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "professional development" platform 2025 OR 2026 filetype:pdf',
    'Texas school RFP "MTSS" OR "RTI" software 2025 OR 2026 filetype:pdf',
    'Texas ISD RFP "STAAR" OR "TEKS" assessment 2025 OR 2026 filetype:pdf',
    'Texas school district "request for proposal" "cloud" "SaaS" 2025 OR 2026 filetype:pdf',
    'Texas ISD "competitive sealed proposal" software 2025 OR 2026 filetype:pdf',
    'Texas school district RFQ software technology 2025 OR 2026 filetype:pdf',

    # Big Texas districts
    'Houston ISD RFP software OR technology OR "student data" 2025 OR 2026 filetype:pdf',
    'Dallas ISD RFP software OR technology OR "student data" 2025 OR 2026 filetype:pdf',
    'Austin ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Fort Worth ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'San Antonio ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Katy ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Cypress-Fairbanks ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Northside ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Fort Bend ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Conroe ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'North East ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Frisco ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Plano ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Arlington ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Garland ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Humble ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Klein ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'El Paso ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Round Rock ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Leander ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Pasadena ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Killeen ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Clear Creek ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Aldine ISD RFP software OR technology 2025 OR 2026 filetype:pdf',
    'IDEA Public Schools RFP software OR technology 2025 OR 2026 filetype:pdf',

    # ESC Region queries
    'Texas "Region 4" ESC RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Texas "Region 10" ESC RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Texas "Region 11" ESC RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Texas "Region 13" ESC RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Texas "Region 20" ESC RFP software OR technology 2025 OR 2026 filetype:pdf',
    'Texas "Region 1" ESC RFP software OR technology 2025 OR 2026 filetype:pdf',

    # Site-specific
    'site:k12.tx.us RFP software OR technology 2025 OR 2026 filetype:pdf',
    'site:boarddocs.com Texas ISD RFP technology 2025 OR 2026',
    'site:governmentnavigator.com Texas school district software 2025 OR 2026',
    'site:bonfirehub.com Texas school district RFP technology 2025 OR 2026',
    'site:bidnetdirect.com Texas school district software 2025 OR 2026 filetype:pdf',
    'site:findrfp.com Texas school district software OR technology 2025 OR 2026',

    # Non-PDF (HTML-posted RFPs)
    'Texas school district "request for proposal" "student information" 2025 OR 2026',
    'Texas ISD RFP "data analytics" "student data" 2025 OR 2026',
    'Texas school district RFP "assessment platform" 2025 OR 2026',
    'Texas ISD RFP "learning management" 2025 OR 2026',

    # Cooperative purchasing portals
    'site:buyboard.com Texas technology software education 2025 OR 2026',
    'site:tips-usa.com technology software education 2025 OR 2026',
    'site:tips-usa.com "student" OR "school" OR "education" software',
    'site:buyboard.com "student data" OR "assessment" OR "analytics"',
    'site:omniapartners.com Texas education technology 2025 OR 2026',
    'site:tcpn.org Texas education software technology',
    'site:1702d.com Texas school district technology',

    # Texas Education Agency / state portals
    'site:tea.texas.gov RFP OR solicitation software technology 2025 OR 2026',
    'site:dir.texas.gov education software technology RFP 2025 OR 2026',
    'site:txsmartbuy.gov education software technology',
    '"Texas Education Agency" RFP software technology 2025 OR 2026',

    # IonWave portals (many TX districts use this)
    'site:ionwave.net Texas school district RFP technology 2025 OR 2026',
    'ionwave.net Texas ISD RFP software OR technology 2025 OR 2026',

    # Broader non-PDF queries (current open RFPs)
    'Texas ISD "currently accepting" OR "now accepting" proposals technology 2025 OR 2026',
    'Texas school district "open solicitation" software OR technology 2026',
    'Texas ISD bid "student assessment" OR "student data" OR "data analytics" 2026',
    'Texas school "request for qualifications" OR RFQ software technology 2026',
    'Texas education "competitive sealed proposal" CSP technology 2025 OR 2026',
    'Texas ISD "invitation to bid" technology software 2025 OR 2026',
    'Region 4 ESC cooperative purchasing technology education 2025 OR 2026',
    'Region 7 ESC cooperative purchasing technology education 2025 OR 2026',

    # More specific Strived use-case queries
    'Texas school district RFP "student performance" OR "student achievement" 2025 OR 2026',
    'Texas ISD RFP "classroom" OR "teacher" analytics platform 2025 OR 2026',
    'Texas school "data-driven" RFP OR bid OR solicitation 2025 OR 2026',
    'Texas ISD "actionable insights" OR "data visualization" education 2025 OR 2026',
    'Texas school RFP "ESSA" OR "accountability" software data 2025 OR 2026',
    'Texas ISD RFP "student engagement" OR "behavior tracking" 2025 OR 2026',

    # National K-12 (may also be relevant)
    'school district RFP "student data analytics" 2025 OR 2026',
    'K-12 RFP "data dashboard" OR "data analytics" 2025 OR 2026',
    'school district RFP "student information system" 2025 OR 2026',
    '"education service center" OR ESC RFP technology software 2025 OR 2026',
    'school district RFP "assessment analytics" OR "formative assessment" 2025 OR 2026',
]

DATE_PATTERNS = [
    re.compile(
        r"(?:proposal[s]?\s*(?:are\s*)?due|due\s*date|deadline|"
        r"submit(?:tal|ted)?\s*(?:date|deadline|by|no\s*later)|"
        r"response[s]?\s*due|bid[s]?\s*due|closing\s*date|"
        r"submission\s*(?:deadline|date|due)|"
        r"proposals?\s*must\s*be\s*(?:received|submitted)|"
        r"must\s*be\s*(?:received|submitted)\s*(?:by|before|no\s*later)|"
        r"received\s*(?:by|before|no\s*later\s*than)|"
        r"no\s*later\s*than|not\s*later\s*than|"
        r"close[sd]?\s*(?:on|date))"
        r"\s*[:\-–—\s]*?"
        r"(\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:proposal[s]?\s*(?:are\s*)?due|due\s*date|deadline|"
        r"submit(?:tal|ted)?\s*(?:date|deadline|by)|"
        r"response[s]?\s*due|bid[s]?\s*due|closing\s*date|"
        r"submission\s*(?:deadline|date|due)|"
        r"no\s*later\s*than|received\s*by)"
        r"\s*[:\-–—\s]*?"
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due|deadline|closing|submission|received|submitted)\s*[:\-–—\s]*?"
        r"(\w+\s+\d{1,2},?\s*\d{4})",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:due|deadline|closing|submission)\s*[:\-–—\s]*?"
        r"(\d{1,2}/\d{1,2}/\d{2,4})",
        re.IGNORECASE,
    ),
]

DATE_FORMATS = [
    "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y",
    "%m/%d/%Y", "%m/%d/%y",
    "%d %B %Y", "%d %b %Y",
    "%B %d,%Y", "%b %d,%Y",
]

BUDGET_PATTERN = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)", re.IGNORECASE)
RFP_ID_PATTERN = re.compile(r"(?:RFP|RFQ|CSP|BID|ITB)\s*#?\s*[:\-]?\s*([\w\-]+\d[\w\-]*)", re.IGNORECASE)


# ============================================================
# HELPERS
# ============================================================

def is_rfp_pdf(url, title, body):
    if not url:
        return False
    url_lower = url.lower()
    if not url_lower.endswith(".pdf") and "filetype=pdf" not in url_lower:
        text = f"{title} {body}".lower()
        rfp_words = ["rfp", "request for proposal", "solicitation", "bid", "procurement"]
        if not any(w in text for w in rfp_words):
            return False
    text = f"{title} {body} {url}".lower()
    return sum(1 for k in SEARCH_KEYWORDS if k in text) >= 1


def scrape_pdfs_from_html(url):
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT, headers={"User-Agent": random.choice(USER_AGENTS)})
        content_type = resp.headers.get("content-type", "")
        if "pdf" in content_type:
            return [url]
        if "html" not in content_type:
            return []
        soup = BeautifulSoup(resp.text, "lxml")
        pdf_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.lower().endswith(".pdf"):
                full = href if href.startswith("http") else urljoin(url, href)
                pdf_links.append(full)
        return pdf_links
    except Exception:
        return []


def is_school_related(text):
    t = text.lower()
    school_words = [
        "school", "isd", "cisd", "district", "education", "student",
        "academy", "charter", "k-12", "learning", "teacher",
        "instruction", "curriculum", "assessment", "esc",
        "instructional", "educational", "classroom", "k12",
        "professional development", "intervention", "literacy",
        "data analytics", "technology solutions", "software",
        "saas", "platform", "dashboard", "lms",
    ]
    return any(w in t for w in school_words)


def parse_date(date_str):
    date_str = re.sub(r"\s+", " ", date_str.strip())
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if 2020 <= dt.year <= 2030:
                return dt
        except ValueError:
            continue
    return None


def extract_deadline_regex(text):
    for pattern in DATE_PATTERNS:
        matches = pattern.findall(text)
        parsed = []
        for match in matches:
            dt = parse_date(match)
            if dt and dt.year >= 2024:
                parsed.append((dt, match))
        if parsed:
            parsed.sort(key=lambda x: x[0], reverse=True)
            return parsed[0]
    return None, ""


def llm_extract_deadline(text, client):
    truncated = text[:8000]
    try:
        chat = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Extract the submission deadline/due date from this RFP document. Reply with ONLY the date in MM/DD/YYYY format. If no deadline is found, reply NONE."},
                {"role": "user", "content": truncated},
            ],
            model=GROQ_MODEL,
            temperature=0.0,
            max_tokens=30,
        )
        reply = chat.choices[0].message.content.strip()
        if reply.upper() == "NONE" or len(reply) > 30:
            return None, ""
        dt = parse_date(reply)
        if dt:
            return dt, reply
        m = re.search(r"(\d{1,2}/\d{1,2}/\d{2,4})", reply)
        if m:
            dt = parse_date(m.group(1))
            if dt:
                return dt, m.group(1)
        m = re.search(r"(\w+\s+\d{1,2},?\s*\d{4})", reply)
        if m:
            dt = parse_date(m.group(1))
            if dt:
                return dt, m.group(1)
        return None, ""
    except Exception:
        return None, ""


def is_actual_rfp(text):
    text_lower = text.lower()
    for neg in RFP_NEGATIVE_TERMS:
        if neg in text_lower:
            return False, f"Negative: '{neg}'"
    has_mandatory = any(t in text_lower for t in RFP_MANDATORY_TERMS)
    support_count = sum(1 for t in RFP_SUPPORTING_TERMS if t in text_lower)
    if has_mandatory and support_count >= 2:
        return True, f"RFP confirmed (mandatory=True, support={support_count})"
    if support_count >= 4:
        return True, f"Likely RFP (support={support_count})"
    return False, f"Not RFP (mandatory={has_mandatory}, support={support_count})"


def keyword_filter(text):
    text_lower = text.lower()
    for neg in STRIVED_NEGATIVE_TERMS:
        if neg in text_lower:
            return False, [], f"Negative: '{neg}'"
    sw = [t for t in SOFTWARE_TERMS if t in text_lower]
    ed = [t for t in EDUCATION_TERMS if t in text_lower]
    pj = [t for t in PROJECT_TERMS if t in text_lower]
    all_hits = sw + ed + pj
    if sw and ed:
        return True, all_hits, "Passed: software + education"
    return False, all_hits, f"Failed: sw={len(sw)}, ed={len(ed)}"


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
# SOURCE 1: ESBD
# ============================================================
async def search_esbd():
    print("\n" + "=" * 60)
    print("  SOURCE 1: ESBD (Texas Electronic State Business Daily)")
    print("=" * 60)

    all_results = []
    seen = set()

    try:
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
                        href = h if h.startswith("http") else "https://www.txsmartbuy.gov" + h

                    text = row.get_text(separator=" ", strip=True)
                    if not is_school_related(text):
                        continue

                    if href in seen:
                        continue
                    seen.add(href)

                    agency_match = re.search(
                        r"([\w\s\.'-]+(?:ISD|CISD|School District|Academy|Public Schools|ESC))",
                        text, re.I)

                    school_name = agency_match.group(1).strip() if agency_match else ""

                    # Visit detail page to find PDF attachments
                    pdf_urls = []
                    if href:
                        try:
                            await page.goto(href, timeout=30000)
                            await page.wait_for_timeout(3000)
                            detail_html = await page.content()
                            detail_soup = BeautifulSoup(detail_html, "lxml")
                            for a in detail_soup.find_all("a", href=True):
                                link_href = a["href"]
                                if link_href.lower().endswith(".pdf"):
                                    full = link_href if link_href.startswith("http") else urljoin(href, link_href)
                                    pdf_urls.append(full)
                        except Exception:
                            pass

                    if pdf_urls:
                        for pdf_url in pdf_urls:
                            if pdf_url not in seen:
                                seen.add(pdf_url)
                                all_results.append({
                                    "source": "ESBD",
                                    "school_name": school_name,
                                    "title": title,
                                    "bid_url": href,
                                    "pdf_url": pdf_url,
                                })
                    else:
                        all_results.append({
                            "source": "ESBD",
                            "school_name": school_name,
                            "title": title,
                            "bid_url": href,
                            "pdf_url": href,
                        })
                    page_hits += 1

                print(f"    Page {pg}: {len(rows)} results, {page_hits} school bids")
                if len(rows) < 24:
                    break
                await asyncio.sleep(random.uniform(1.5, 3.0))

        await browser.close()
        await pw.stop()
    except Exception as e:
        print(f"  ESBD error: {e}")

    print(f"\n  ESBD total: {len(all_results)} school technology bids")
    return all_results


# ============================================================
# SOURCE 2: BIDNET DIRECT (Playwright-based)
# ============================================================
async def search_bidnet():
    print("\n" + "=" * 60)
    print("  SOURCE 2: BidNet Direct (Open Bids - Playwright)")
    print("=" * 60)

    all_results = []
    seen_urls = set()
    seen_pdfs = set()

    # Step 1: Collect solicitation URLs via aiohttp
    listing_urls = []
    sem = asyncio.Semaphore(5)
    headers = {"User-Agent": random.choice(USER_AGENTS)}

    BIDNET_SCOPES = [
        "/texas/solicitations/open-bids",
        "/solicitations/open-bids",
    ]

    async def fetch_listing(session, keyword, page_num, scope="/texas/solicitations/open-bids"):
        url = f"{BIDNET_BASE}{scope}?keywords={keyword}&page={page_num}"
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
            if full_url not in seen_urls:
                seen_urls.add(full_url)
                bids.append({"url": full_url, "title": title[:200]})
        return bids

    connector = aiohttp.TCPConnector(limit=20, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        for scope in BIDNET_SCOPES:
            scope_name = "Texas" if "texas" in scope else "Nationwide"
            print(f"  Scope: {scope_name}")
            for keyword in BIDNET_KEYWORDS:
                page1 = await fetch_listing(session, keyword, 1, scope)
                listing_urls.extend(page1)
                if len(page1) >= 20:
                    tasks = [fetch_listing(session, keyword, p, scope) for p in range(2, 4)]
                    pages = await asyncio.gather(*tasks, return_exceptions=True)
                    for pr in pages:
                        if isinstance(pr, list):
                            listing_urls.extend(pr)
                await asyncio.sleep(random.uniform(0.3, 0.8))

    # Filter to education-related only
    edu_listings = [b for b in listing_urls if is_school_related(b["title"])]
    print(f"  Found {len(listing_urls)} total bids, {len(edu_listings)} edu-related")

    if not edu_listings:
        return all_results

    # Keep edu listings as candidates — process_candidate will analyze the HTML text
    for bid in edu_listings:
        all_results.append({
            "source": "BidNet",
            "school_name": "",
            "title": bid["title"],
            "bid_url": bid["url"],
            "pdf_url": bid["url"],
        })

    print(f"\n  BidNet total: {len(all_results)} edu-related solicitations")
    return all_results


# ============================================================
# SOURCE 3: DDGS + GOOGLE CSE
# ============================================================
def search_ddgs_and_cse():
    print("\n" + "=" * 60)
    print("  SOURCE 3: DuckDuckGo + Google CSE Search")
    print("=" * 60)

    all_results = []
    seen = set()
    total_queries = len(DDGS_QUERIES)

    with DDGS() as ddgs:
        for i, query in enumerate(DDGS_QUERIES):
            print(f"  [{i+1}/{total_queries}] {query[:70]}...", end=" ", flush=True)
            time.sleep(random.uniform(2.0, 4.0))

            found = 0
            for attempt in range(3):
                try:
                    results = list(ddgs.text(query, max_results=50))
                    for r in results:
                        url = r.get("href", "")
                        title = r.get("title", "")
                        body = r.get("body", "")
                        if url in seen:
                            continue
                        if is_rfp_pdf(url, title, body):
                            seen.add(url)
                            all_results.append({
                                "source": "DDGS",
                                "school_name": "",
                                "title": title[:200],
                                "bid_url": url,
                                "pdf_url": url,
                            })
                            found += 1
                    break
                except Exception as e:
                    if attempt < 2:
                        time.sleep(random.uniform(10, 20))
                    else:
                        print(f"FAILED: {e}")

            # Google CSE for the same query
            if GOOGLE_API_KEY and GOOGLE_CX:
                try:
                    params = {
                        "key": GOOGLE_API_KEY, "cx": GOOGLE_CX,
                        "q": query, "num": 10,
                    }
                    resp = requests.get(GOOGLE_CSE_URL, params=params, timeout=REQUEST_TIMEOUT)
                    data = resp.json()
                    if "items" in data:
                        for item in data["items"]:
                            url = item.get("link", "")
                            if url not in seen and is_rfp_pdf(url, item.get("title", ""), item.get("snippet", "")):
                                seen.add(url)
                                all_results.append({
                                    "source": "GoogleCSE",
                                    "school_name": "",
                                    "title": item.get("title", "")[:200],
                                    "bid_url": url,
                                    "pdf_url": url,
                                })
                                found += 1
                except Exception:
                    pass

            print(f"{found} found")

    print(f"\n  DDGS+CSE total: {len(all_results)} unique candidates")
    return all_results


# ============================================================
# SOURCE 4: ESC WEBSITE CRAWLING
# ============================================================
async def search_esc():
    print("\n" + "=" * 60)
    print("  SOURCE 4: ESC Website Crawling (20 regions)")
    print("=" * 60)

    all_results = []
    seen = set()
    sem = asyncio.Semaphore(10)

    async def crawl_one(session, name, base_url):
        async with sem:
            results = []
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
                    if any(t in combined for t in TECH_TERMS):
                        full_url = urljoin(url, href)
                        if full_url not in seen:
                            seen.add(full_url)
                            results.append({
                                "source": "ESC",
                                "school_name": name,
                                "title": text[:200] if text else href.split("/")[-1],
                                "bid_url": full_url,
                                "pdf_url": full_url,
                            })
            return results

    connector = aiohttp.TCPConnector(limit=20, force_close=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [crawl_one(session, name, url) for name, url in ESC_SITES]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(results_list):
            name = ESC_SITES[i][0]
            if isinstance(result, Exception) or not result:
                continue
            for r in result:
                all_results.append(r)
            if result:
                print(f"    {name}: {len(result)} PDFs")

    print(f"\n  ESC total: {len(all_results)} unique PDFs")
    return all_results


# ============================================================
# SOURCE 5: DISTRICT WEBSITE CRAWLING (Playwright)
# ============================================================
async def search_districts():
    print("\n" + "=" * 60)
    print("  SOURCE 5: District Website Crawling (Top 40 districts)")
    print("=" * 60)

    all_results = []
    seen = set()

    try:
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page(user_agent=random.choice(USER_AGENTS))

        for i, (name, base_url) in enumerate(DISTRICT_BID_PAGES):
            try:
                await page.goto(base_url, timeout=20000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                soup = BeautifulSoup(html, "lxml")

                district_pdfs = 0
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    text = a.get_text(strip=True)
                    combined = f"{text} {href}".lower()

                    if not href.lower().endswith(".pdf"):
                        continue

                    rfp_words = ["rfp", "rfq", "bid", "proposal", "solicitation", "csp", "procurement"]
                    if not any(w in combined for w in rfp_words):
                        continue

                    full_url = href if href.startswith("http") else urljoin(base_url, href)
                    if full_url not in seen:
                        seen.add(full_url)
                        all_results.append({
                            "source": "District",
                            "school_name": name,
                            "title": text[:200] if text else href.split("/")[-1],
                            "bid_url": base_url,
                            "pdf_url": full_url,
                        })
                        district_pdfs += 1

                if district_pdfs > 0:
                    print(f"    {name}: {district_pdfs} PDFs")

            except Exception:
                continue
            await asyncio.sleep(random.uniform(0.5, 1.5))

        await browser.close()
        await pw.stop()
    except Exception as e:
        print(f"  District crawl error: {e}")

    print(f"\n  District total: {len(all_results)} unique RFP PDFs")
    return all_results


# ============================================================
# STAGE 2-4: VERIFY + DEADLINE + ENRICH
# ============================================================
def _verify_and_enrich(record, text, client, today):
    """Run verification, deadline extraction, and enrichment on extracted PDF text."""
    school = record["school_name"]
    title = record["title"]

    # Layer 0: RFP check
    is_rfp, rfp_reason = is_actual_rfp(text)
    record["is_rfp"] = is_rfp
    record["rfp_reason"] = rfp_reason
    if not is_rfp:
        record["status"] = f"Not RFP: {rfp_reason}"
        return record

    # Layer 1: Keyword filter
    passed, hits, reason = keyword_filter(text)
    record["keyword_pass"] = passed
    record["keyword_hits"] = ", ".join(hits[:10])
    if not passed:
        record["status"] = f"Keywords: {reason}"
        return record

    # Layer 2: LLM classification
    llm_result = llm_classify(text, client)
    record.update(llm_result)
    time.sleep(GROQ_SLEEP)

    if record["llm_software"] == "YES" and record["llm_education"] == "YES" and record["llm_score"] >= 5:
        record["is_relevant"] = True

    if not record["is_relevant"]:
        record["status"] = f"LLM rejected (score={record['llm_score']})"
        return record

    # Deadline extraction
    result = extract_deadline_regex(text)
    if result and result[0]:
        dt, raw = result
        record["deadline_raw"] = raw
        record["deadline_parsed"] = dt.strftime("%Y-%m-%d")
        record["deadline_source"] = "pdf_regex"
    else:
        dt, raw = llm_extract_deadline(text, client)
        if dt:
            record["deadline_raw"] = raw
            record["deadline_parsed"] = dt.strftime("%Y-%m-%d")
            record["deadline_source"] = "groq_llm"
        time.sleep(2)

    # Active check
    if record["deadline_parsed"]:
        deadline_dt = datetime.strptime(record["deadline_parsed"], "%Y-%m-%d")
        delta = (deadline_dt - today).days
        record["days_remaining"] = delta
        if delta >= 0:
            record["is_active"] = True
    else:
        record["is_active"] = "possibly"
        record["days_remaining"] = ""

    # Enrich
    record["school_name"] = recover_school_name(school, record["llm_summary"], title)
    emails = extract_emails_from_text(text)
    record["emails"] = ", ".join([e for e in emails if validate_email(e)])

    budget_matches = BUDGET_PATTERN.findall(text)
    for b in budget_matches:
        cleaned = clean_budget(b.replace(",", ""))
        if cleaned:
            record["budget"] = cleaned
            break

    rfp_match = RFP_ID_PATTERN.search(text)
    if rfp_match:
        record["rfp_id"] = rfp_match.group(1).strip()

    record["status"] = "Success"
    return record


def process_candidate(row, client, today):
    url = row.get("pdf_url", "") or row.get("bid_url", "")
    school = str(row.get("school_name", ""))
    title = str(row.get("title", ""))
    source = row.get("source", "")

    record = {
        "school_name": school, "pdf_url": url, "bid_url": row.get("bid_url", ""),
        "title": title, "source": source,
        "is_rfp": False, "rfp_reason": "",
        "keyword_pass": False, "keyword_hits": "",
        "llm_software": "", "llm_education": "", "llm_strived_fit": "",
        "llm_score": 0, "llm_summary": "",
        "deadline_raw": "", "deadline_parsed": "", "deadline_source": "",
        "is_active": False, "days_remaining": "",
        "emails": "", "budget": "", "rfp_id": "",
        "is_relevant": False, "status": "",
    }

    if not isinstance(url, str) or not url.startswith("http"):
        record["status"] = "Invalid URL"
        return record

    results = []

    try:
        content = download_pdf_cached(url)
        is_html = content[:20].lstrip().lower().startswith((b"<!doc", b"<html", b"<head", b"<?xml"))

        if is_html:
            # Try to find PDFs linked from this HTML page
            pdf_links = scrape_pdfs_from_html(url)

            if pdf_links:
                for pdf_url in pdf_links[:10]:
                    try:
                        pdf_content = download_pdf_cached(pdf_url)
                        if pdf_content[:20].lstrip().lower().startswith((b"<!doc", b"<html", b"<head", b"<?xml")):
                            continue
                        pdf_text = extract_text_from_pdf(pdf_content, max_pages=999)
                        if not pdf_text.strip():
                            continue
                        sub_record = dict(record)
                        sub_record["pdf_url"] = pdf_url
                        sub_record = _verify_and_enrich(sub_record, pdf_text, client, today)
                        if sub_record["is_relevant"]:
                            results.append(sub_record)
                    except Exception:
                        continue

                if results:
                    return results

            # Fallback: analyze the HTML page text itself for relevance
            html_text = content.decode("utf-8", errors="replace")
            soup = BeautifulSoup(html_text, "lxml")
            page_text = soup.get_text(separator=" ", strip=True)
            if len(page_text) > 500:
                record = _verify_and_enrich(record, page_text, client, today)
                if record["is_relevant"]:
                    return record

            pdf_count = len(pdf_links) if pdf_links else 0
            record["status"] = f"HTML page, {pdf_count} PDFs checked, none relevant"
            return record

        text = extract_text_from_pdf(content, max_pages=999)

        if not text.strip():
            record["status"] = "PDF blank/unreadable"
            return record

        record = _verify_and_enrich(record, text, client, today)

    except Exception as e:
        record["status"] = f"Error: {e}"

    return record


# ============================================================
# MAIN
# ============================================================
async def run_search():
    esbd_results = await search_esbd()
    bidnet_results = await search_bidnet()
    esc_results = await search_esc()
    district_results = await search_districts()
    return esbd_results + bidnet_results + esc_results + district_results


def main():
    today = datetime.now()
    print("=" * 60)
    print("  STRIVED ACTIVE TEXAS RFP FINDER")
    print(f"  Date: {today.strftime('%B %d, %Y')}")
    print("=" * 60)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Stage 1: Search all sources
    print("\n" + "#" * 60)
    print("  STAGE 1: SEARCHING ALL SOURCES")
    print("#" * 60)

    async_results = asyncio.run(run_search())
    ddgs_results = search_ddgs_and_cse()

    all_candidates = async_results + ddgs_results

    # Dedup by URL
    seen_urls = set()
    unique = []
    for r in all_candidates:
        url = r.get("pdf_url", "") or r.get("bid_url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(r)

    print(f"\n{'='*60}")
    print(f"  STAGE 1 COMPLETE: {len(unique)} unique candidates from {len(all_candidates)} total")
    print(f"{'='*60}")

    # Stage 2-4: Verify + Deadline + Enrich
    print("\n" + "#" * 60)
    print("  STAGE 2-4: VERIFY + EXTRACT DEADLINES + ENRICH")
    print("#" * 60 + "\n")

    client = Groq(api_key=GROQ_API_KEY)
    results = []
    relevant_count = 0
    active_count = 0

    for i, candidate in enumerate(unique):
        url = candidate.get("pdf_url", "")
        title = str(candidate.get("title", ""))[:40]
        source = candidate.get("source", "")

        print(f"[{i+1}/{len(unique)}] [{source}] {title}...", end=" ", flush=True)

        outcome = process_candidate(candidate, client, today)
        records = outcome if isinstance(outcome, list) else [outcome]

        for record in records:
            results.append(record)

            if record["is_relevant"]:
                relevant_count += 1
                active_tag = ""
                if record["is_active"] == True:
                    active_count += 1
                    active_tag = f"ACTIVE ({record['days_remaining']}d left)"
                elif record["is_active"] == "possibly":
                    active_tag = "POSSIBLY ACTIVE (no deadline)"
                else:
                    active_tag = f"expired ({record['deadline_raw']})"
                print(f"  RELEVANT score={record['llm_score']} | {active_tag}")
            else:
                print(f"  {record['status'][:50]}")

        # Save progress every 20
        if len(results) % 20 == 0:
            try:
                pd.DataFrame(results).to_csv(PROGRESS_CSV, index=False)
            except PermissionError:
                pass

    # Save full results
    results_df = pd.DataFrame(results)
    try:
        results_df.to_csv(ACTIVE_OUTPUT, index=False)
    except PermissionError:
        alt_path = ACTIVE_OUTPUT.replace(".csv", "_2.csv")
        results_df.to_csv(alt_path, index=False)
        print(f"  (Permission denied on {ACTIVE_OUTPUT}, saved to {alt_path})")

    # Filter active
    active_df = results_df[results_df["is_active"] == True].sort_values("days_remaining")
    possibly_active = results_df[results_df["is_active"] == "possibly"]

    # Summary
    print(f"\n{'='*60}")
    print(f"  SEARCH COMPLETE")
    print(f"{'='*60}")
    print(f"  Total candidates searched:    {len(unique)}")
    print(f"  Strived-relevant:             {relevant_count}")
    print(f"  ACTIVE (deadline >= today):   {len(active_df)}")
    print(f"  POSSIBLY ACTIVE (no deadline):{len(possibly_active)}")
    print(f"  Expired:                      {relevant_count - len(active_df) - len(possibly_active)}")
    print(f"{'='*60}")

    if len(active_df) > 0:
        print(f"\n{'='*60}")
        print(f"  ACTIVE RFPs (deadline >= {today.strftime('%Y-%m-%d')})")
        print(f"{'='*60}\n")
        for _, r in active_df.iterrows():
            print(f"  {str(r['school_name'])[:50]}")
            print(f"    Deadline:  {r['deadline_raw']} ({int(r['days_remaining'])} days left)")
            print(f"    Score:     {r['llm_score']}")
            print(f"    Source:    {r['source']}")
            if r["emails"] and str(r["emails"]) != "nan":
                print(f"    Emails:    {str(r['emails'])[:70]}")
            if r["llm_summary"] and str(r["llm_summary"]) != "nan":
                print(f"    Summary:   {str(r['llm_summary'])[:100]}")
            print(f"    PDF:       {str(r['pdf_url'])[:80]}")
            print()

    if len(possibly_active) > 0:
        print(f"\n{'='*60}")
        print(f"  POSSIBLY ACTIVE (no deadline found - may still be open)")
        print(f"{'='*60}\n")
        for _, r in possibly_active.iterrows():
            print(f"  {str(r['school_name'])[:50]}")
            print(f"    Score:     {r['llm_score']}")
            print(f"    Source:    {r['source']}")
            if r["emails"] and str(r["emails"]) != "nan":
                print(f"    Emails:    {str(r['emails'])[:70]}")
            if r["llm_summary"] and str(r["llm_summary"]) != "nan":
                print(f"    Summary:   {str(r['llm_summary'])[:100]}")
            print(f"    PDF:       {str(r['pdf_url'])[:80]}")
            print()

    print(f"Full results saved to: {ACTIVE_OUTPUT}")
    return len(active_df) + len(possibly_active)


if __name__ == "__main__":
    main()
