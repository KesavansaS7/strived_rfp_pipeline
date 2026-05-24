"""
Shared utilities for the Strived RFP pipeline.
- PDF caching (download once, reuse across phases)
- School name recovery from LLM summaries
- Email validation
"""

import hashlib
import io
import os
import re

import requests
from pypdf import PdfReader

from config import (
    PDF_CACHE_DIR, REQUEST_TIMEOUT, PAGES_TO_PARSE,
    EMAIL_REGEX, SCHOOL_NAME_PATTERNS, MAX_BUDGET_CAP,
)


def _url_to_cache_path(url):
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return os.path.join(PDF_CACHE_DIR, f"{url_hash}.pdf")


def download_pdf_cached(url):
    os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    cache_path = _url_to_cache_path(url)

    if os.path.exists(cache_path):
        with open(cache_path, "rb") as f:
            return f.read()

    response = requests.get(url, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    with open(cache_path, "wb") as f:
        f.write(response.content)

    return response.content


def extract_text_from_pdf(content, max_pages=None):
    if max_pages is None:
        max_pages = PAGES_TO_PARSE
    reader = PdfReader(io.BytesIO(content))
    text = ""
    for page in reader.pages[:max_pages]:
        extracted = page.extract_text()
        if extracted:
            text += extracted + "\n"
    return text


def extract_emails_from_text(text):
    emails = list(set(re.findall(EMAIL_REGEX, text)))
    cleaned = []
    for e in emails:
        e = e.rstrip(".")
        if e.endswith(".pdf") or e.endswith(".gov.") or e.endswith(".edu."):
            continue
        if "noreply" in e.lower() or "no-reply" in e.lower():
            continue
        cleaned.append(e)
    return cleaned


def recover_school_name(school_name, llm_summary="", title=""):
    if school_name and str(school_name).lower() not in ("nan", "", "none", "unknown"):
        return str(school_name).strip()

    for source_text in [llm_summary, title]:
        if not source_text or str(source_text).lower() == "nan":
            continue
        for pattern in SCHOOL_NAME_PATTERNS:
            match = re.search(pattern, str(source_text), re.IGNORECASE)
            if match:
                name = match.group(1).strip()
                if len(name) > 5:
                    return name

    if title and str(title).lower() != "nan":
        clean = str(title).strip()
        for prefix in ["REQUEST FOR PROPOSAL", "RFP", "Microsoft Word -"]:
            clean = clean.replace(prefix, "").strip(" -_")
        if len(clean) > 5 and len(clean) < 80:
            return clean

    return "Unknown District"


def clean_budget(budget_str):
    if not budget_str:
        return ""
    try:
        amount = float(str(budget_str).replace("$", "").replace(",", ""))
        if amount > MAX_BUDGET_CAP:
            return ""
        if amount < 1000:
            return ""
        return f"${amount:,.2f}"
    except (ValueError, AttributeError):
        return ""


def validate_email(email):
    if not email or not isinstance(email, str):
        return False
    email = email.strip()
    if not re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", email):
        return False
    if email.endswith(".pdf"):
        return False
    if len(email) < 6 or len(email) > 254:
        return False
    return True
