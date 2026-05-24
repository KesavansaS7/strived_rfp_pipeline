"""
Phase 6: RAG query engine over stored RFPs.
Uses ChromaDB for retrieval + Groq LLM for answer generation.
Input:  output/chroma_rfp_data/ + output/rfp_database.sqlite
Output: Interactive Q&A or single-query mode (--query flag)

No torch required — uses ChromaDB's built-in ONNX embeddings.
"""

import argparse
import csv
import sqlite3
import os
import sys

import chromadb
from groq import Groq as GroqClient

from config import SQLITE_DB, CHROMA_DIR, GROQ_API_KEY, GROQ_MODEL, OUTPUT_DIR


def get_sqlite_details(rfp_ids):
    if not os.path.exists(SQLITE_DB):
        return {}

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    details = {}

    for rid in rfp_ids:
        cursor.execute(
            "SELECT school_name, budget, deadline, rfp_id, llm_summary, emails FROM rfp_documents WHERE id = ?",
            (rid,),
        )
        row = cursor.fetchone()
        if row:
            details[rid] = {
                "school_name": row[0], "budget": row[1], "deadline": row[2],
                "rfp_id": row[3], "summary": row[4], "emails": row[5],
            }

    conn.close()
    return details


def query_rfps(question, collection, groq_client, top_k=5):
    results = collection.query(query_texts=[question], n_results=top_k)

    if not results["documents"] or not results["documents"][0]:
        return "No relevant RFP documents found.", {}

    context_parts = []
    rfp_ids = set()
    for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
        rfp_id = meta.get("sqlite_rfp_id", "")
        school = meta.get("school_name", "Unknown")
        rfp_ids.add(rfp_id)
        context_parts.append(f"[Source: {school} (RFP ID: {rfp_id})]\n{doc}")

    context = "\n\n---\n\n".join(context_parts)

    details = get_sqlite_details(rfp_ids)

    metadata_context = ""
    for rid, info in details.items():
        metadata_context += f"\nRFP ID {rid}: {info['school_name']}"
        if info["budget"]:
            metadata_context += f" | Budget: {info['budget']}"
        if info["deadline"]:
            metadata_context += f" | Deadline: {info['deadline']}"
        if info["rfp_id"]:
            metadata_context += f" | RFP#: {info['rfp_id']}"
        if info["summary"]:
            metadata_context += f" | Summary: {info['summary'][:150]}"

    prompt = f"""You are an RFP intelligence assistant for Strived.io — an AI-powered K-12 education data analytics platform that consolidates fragmented student data (attendance, assessments, grades, progress monitoring, edtech tools) into a single hub, layers district-specific frameworks on top, and delivers actionable classroom-ready insights. Key capabilities: Smart Inbox, semantic layer, nightly data sync, skill gap identification, early intervention. ESSA Tier IV evidence-based.

RFP Metadata:
{metadata_context}

RFP Document Excerpts:
---------------------
{context}
---------------------

Based strictly on the context above, answer the following question.
Include relevant details: school name, budget, deadline, RFP ID, and what the RFP is requesting.
If the context doesn't contain enough information, say so.

Question: {question}
Answer:"""

    response = groq_client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=GROQ_MODEL,
        temperature=0.1,
        max_tokens=1000,
    )

    return response.choices[0].message.content.strip(), details


def query_budget_filter(min_budget):
    if not os.path.exists(SQLITE_DB):
        return []

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT school_name, budget, deadline, llm_summary FROM rfp_documents WHERE budget != ''")
    rows = cursor.fetchall()
    conn.close()

    results = []
    for school, budget, deadline, summary in rows:
        try:
            amount = float(budget.replace("$", "").replace(",", ""))
            if amount >= min_budget:
                results.append({
                    "school": school, "budget": budget,
                    "deadline": deadline, "summary": summary,
                })
        except (ValueError, AttributeError):
            pass

    return results


def query_deadline_filter(keyword):
    if not os.path.exists(SQLITE_DB):
        return []

    conn = sqlite3.connect(SQLITE_DB)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT school_name, budget, deadline, llm_summary FROM rfp_documents WHERE deadline != '' AND deadline LIKE ?",
        (f"%{keyword}%",),
    )
    rows = cursor.fetchall()
    conn.close()

    return [
        {"school": r[0], "budget": r[1], "deadline": r[2], "summary": r[3]}
        for r in rows
    ]


