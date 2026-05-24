# Strived RFP Intelligence Pipeline

AI-powered pipeline that discovers, verifies, and analyzes K-12 education RFPs (Request for Proposals) relevant to [Strived.io](https://strived.io) - a data analytics platform that consolidates fragmented student data and delivers actionable classroom-ready insights for educators.

---

## Pipeline Architecture

```
NCES District List --> Phase 1: Search --> Phase 2: Verify --> Phase 3: Emails --> Phase 4: Outreach
                         (DuckDuckGo +      (3-layer filter)     (PDF extraction     (Gmail SMTP)
                          Google CSE)                              + validation)
                                                    |
                                                    v
                                              Phase 5: Store --> Phase 6: RAG Query
                                              (SQLite + ChromaDB)   (Ask questions)
```

---

## Phase-by-Phase Flow

### Phase 1: Search (`phase1_search.py`)

**Goal:** Find RFP PDF documents from school districts across the internet.

**How it works:**
1. Loads the NCES (National Center for Education Statistics) master list of school districts from `data/nces_master.csv`
2. Filters for Texas districts (1,000+ districts)
3. For each district, builds search queries like `"{district} RFP 2025 filetype:pdf"` across years 2020-2026
4. Searches using **DuckDuckGo** (primary) and **Google Custom Search API** (secondary, if API key configured)
5. Filters results: only keeps URLs ending in `.pdf` that contain RFP-related keywords (rfp, bid, solicitation, procurement, etc.)
6. Uses **3 parallel workers** with retry logic (3 retries with exponential backoff)
7. Deduplicates URLs across all districts
8. Saves results incrementally (crash-safe)

**Input:** `data/nces_master.csv`
**Output:** `output/phase1_rfp_candidates.csv` (columns: school_name, pdf_url, title, source)

---

### Phase 2: Verify + Score (`phase2_verify.py`)

**Goal:** Confirm each PDF is actually an RFP (not a newsletter or handbook) and score how relevant it is to Strived.

**3-Layer Verification Pipeline:**

**Layer 0 - RFP Verification:**
- Checks for **negative terms** first (board agenda, meeting minutes, lunch menu, student handbook) -> immediate reject
- Checks for **mandatory terms** (request for proposal, invitation to bid, solicitation, scope of work)
- Counts **supporting terms** (procurement, vendor, evaluation criteria, submission deadline)
- Pass condition: at least 1 mandatory term + 2 supporting terms, OR 5+ supporting terms

**Layer 1 - Keyword Filter:**
- Checks for **Strived negative terms** (construction, roofing, HVAC, janitorial, food service) -> immediate reject
- Searches for **software terms** (SaaS, platform, cloud, data analytics, dashboard, API, SSO)
- Searches for **education terms** (student data, assessment, curriculum, K-12, formative assessment, ESSA)
- Pass condition: must have both software AND education keyword hits

**Layer 2 - Groq LLM Classification:**
- Sends first 15,000 characters of PDF text to **Llama 3.3 70B** via Groq API
- LLM acts as a Strived procurement analyst
- Returns: SOFTWARE (YES/NO), EDUCATION (YES/NO), STRIVED_FIT (YES/NO), SCORE (1-10), SUMMARY
- Pass condition: SOFTWARE=YES, EDUCATION=YES, SCORE >= 6

**Additional features:**
- Extracts email addresses from PDF text during verification
- Recovers school names from LLM summaries when original name is missing
- Resume support: skips already-processed URLs on restart
- Saves progress every 10 rows (crash-safe)
- Uses cached PDF downloads (same PDF never downloaded twice)

**Input:** `output/phase1_rfp_candidates.csv`
**Output:** `output/phase2_verified_rfps.csv`

---

### Phase 3: Email Enrichment (`phase3_extract.py`)

**Goal:** Find contact emails for RFPs that Phase 2 missed.

**How it works:**
1. Loads Phase 2 output and identifies RFPs with missing or empty email fields
2. For missing emails: re-downloads PDF (from cache) and parses up to **30 pages** (Phase 2 only does 15)
3. Extracts emails using regex, then validates each one:
   - Filters out `.pdf` emails (false positives from URLs)
   - Filters out `noreply` / `no-reply` addresses
   - Validates format, length (6-254 chars), and domain structure
4. Also re-validates existing emails from Phase 2

**Input:** `output/phase2_verified_rfps.csv`
**Output:** `output/phase3_rfps_with_emails.csv`

---

### Phase 4: Email Outreach (`phase4_email.py`)

**Goal:** Send personalized outreach emails to school districts with relevant RFPs.

**How it works:**
1. Loads Phase 3 output, filters to `is_relevant == True` only
2. Skips rows with invalid school names (nan, Unknown, empty)
3. For each valid email:
   - Validates email format using `validate_email()`
   - Checks against `email_success_log.txt` to prevent duplicate sends
   - Sends personalized email via **Gmail SMTP** (TLS on port 587)
   - Email body describes Strived.io capabilities tailored to the school RFP
4. Logs every send attempt to `email_tracking.csv` (school, email, status, timestamp)
5. Configurable **test limit** (default: 1 email) for safety
6. Random 5-10 second delay between sends to avoid rate limiting

**Input:** `output/phase3_rfps_with_emails.csv`
**Output:** `output/email_success_log.txt` + `output/email_tracking.csv`

---

### Phase 5: Vector Database (`phase5_vectordb.py`)

**Goal:** Store relevant RFPs in a searchable vector database for RAG queries.

**How it works:**
1. Loads Phase 3 output (or existing verified CSV), filters to `is_relevant == True`
2. For each relevant RFP:
   - Downloads PDF using cached downloader
   - Extracts full text from PDF
   - Extracts metadata from text using regex:
     - **Budget**: dollar amounts (validated, capped at $50M)
     - **Deadline**: due date / submission deadline
     - **RFP ID**: RFP/RFQ/BID numbers
   - Recovers school name if missing (from LLM summary or title)
3. Stores metadata in **SQLite** database (`rfp_documents` table)
4. Chunks PDF text into ~1000-character segments
5. Indexes chunks into **ChromaDB** using built-in ONNX embeddings (all-MiniLM-L6-v2)
   - No PyTorch required - runs on any machine
6. Marks indexed documents in SQLite (`is_indexed = 1`)

**Input:** `output/phase3_rfps_with_emails.csv`
**Output:** `output/rfp_database.sqlite` + `output/chroma_rfp_data/`

---

### Phase 6: RAG Query Engine (`phase6_rag.py`)

**Goal:** Ask natural language questions about stored RFPs and get AI-generated answers with sources.

**How it works:**
1. Loads ChromaDB collection and connects to Groq LLM
2. For each question:
   - Searches ChromaDB for the 5 most relevant document chunks (semantic search)
   - Retrieves full metadata from SQLite (school name, budget, deadline, emails)
   - Constructs a prompt with context + metadata + question
   - Sends to **Llama 3.3 70B** via Groq for answer generation
3. Displays answer with source RFPs (school name, budget, deadline, contact)

**Special commands:**
- `budget > AMOUNT` - filter RFPs by minimum budget (e.g., `budget > 100k`)
- `deadline KEYWORD` - filter by deadline text (e.g., `deadline 2026`)
- `export` - save last query results to `output/rag_export.csv`
- `stats` - show database statistics

**Non-interactive mode:**
```bash
python phase6_rag.py --query "Which RFPs are about student assessment platforms?"
```

**Input:** `output/chroma_rfp_data/` + `output/rfp_database.sqlite`
**Output:** Interactive Q&A or single-query response

---

### Active RFP Scanner (`find_active_rfps_deep.py`)

**Goal:** Identify RFPs that are still accepting proposals (deadline has not passed).

**3-Pass Deep Scan:**
1. **Pass 1 - PDF Regex:** Scans all pages of each PDF with broad deadline regex patterns
2. **Pass 2 - Bid URL HTML:** Fetches bid portal HTML pages and extracts deadline info
3. **Pass 3 - Groq LLM:** For remaining unknowns, sends PDF text to LLM and asks for the deadline

Classifies each RFP as **active** (deadline >= today) or **expired**, and shows recently expired RFPs (within 30 days) that may still accept late submissions.

**Output:** `output/STRIVED_ACTIVE_RFPS.csv`

---

## Shared Utilities (`utils.py`)

| Function | Purpose |
|----------|---------|
| `download_pdf_cached()` | Downloads PDFs with MD5-based caching - same PDF never downloaded twice |
| `extract_text_from_pdf()` | Extracts text from PDF bytes using pypdf |
| `extract_emails_from_text()` | Regex email extraction with filtering (no .pdf, no noreply) |
| `validate_email()` | Full email format validation |
| `recover_school_name()` | Recovers missing school names from LLM summaries using regex patterns |
| `clean_budget()` | Validates and formats budget amounts (caps at $50M, filters < $1K) |

---

## Configuration (`config.py`)

All settings in one place:
- **API keys** loaded from `.env` (Groq, Gmail SMTP, Google CSE)
- **File paths** (input/output directories)
- **Search config** (keywords, years, retry settings)
- **Keyword lists** (software terms, education terms, negative terms)
- **LLM system prompt** (Strived product description for the procurement analyst)

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

### Required API Keys

| Key | Source | Required For |
|-----|--------|-------------|
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) | Phase 2 (LLM scoring), Phase 6 (RAG), Active RFP scan |
| `SMTP_EMAIL` | Gmail address | Phase 4 (email outreach) |
| `SMTP_APP_PASSWORD` | [Google App Passwords](https://myaccount.google.com/apppasswords) | Phase 4 (email outreach) |
| `GOOGLE_API_KEY` | Google Cloud Console | Phase 1 (optional, secondary search) |
| `GOOGLE_CX` | [Programmable Search](https://programmablesearchengine.google.com) | Phase 1 (optional, secondary search) |

---

## Quick Start

```bash
# Run full pipeline (Phases 1-5)
python run_pipeline.py --all

# Or run individual phases
python run_pipeline.py --phase 1    # Search
python run_pipeline.py --phase 2    # Verify
python run_pipeline.py --phase 3    # Emails
python run_pipeline.py --phase 4    # Outreach
python run_pipeline.py --phase 5    # Vector DB

# Query your RFP database
python run_pipeline.py --phase 6

# Find active RFPs
python find_active_rfps_deep.py
```

---

## Tech Stack

- **Search:** DuckDuckGo Search + Google Custom Search API
- **PDF Parsing:** pypdf
- **LLM:** Groq (Llama 3.3 70B Versatile)
- **Vector DB:** ChromaDB (built-in ONNX embeddings, no PyTorch needed)
- **Metadata DB:** SQLite
- **Email:** Gmail SMTP (TLS)
