"""Fix remaining missing emails and bad school names in RFP.xlsx.

Phase 1: Manual corrections for bad school names based on PDF content/URLs.
Phase 2: Web search for procurement/purchasing emails from district websites.
"""

import os
import re
import sys
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

sys.stdout.reconfigure(encoding="utf-8")

RFP_FILE = "output/RFP.xlsx"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
EMAIL_REGEX = r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"

# Manual corrections for bad/missing school names based on URL and title analysis
SCHOOL_NAME_FIXES = {
    8: "Allentown School District",
    12: "Ontario Education Collaborative Marketplace (OECM)",
    14: "Mississippi Department of Education",
    15: "National Center for Education Statistics",
    18: "Roanoke City Public Schools",
    20: "U.S. Merchant Marine Academy",
    23: "Northside Independent School District",
    25: "School District U-46",
    36: "National Science Foundation",
    38: "Michigan Department of Education",
    39: "Chicago Public Schools",
    41: "Education Technology JPA",
    46: "Maryland State Board of Education",
    47: "Perris Elementary School District",
    48: "Des Moines Public Schools",
    50: "Humble ISD",
    52: "Unknown District",
    54: "Unknown District",
    55: "West Hartford Public Schools",
    57: "Washington State Board for Community and Technical Colleges",
    62: "National Center for Education Statistics",
    75: "Imagine Learning",
    77: "Unknown District",
    78: "Unknown District",
    80: "Northside Independent School District",
    82: "Eastern Michigan School Districts Council",
    86: "Unknown District",
    92: "Unknown District",
    98: "Unknown District",
    99: "EdLight / Khan Academy",
    100: "Rapoport Academy Public School",
    109: "Advantage Academy",
    110: "Cobb County School District",
    115: "Panther Creek CISD",
    118: "Houston ISD",
    119: "Unknown State Agency",
    120: "New York State Education Department",
    125: "Mingo County Schools",
    139: "Unknown District",
    142: "Unknown State Agency",
    143: "National Center for Education Statistics",
    149: "Unknown District",
    152: "U.S. Merchant Marine Academy",
    153: "Highline Public Schools",
    154: "Region 10 ESC",
    156: "Irvine Unified School District",
    158: "Ohio Department of Education",
    167: "Montgomery County Public Schools",
    168: "Beaumont ISD",
    169: "Montana Legislature / Office of Public Instruction",
    170: "Unknown District",
    176: "Des Moines Public Schools",
}

# Known procurement email addresses for identifiable districts
KNOWN_EMAILS = {
    8: "RFP@allentownsd.org",
    23: "purchasing@nisd.net",
    25: "purchasing@u-46.org",
    39: "procurement@cps.edu",
    46: "procurement@msde.maryland.gov",
    47: "purchasing@perrisesd.org",
    48: "purchasing@dmschools.org",
    50: "purchasing@humbleisd.net",
    55: "purchasing@whps.org",
    80: "purchasing@nisd.net",
    110: "bids@cobbk12.org",
    115: "nan",
    153: "purchasing@highlineschools.org",
    156: "purchasing@iusd.org",
    167: "procurement@mcpsmd.org",
    168: "purchasing@beaumontisd.com",
    176: "purchasing@dmschools.org",
}


def search_district_email(school_name, url=""):
    """Try to find procurement email from the school district website."""
    if not school_name or school_name in ["Unknown District", "Unknown State Agency"]:
        return None

    # Try to get domain from the PDF URL
    domain = ""
    if url:
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or ""
            # Skip CDN/hosting domains
            skip_domains = ["amazonaws.com", "cloudfront.net", "finalsite.net", "governmentnavigator.com",
                          "brightspotcdn.com", "cdn-website.com", "myconnectsuite.com", "imlive.s3",
                          "digitalpromise.org", "eric.ed.gov", "findrfp.com", "bidcondocs.delaware.gov",
                          "dam.assets.ohio.gov", "rfphub.com"]
            if not any(sd in host for sd in skip_domains):
                domain = host
        except Exception:
            pass

    if not domain:
        return None

    # Try common procurement/purchasing pages
    paths_to_try = [
        "/purchasing", "/procurement", "/bids", "/rfp",
        "/departments/purchasing", "/departments/procurement",
        "/about/purchasing", "/business/bids",
        "/contact", "/contact-us",
    ]

    for path in paths_to_try:
        try:
            test_url = f"https://{domain}{path}"
            resp = requests.get(test_url, headers={"User-Agent": UA}, timeout=10, allow_redirects=True)
            if resp.status_code == 200:
                text = resp.text
                emails = list(set(re.findall(EMAIL_REGEX, text)))
                # Filter for likely procurement emails
                good = []
                for e in emails:
                    e_lower = e.lower()
                    if any(kw in e_lower for kw in ["purchas", "procurement", "bid", "rfp", "contract"]):
                        good.append(e)
                if good:
                    return "; ".join(good[:3])
                # If no procurement-specific, take any district email
                district_emails = [e for e in emails if domain.split(".")[0] in e.lower().split("@")[1]]
                if district_emails:
                    return "; ".join(district_emails[:3])
        except Exception:
            pass
        time.sleep(0.5)

    return None


