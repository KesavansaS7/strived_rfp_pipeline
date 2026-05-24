import os
import sys
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.stdout.reconfigure(encoding="utf-8")

# ============================================================
# API KEYS
# ============================================================
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.3-70b-versatile"

SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_APP_PASSWORD = os.getenv("SMTP_APP_PASSWORD")
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CX = os.getenv("GOOGLE_CX")
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"

# ============================================================
# PATHS (relative to strived_pipeline/)
# ============================================================
BASE_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, ".."))
DATA_DIR = os.path.join(ROOT_DIR, "data")
OUTPUT_DIR = os.path.join(ROOT_DIR, "output")

NCES_FILE = os.path.join(DATA_DIR, "nces_master.csv")

PHASE1_OUTPUT = os.path.join(OUTPUT_DIR, "phase1_rfp_candidates.csv")
PHASE2_OUTPUT = os.path.join(OUTPUT_DIR, "phase2_verified_rfps.csv")
PHASE3_OUTPUT = os.path.join(OUTPUT_DIR, "phase3_rfps_with_emails.csv")
EMAIL_LOG = os.path.join(OUTPUT_DIR, "email_success_log.txt")
PDF_DOWNLOAD_DIR = os.path.join(OUTPUT_DIR, "rfp_pdfs")
SQLITE_DB = os.path.join(OUTPUT_DIR, "rfp_database.sqlite")
CHROMA_DIR = os.path.join(OUTPUT_DIR, "chroma_rfp_data")

EXISTING_VERIFIED_CSV = os.path.join(OUTPUT_DIR, "STRIVED_ALL_VERIFIED_RFPS_WITH_EMAILS.csv")
PDF_CACHE_DIR = os.path.join(OUTPUT_DIR, "pdf_cache")
EMAIL_TRACKING_CSV = os.path.join(OUTPUT_DIR, "email_tracking.csv")

MAX_BUDGET_CAP = 50_000_000

# ============================================================
# SEARCH CONFIG (Phase 1)
# ============================================================
MAX_RESULTS_PER_QUERY = 200
MAX_SEARCH_WORKERS = 3
SEARCH_SLEEP = (1.5, 3.0)
MAX_RETRIES = 3
RETRY_BACKOFF = [5, 15, 30]
SEARCH_YEARS = ["2020", "2021", "2022", "2023", "2024", "2025", "2026"]

SEARCH_KEYWORDS = [
    "rfp", "request for proposal", "request for proposals",
    "rfq", "request for qualifications",
    "bid", "solicitation", "invitation to bid",
    "procurement", "purchasing",
]

# ============================================================
# VERIFICATION CONFIG (Phase 2)
# ============================================================
PAGES_TO_PARSE = 15
REQUEST_TIMEOUT = 15
GROQ_SLEEP = 5

EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"

RFP_MANDATORY_TERMS = [
    "request for proposal", "invitation to bid", "request for qualifications",
    "competitive sealed proposal", "rfp ", "rfq ", "csp ", "solicitation",
    "request for quotes", "proposal due", "scope of work",
]

RFP_SUPPORTING_TERMS = [
    "procurement", "purchasing", "vendor", "contract", "submission deadline",
    "due date", "bidder", "evaluation criteria", "pricing",
    "addendum", "proposer", "terms and conditions", "submittal",
    "instructions to bidders", "award date",
]

RFP_NEGATIVE_TERMS = [
    "board agenda", "meeting minutes", "newsletter", "school calendar",
    "student handbook", "lunch menu", "board meeting", "employee handbook",
    "code of conduct", "evaluation plan", "annual report", "budget summary",
    "grant proposal", "grant application", "research paper",
]

SOFTWARE_TERMS = [
    "software", "saas", "platform", "cloud", "web-based", "web based",
    "technology", "data platform", "data analytics", "application",
    "system integration", "api", "dashboard", "portal", "digital",
    "information system", "database", "online platform", "tech solution",
    "data warehouse", "data visualization", "business intelligence",
    "analytics", "artificial intelligence", "ai-powered", "machine learning",
    "data integration", "interoperability", "single sign-on", "sso",
]

EDUCATION_TERMS = [
    "student data", "student information", "learning management",
    "assessment", "attendance", "curriculum", "edtech", "ed-tech",
    "instructional technology", "data-driven instruction", "data driven",
    "progress monitoring", "academic performance", "classroom", "k-12",
    "student achievement", "learning", "school district", "educator",
    "instruction", "student", "teacher", "school",
    "early intervention", "skill gap", "personalized learning",
    "scope and sequence", "instructional framework", "student grouping",
    "formative assessment", "summative assessment", "benchmark",
    "student enrollment", "student performance", "actionable insights",
    "classroom insights", "data-driven decision", "essa",
]

PROJECT_TERMS = [
    "implementation", "subscription", "license", "configuration",
    "training", "support", "data migration", "hosting", "deployment",
    "managed services", "integration", "onboarding",
    "professional development", "data sync", "interoperability",
]

STRIVED_NEGATIVE_TERMS = [
    "construction", "roofing", "hvac", "plumbing", "electrical wiring",
    "janitorial", "custodial", "food service", "cafeteria",
    "transportation", "school bus", "mowing", "landscaping",
    "demolition", "athletic equipment", "uniforms", "furniture",
    "copier", "insurance", "audit services", "legal services",
    "architectural", "paving", "flooring", "painting contractor",
    "fencing", "playground equipment", "asbestos", "fire alarm",
]

# ============================================================
# LLM PROMPT (Phase 2)
# ============================================================
LLM_SYSTEM_PROMPT = """You are a procurement analyst for Strived.io — an AI-powered K-12 education data analytics platform that consolidates fragmented student data from multiple sources (attendance, assessments, grades, progress monitoring, and edtech tools) into a single hub. It then layers district-specific frameworks on top — including curriculum, scope and sequence, and key guardrails — to deliver actionable, classroom-ready insights and recommendations for educators. Strived doesn't just show trends; it tells educators what to do next.

Key capabilities:
- Smart Inbox: AI-driven suggestions surfaced proactively to educators
- Semantic Layer: maps data against each district's unique instructional models and frameworks
- Automatic nightly data sync (no manual uploads)
- Skill gap identification for personalized learning and student grouping
- Early intervention signals based on data-driven insights
- Cross-platform data unification from multiple EdTech vendors into one place

Strived serves district administrators, school leaders, and classroom teachers. It is ESSA Tier IV evidence-based, backed by NewSchools Venture Fund, AWS, Google Cloud, and Carnegie Mellon, and is a SIIA CODiE award winner.

Analyze this RFP and reply in EXACTLY this format (no extra text):
SOFTWARE: YES or NO
EDUCATION: YES or NO
STRIVED_FIT: YES or NO
SCORE: 1-10
SUMMARY: one line describing what the RFP is requesting"""

# ============================================================
# EMAIL CONFIG (Phase 4)
# ============================================================
EMAIL_TEST_LIMIT = 1

SCHOOL_NAME_PATTERNS = [
    r"([\w\s]+(?:School District|Public Schools|County Schools|Unified School District|City Schools|Parish Schools|Independent School District|Municipal School District|Community Schools))",
    r"([\w\s]+(?:ISD|CISD|CSD|USD|CUSD))\b",
    r"([\w\s]+(?:Academy|Charter School|Preparatory|High School District))",
]
