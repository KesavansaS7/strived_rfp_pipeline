"""
Phase 1: Search for RFP PDFs using DuckDuckGo + Google Custom Search.
Input:  data/nces_master.csv (school district list)
Output: output/phase1_rfp_candidates.csv
"""

import os
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import pandas as pd
import requests
from ddgs import DDGS

from config import (
    NCES_FILE, PHASE1_OUTPUT,
    MAX_RESULTS_PER_QUERY, MAX_SEARCH_WORKERS, SEARCH_SLEEP,
    MAX_RETRIES, RETRY_BACKOFF, SEARCH_YEARS, SEARCH_KEYWORDS,
    REQUEST_TIMEOUT,
    GOOGLE_API_KEY, GOOGLE_CX, GOOGLE_CSE_URL,
)

TEST_MODE = False
TEST_LIMIT = 50

progress_lock = Lock()
csv_lock = Lock()
completed_schools = 0
total_rfps_found = 0


def is_rfp_pdf(url, title, body):
    if not url or not url.lower().endswith(".pdf"):
        return False
    text = f"{title} {body} {url}".lower()
    return sum(1 for k in SEARCH_KEYWORDS if k in text) >= 1


def build_queries(district):
    clean = district.replace(" ISD", "").replace(" CISD", "").replace(" CSD", "").strip()
    queries = []
    for year in SEARCH_YEARS:
        queries.append(f"{district} request for proposal {year} filetype:pdf")
        queries.append(f"{district} RFP {year} filetype:pdf")
        queries.append(f"{clean} Texas RFP {year} filetype:pdf")
    return queries


def search_google_cse(query):
    if not GOOGLE_API_KEY or not GOOGLE_CX:
        return []

    try:
        params = {
            "key": GOOGLE_API_KEY,
            "cx": GOOGLE_CX,
            "q": query,
            "num": min(MAX_RESULTS_PER_QUERY, 10),
        }
        response = requests.get(GOOGLE_CSE_URL, params=params, timeout=REQUEST_TIMEOUT)
        data = response.json()

        results = []
        if "items" in data:
            for item in data["items"]:
                results.append({
                    "href": item.get("link", ""),
                    "title": item.get("title", ""),
                    "body": item.get("snippet", ""),
                })
        return results
    except Exception:
        return []


def search_district(ddgs, district, index, total):
    global completed_schools, total_rfps_found

    results = []
    local_seen = set()

    print(f"\n[{index}/{total}] Searching: {district}")

    for query in build_queries(district):
        time.sleep(random.uniform(*SEARCH_SLEEP))

        search_results = None
        for attempt in range(MAX_RETRIES):
            try:
                search_results = list(ddgs.text(query, max_results=MAX_RESULTS_PER_QUERY))
                break
            except Exception as e:
                wait = RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
                print(f"   Attempt {attempt+1}/{MAX_RETRIES} failed: {e}. Retrying in {wait}s...")
                time.sleep(wait)

        if search_results is None:
            search_results = []

        if GOOGLE_API_KEY and GOOGLE_CX:
            cse_results = search_google_cse(query)
            for r in cse_results:
                search_results.append(r)

        for r in search_results:
            url = r.get("href", "")
            title = r.get("title", "")
            body = r.get("body", "")

            if not url or url in local_seen:
                continue

            if is_rfp_pdf(url, title, body):
                row = {"school_name": district, "pdf_url": url, "title": title, "source": "DDGS+CSE"}
                results.append(row)
                local_seen.add(url)

                with csv_lock:
                    pd.DataFrame([row]).to_csv(PHASE1_OUTPUT, mode="a", header=False, index=False)

    with progress_lock:
        completed_schools += 1
        total_rfps_found += len(results)
        pct = (completed_schools / total) * 100
        print(f"  Done [{completed_schools}/{total}] | Found: {len(results)} | Total: {total_rfps_found} | {pct:.1f}%")

    return results


def load_texas_districts():
    if os.path.exists(NCES_FILE):
        df = pd.read_csv(NCES_FILE, low_memory=False)
        districts = df[df["state"].str.upper() == "TX"]["official_name"].dropna().unique().tolist()
        if TEST_MODE:
            districts = districts[:TEST_LIMIT]
            print(f"TEST MODE: Loaded {len(districts)} Texas districts (limited to {TEST_LIMIT})")
        else:
            print(f"Loaded {len(districts)} Texas districts")
        return districts

    print("NCES file not found, using fallback list")
    return ["Houston ISD", "Dallas ISD", "Austin ISD", "Fort Worth ISD", "San Antonio ISD"]


def main():
    global completed_schools, total_rfps_found
    completed_schools = 0
    total_rfps_found = 0

    districts = load_texas_districts()
    total = len(districts)

    all_results = []
    failed_districts = []
    seen_urls = set()

    pd.DataFrame(columns=["school_name", "pdf_url", "title", "source"]).to_csv(PHASE1_OUTPUT, index=False)

    with DDGS() as ddgs:
        with ThreadPoolExecutor(max_workers=MAX_SEARCH_WORKERS) as executor:
            futures = {
                executor.submit(search_district, ddgs, d, i + 1, total): d
                for i, d in enumerate(districts)
            }
            for future in as_completed(futures):
                district = futures[future]
                try:
                    for r in future.result():
                        if r["pdf_url"] not in seen_urls:
                            all_results.append(r)
                            seen_urls.add(r["pdf_url"])
                except Exception as e:
                    print(f"Failed district: {district} -> {e}")
                    failed_districts.append(district)

    if failed_districts:
        print(f"\nRetrying {len(failed_districts)} failed districts...")
        with DDGS() as ddgs_retry:
            for i, district in enumerate(failed_districts[:], 1):
                try:
                    time.sleep(random.uniform(3, 6))
                    for r in search_district(ddgs_retry, district, i, len(failed_districts)):
                        if r["pdf_url"] not in seen_urls:
                            all_results.append(r)
                            seen_urls.add(r["pdf_url"])
                    failed_districts.remove(district)
                except Exception as e:
                    print(f"Retry failed: {district} -> {e}")

    pd.DataFrame(all_results).to_csv(PHASE1_OUTPUT, index=False)

    print(f"\n{'='*50}")
    print(f"PHASE 1 COMPLETE")
    print(f"  Total unique PDFs: {len(all_results)}")
    print(f"  Failed districts:  {len(failed_districts)}")
    print(f"  Saved to: {PHASE1_OUTPUT}")
    print(f"{'='*50}")

    return len(all_results)


if __name__ == "__main__":
    main()