def export_results(details):
    export_path = os.path.join(OUTPUT_DIR, "rag_export.csv")
    if not details:
        print("No results to export.")
        return

    with open(export_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["school_name", "budget", "deadline", "rfp_id", "summary", "emails"])
        writer.writeheader()
        for info in details.values():
            writer.writerow(info)

    print(f"Exported {len(details)} results to {export_path}")


def print_answer(answer, details):
    print("-" * 50)
    print("ANSWER")
    print("-" * 50)
    print(answer)

    if details:
        print("\n" + "-" * 50)
        print("SOURCES")
        print("-" * 50)
        for rid, info in details.items():
            print(f"  - {info['school_name']}")
            if info["budget"]:
                print(f"    Budget: {info['budget']}")
            if info["deadline"]:
                print(f"    Deadline: {info['deadline']}")
            if info["emails"]:
                print(f"    Contact: {info['emails'][:80]}")
    print()


def main(query=None):
    if not os.path.exists(CHROMA_DIR):
        print(f"ChromaDB not found at {CHROMA_DIR}. Run Phase 5 first.")
        return

    print("Loading ChromaDB...")
    chroma_client = chromadb.PersistentClient(path=CHROMA_DIR)

    try:
        collection = chroma_client.get_collection("rfp_collection")
    except Exception:
        print("No 'rfp_collection' found. Run Phase 5 first.")
        return

    count = collection.count()
    print(f"Loaded {count} document chunks from ChromaDB")

    print("Connecting to Groq LLM...")
    groq_client = GroqClient(api_key=GROQ_API_KEY)

    if query:
        print(f"\nSearching {count} document chunks...\n")
        answer, details = query_rfps(query, collection, groq_client)
        print_answer(answer, details)
        return

    print("\nReady! Type your questions (type 'quit' to exit).")
    print("Special commands:")
    print("  budget > AMOUNT    — filter RFPs by minimum budget")
    print("  deadline KEYWORD   — filter RFPs by deadline (e.g. 'deadline 2025')")
    print("  export             — export last results to CSV")
    print("  stats              — show database statistics\n")

    last_details = {}

    while True:
        try:
            question = input("Question: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not question or question.lower() in ("quit", "exit", "q"):
            break

        if question.lower() == "stats":
            conn = sqlite3.connect(SQLITE_DB)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM rfp_documents")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM rfp_documents WHERE is_indexed = 1")
            idx = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM rfp_documents WHERE budget != ''")
            has_budget = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM rfp_documents WHERE deadline != ''")
            has_deadline = cursor.fetchone()[0]
            conn.close()
            print(f"\n  Total RFPs: {total} | Indexed: {idx} | With budget: {has_budget} | With deadline: {has_deadline} | Chunks: {count}\n")
            continue

        if question.lower() == "export":
            export_results(last_details)
            continue

        if question.lower().startswith("deadline "):
            keyword = question.split(" ", 1)[1].strip()
            results = query_deadline_filter(keyword)
            if results:
                print(f"\nRFPs with deadline matching '{keyword}':")
                for r in results:
                    print(f"  - {r['school']} | Deadline: {r['deadline']} | Budget: {r['budget'] or 'N/A'}")
                    if r["summary"]:
                        print(f"    {r['summary'][:120]}")
            else:
                print(f"No RFPs found with deadline matching '{keyword}'.")
            print()
            continue

        if question.lower().startswith("budget >"):
            try:
                amount_str = question.split(">")[1].strip().replace("$", "").replace(",", "")
                amount_str = amount_str.replace("k", "000").replace("K", "000")
                amount = float(amount_str)
                results = query_budget_filter(amount)
                if results:
                    print(f"\nRFPs with budget > ${amount:,.0f}:")
                    for r in results:
                        print(f"  - {r['school']} | {r['budget']} | Deadline: {r['deadline'] or 'N/A'}")
                        if r["summary"]:
                            print(f"    {r['summary'][:120]}")
                else:
                    print("No RFPs found with that budget threshold.")
                print()
                continue
            except ValueError:
                pass

        print(f"\nSearching {count} document chunks...\n")

        answer, details = query_rfps(question, collection, groq_client)
        last_details = details

        print_answer(answer, details)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Strived RFP RAG Query Engine")
    parser.add_argument("--query", type=str, help="Single query (non-interactive mode)")
    args = parser.parse_args()

    main(query=args.query)