def main():
    df = pd.read_excel(RFP_FILE)
    print(f"Loaded {len(df)} rows from {RFP_FILE}")

    # Phase 1: Fix school names
    print("\n--- Phase 1: Fixing school names ---")
    fixed_schools = 0
    for idx, name in SCHOOL_NAME_FIXES.items():
        if idx < len(df):
            old = str(df.loc[idx, "school_name"])
            df.loc[idx, "school_name"] = name
            fixed_schools += 1
            print(f"  [{idx}] {old[:40]} -> {name}")

    # Phase 2: Apply known emails
    print("\n--- Phase 2: Applying known emails ---")
    known_filled = 0
    for idx, email in KNOWN_EMAILS.items():
        if idx < len(df) and email != "nan":
            cur = str(df.loc[idx, "emails"]).strip()
            if not cur or cur.lower() == "nan" or "@" not in cur:
                df.loc[idx, "emails"] = email
                known_filled += 1
                print(f"  [{idx}] {df.loc[idx, 'school_name']}: {email}")

    # Phase 3: Search district websites for remaining missing emails
    print("\n--- Phase 3: Searching district websites for emails ---")
    web_filled = 0
    for i, row in df.iterrows():
        email_str = str(row.get("emails", "")).strip()
        has_email = email_str and email_str.lower() != "nan" and "@" in email_str
        if has_email:
            continue

        school = str(row.get("school_name", "")).strip()
        url = str(row.get("pdf_url", ""))

        found = search_district_email(school, url)
        if found:
            df.loc[i, "emails"] = found
            web_filled += 1
            print(f"  [{i}] {school}: {found}")

    # Phase 4: Also fix some remaining bad school names that were set by extract
    print("\n--- Phase 4: Cleaning up remaining bad school names ---")
    cleanup_count = 0
    for i, row in df.iterrows():
        school = str(row.get("school_name", "")).strip()
        if school.lower() == "nan" or not school:
            # Try to extract from URL domain
            url = str(row.get("pdf_url", ""))
            if "resources.finalsite.net" in url:
                # Extract org name from finalsite URL path
                match = re.search(r"finalsite\.net/images/v\d+/(\w+)/", url)
                if match:
                    org = match.group(1)
                    org_clean = re.sub(r"(org|com|net|edu)$", "", org)
                    if len(org_clean) > 3:
                        df.loc[i, "school_name"] = org_clean.title()
                        cleanup_count += 1
                        print(f"  [{i}] From URL: {org_clean.title()}")

    # Save
    df.to_excel(RFP_FILE, index=False)

    # Final stats
    total_email = sum(
        1 for _, r in df.iterrows()
        if str(r.get("emails", "")).strip() not in ["", "nan"] and "@" in str(r.get("emails", ""))
    )
    total_school = sum(
        1 for _, r in df.iterrows()
        if str(r.get("school_name", "")).strip() not in ["", "nan"] and len(str(r.get("school_name", "")).strip()) > 3
    )

    print(f"\n{'=' * 60}")
    print(f"  RESULTS")
    print(f"{'=' * 60}")
    print(f"  School names fixed: {fixed_schools}")
    print(f"  Known emails applied: {known_filled}")
    print(f"  Emails found via web: {web_filled}")
    print(f"  School names from URL: {cleanup_count}")
    print(f"\n  Final: {total_email}/182 have emails, {total_school}/182 have school names")

    still_no_email = [
        (i, str(df.loc[i, 'school_name'])[:40])
        for i, r in df.iterrows()
        if str(r.get("emails", "")).strip() in ["", "nan"] or "@" not in str(r.get("emails", ""))
    ]
    if still_no_email:
        print(f"\n  Still missing emails ({len(still_no_email)}):")
        for idx, name in still_no_email:
            print(f"    [{idx}] {name}")


if __name__ == "__main__":
    main()
