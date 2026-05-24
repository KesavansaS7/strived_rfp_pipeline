"""
Phase 5: Download PDFs, create SQLite metadata DB, build ChromaDB vector index.
Input:  output/phase3_rfps_with_emails.csv (or STRIVED_ALL_VERIFIED_RFPS_WITH_EMAILS.csv)
Output: output/rfp_database.sqlite + output/chroma_rfp_data/

Uses ChromaDB's built-in ONNX embeddings (all-MiniLM-L6-v2) — no torch required.
"""

import os
import re
import sqlite3

import chromadb
import pandas as pd

from config import (
    PHASE3_OUTPUT, EXISTING_VERIFIED_CSV,
    PDF_DOWNLOAD_DIR, SQLITE_DB, CHROMA_DIR,
    PAGES_TO_PARSE,
)
from utils import download_pdf_cached, extract_text_from_pdf, recover_school_name, clean_budget

CHUNK_SIZE = 1000
BUDGET_PATTERN = re.compile(r"\$\s?([\d,]+(?:\.\d{2})?)\s*(?:million|mil|m)?\b", re.IGNORECASE)
DATE_PATTERN = re.compile(
    r"(?:due\s*(?:date|by)?|deadline|submit(?:tal)?\s*(?:date|by)?|proposals?\s*due)"
    r"\s*[:\-]?\s*"
    r"(\w+\s+\d{1,2},?\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4})",
    re.IGNORECASE,
)
RFP_ID_PATTERN = re.compile(r"(?:RFP|RFQ|CSP|BID)\s*#?\s*[:\-]?\s*([\w\-]+\d[\w\-]*)", re.IGNORECASE)


def init_sqlite():
    conn = sqlite3.connect(SQLITE_DB)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS rfp_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school_name TEXT,
            pdf_url TEXT UNIQUE,
            local_file_path TEXT,
            budget TEXT,
            deadline TEXT,
            rfp_id TEXT,
            category TEXT,
            llm_summary TEXT,
            emails TEXT,
            llm_score INTEGER,
            is_indexed INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def extract_metadata(text):
    budget_match = BUDGET_PATTERN.findall(text)
    budgets = []
    for b in budget_match:
        cleaned_val = clean_budget(b.replace(",", ""))
        if cleaned_val:
            budgets.append(cleaned_val)

    date_match = DATE_PATTERN.search(text)
    deadline = date_match.group(1).strip() if date_match else ""

    rfp_match = RFP_ID_PATTERN.search(text)
    rfp_id = rfp_match.group(1).strip() if rfp_match else ""

    return {
        "budget": budgets[0] if budgets else "",
        "deadline": deadline,
        "rfp_id": rfp_id,
    }


def chunk_text(text, size=CHUNK_SIZE):
    words = text.split()
    chunks = []
    current = []
    current_len = 0
    for word in words:
        current.append(word)
        current_len += len(word) + 1
        if current_len >= size:
            chunks.append(" ".join(current))
            current = []
            current_len = 0
    if current:
        chunks.append(" ".join(current))
    return chunks


def save_pdf_local(content, school, index):
    os.makedirs(PDF_DOWNLOAD_DIR, exist_ok=True)
    safe_name = re.sub(r"[^\w\-.]", "_", school)[:50]
    filename = f"{safe_name}_{index}.pdf"
    save_path = os.path.join(PDF_DOWNLOAD_DIR, filename)
    with open(save_path, "wb") as f:
        f.write(content)
    return os.path.abspath(save_path)


def safe_int(value, default=0):
    try:
        if pd.isna(value):
            return default
        return int(float(value))
    except (ValueError, TypeError):
        return default


def main(input_csv=None):
    input_file = input_csv
    if not input_file:
        if os.path.exists(PHASE3_OUTPUT):
            input_file = PHASE3_OUTPUT
        elif os.path.exists(EXISTING_VERIFIED_CSV):
            input_file = EXISTING_VERIFIED_CSV
        else:
            print("No input CSV found. Run Phase 2/3 first.")
            return 0

    print(f"Loading {input_file}...")
    df = pd.read_csv(input_file)

    df_relevant = df[df["is_relevant"] == True]
    print(f"Relevant RFPs to index: {len(df_relevant)}")

    if len(df_relevant) == 0:
        print("No relevant RFPs found.")
        return 0

    conn = init_sqlite()
    cursor = conn.cursor()

    cursor.execute("SELECT pdf_url FROM rfp_documents")
    already_in_db = {row[0] for row in cursor.fetchall()}

    print("Connecting to ChromaDB...")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)
    collection = chroma_client.get_or_create_collection("rfp_collection")

    downloaded = 0
    indexed = 0
    failed = 0

    for i, (_, row) in enumerate(df_relevant.iterrows()):
        url = str(row.get("pdf_url", ""))
        raw_school = str(row.get("school_name", ""))
        llm_summary = str(row.get("llm_summary", ""))
        title = str(row.get("title", ""))

        school = recover_school_name(raw_school, llm_summary, title)

        if url in already_in_db:
            continue

        if not url.startswith("http"):
            continue

        print(f"[{downloaded + failed + 1}/{len(df_relevant) - len(already_in_db)}] {school[:30]} ...")

        try:
            content = download_pdf_cached(url)
            text = extract_text_from_pdf(content, max_pages=PAGES_TO_PARSE)

            if not text.strip():
                print("        -> PDF is blank, skipping")
                failed += 1
                continue

            save_path = save_pdf_local(content, school, i)
            meta = extract_metadata(text)

            llm_score = safe_int(row.get("llm_score", 0))
            emails = str(row.get("emails", ""))
            if emails.lower() == "nan":
                emails = ""

            cursor.execute("""
                INSERT OR IGNORE INTO rfp_documents
                (school_name, pdf_url, local_file_path, budget, deadline, rfp_id, category, llm_summary, emails, llm_score)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                school, url, save_path,
                meta["budget"], meta["deadline"], meta["rfp_id"],
                "education_technology",
                llm_summary if llm_summary.lower() != "nan" else "",
                emails,
                llm_score,
            ))
            conn.commit()
            downloaded += 1

            cursor.execute("SELECT id FROM rfp_documents WHERE pdf_url = ?", (url,))
            rfp_id = cursor.fetchone()[0]

            chunks = chunk_text(text)
            if chunks:
                doc_ids = [f"rfp_{rfp_id}_chunk_{j}" for j in range(len(chunks))]
                metadatas = [{"sqlite_rfp_id": str(rfp_id), "school_name": school} for _ in chunks]

                collection.add(documents=chunks, ids=doc_ids, metadatas=metadatas)

                cursor.execute("UPDATE rfp_documents SET is_indexed = 1 WHERE id = ?", (rfp_id,))
                conn.commit()
                indexed += 1

            print(f"        -> OK | Chunks: {len(chunks)} | Budget: {meta['budget'] or 'N/A'} | Deadline: {meta['deadline'] or 'N/A'}")

        except Exception as e:
            print(f"        -> Failed: {e}")
            failed += 1

    conn.close()

    print(f"\n{'='*50}")
    print(f"PHASE 5 COMPLETE")
    print(f"  PDFs downloaded:  {downloaded}")
    print(f"  PDFs indexed:     {indexed}")
    print(f"  Failed:           {failed}")
    print(f"  SQLite DB:        {SQLITE_DB}")
    print(f"  ChromaDB:         {CHROMA_DIR}")
    print(f"{'='*50}")

    return indexed


if __name__ == "__main__":
    main()
