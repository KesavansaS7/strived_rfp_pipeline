"""
phase4_email.py — TPIA Email Sender for RFP Response Requests

Reads output/RFP.xlsx, generates and sends TPIA (Texas Public Information Act)
emails to school districts requesting bid tabulations and winning proposals
for technology/software RFPs.

Usage:
    # Preview all emails (dry run, no sending)
    python phase4_email.py --preview

    # Send all emails
    python phase4_email.py --send

    # Send to specific row numbers only
    python phase4_email.py --send --rows 0,1,5,10

    # Send with custom sender info
    python phase4_email.py --send --name "John Doe" --title "BD Manager" --company "Strived.io" --phone "555-123-4567"

    # Export all emails to CSV for review before sending
    python phase4_email.py --export

Required environment variables (.env):
    SMTP_EMAIL=kesavan@strived.io
    SMTP_PASSWORD=your-app-password
    SMTP_HOST=smtp.gmail.com        (optional, default: smtp.gmail.com)
    SMTP_PORT=587                   (optional, default: 587)
    SENDER_NAME=Kesavan           (optional, override via --name)
    SENDER_TITLE=Software Engineer         (optional, override via --title)
    SENDER_COMPANY=S7works     (optional, override via --company)
    SENDER_PHONE=Your Phone         (optional, override via --phone)
    SENDER_EMAIL=kesavan@strived.io (optional, defaults to SMTP_EMAIL)
"""

import argparse
import csv
import os
import re
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pandas as pd
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
SMTP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD", os.environ.get("SMTP_PASSWORD", ""))
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SENDER_NAME = os.environ.get("SENDER_NAME", "Kesavan")
SENDER_TITLE = os.environ.get("SENDER_TITLE", "Software Engineer")
SENDER_COMPANY = os.environ.get("SENDER_COMPANY", "S7works")
SENDER_PHONE = os.environ.get("SENDER_PHONE", "")
SENDER_EMAIL = os.environ.get("SENDER_EMAIL", SMTP_EMAIL)

RFP_FILE = os.path.join("output", "RFP.xlsx")
LOG_FILE = os.path.join("output", "email_send_log.csv")
EXPORT_FILE = os.path.join("output", "email_preview.csv")

SEND_DELAY = 10  # seconds between emails to avoid spam flags


def extract_school_name(row):
    """Extract school/district name from school_name, title, or email domain."""
    # Try all text sources for a district/school pattern
    candidates = [
        str(row.get("school_name", "")),
        str(row.get("title", "")),
    ]
    patterns = [
        r"([\w\s\.'-]+(?:Independent School District|Unified School District|School District|Public Schools|County Schools|Parish Schools|City Schools|Board of Education|School Board|High School District|CISD|ISD))",
        r"([\w\s\.'-]+(?:Charter School|Academy|Preparatory School))",
    ]
    for text in candidates:
        text = text.replace("\n", " ").replace("\r", " ")
        for pat in patterns:
            match = re.search(pat, text, re.I)
            if match:
                name = re.sub(r"\s+", " ", match.group(1)).strip()
                if len(name) > 5:
                    return name

    # Clean school_name if present but didn't match patterns
    name = str(row.get("school_name", "")).replace("\n", " ").replace("\r", " ").strip()
    if name and name.lower() != "nan" and len(name) > 3:
        return re.sub(r"\s+", " ", name).strip()

    # Extract from email domain
    emails_str = str(row.get("emails", ""))
    if "@" in emails_str:
        first_email = emails_str.split(";")[0].split(",")[0].strip().rstrip(".")
        if "@" in first_email:
            domain = first_email.split("@")[1]
            domain_name = domain.split(".")[0]
            domain_name = re.sub(r"[-_]", " ", domain_name).title()
            return f"{domain_name} School District"

    return "the School District"


def clean_emails(emails_str):
    """Parse and clean email addresses from the emails field."""
    if not emails_str or str(emails_str).lower() == "nan":
        return []
    emails = []
    for part in re.split(r"[;,]", str(emails_str)):
        addr = part.strip().rstrip(".")
        if re.match(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$", addr):
            if addr.lower() not in [e.lower() for e in emails]:
                emails.append(addr)
    return emails


def create_email_subject(school_name):
    """Generate email subject line."""
    return f"Texas Public Information Act Request — Technology RFP Records — {school_name}"


def create_email_body(school_name, rfp_link, rfp_title, sender_name, sender_title, sender_company, sender_phone, sender_email):
    """Generate the TPIA request email body."""
    signature = f"{sender_name}\n{sender_title} | {sender_company}\n{sender_email}"
    return f"""Dear Purchasing Department,

Under the Texas Public Information Act, Chapter 552 of the Texas Government Code, I am requesting access to or copies of the following public records for {school_name}.

This request pertains to the solicitation referenced here:
Title: {rfp_title}
Link: {rfp_link}

I am requesting:

1. The bid tabulations and scoring rubrics for all technology, software, and IT-related Requests for Proposals (RFPs) awarded within the last 12 months.
2. A copy of the winning proposals/responses for those aforementioned awarded RFPs.

As you may be aware, the Texas Public Information Act requires a response within 10 business days of receipt of this request.

If your agency does not maintain these public records, please let me know who does and include the proper custodian's contact information.

If there are any fees for searching or copying these records, please inform me if the cost will exceed $50. However, I request that these documents be provided in an electronic format (PDF/Excel) delivered via email to avoid unnecessary printing and postage fees.

Thank you for your time and assistance,

{signature}
"""


def load_send_log():
    """Load previously sent emails to avoid duplicates."""
    sent = set()
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = f"{row.get('row_index', '')}_{row.get('to_email', '')}"
                if row.get("status") == "sent":
                    sent.add(key)
    return sent


def log_send(row_index, to_email, school_name, status, error=""):
    """Log email send attempt."""
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "row_index", "to_email", "school_name", "status", "error"])
        from datetime import datetime
        writer.writerow([datetime.now().isoformat(), row_index, to_email, school_name, status, error])


def send_email(smtp_conn, to_emails, subject, body):
    """Send a single email via SMTP."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SENDER_NAME} <{SMTP_EMAIL}>"
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    if SENDER_EMAIL and SENDER_EMAIL != SMTP_EMAIL:
        msg["Reply-To"] = SENDER_EMAIL

    msg.attach(MIMEText(body, "plain", "utf-8"))
    smtp_conn.sendmail(SMTP_EMAIL, to_emails, msg.as_string())


def prepare_all_emails(df, sender_name, sender_title, sender_company, sender_phone, sender_email, row_filter=None):
    """Prepare all email data from the DataFrame."""
    emails_to_send = []
    skipped_no_email = 0

    for idx, row in df.iterrows():
        if row_filter is not None and idx not in row_filter:
            continue

        email_addrs = clean_emails(row.get("emails", ""))
        if not email_addrs:
            skipped_no_email += 1
            continue

        school_name = extract_school_name(row)
        rfp_link = str(row.get("pdf_url", row.get("bid_url", "N/A")))
        rfp_title = str(row.get("title", "Technology RFP"))
        if rfp_title.lower() == "nan":
            rfp_title = "Technology RFP"

        subject = create_email_subject(school_name)
        body = create_email_body(
            school_name, rfp_link, rfp_title,
            sender_name, sender_title, sender_company, sender_phone, sender_email
        )

        emails_to_send.append({
            "row_index": idx,
            "school_name": school_name,
            "to_emails": email_addrs,
            "subject": subject,
            "body": body,
            "rfp_title": rfp_title,
            "rfp_link": rfp_link,
        })

    return emails_to_send, skipped_no_email


def cmd_preview(df, args):
    """Preview all emails without sending."""
    sender_name, sender_title, sender_company, sender_phone, sender_email = get_sender_info(args)
    row_filter = parse_row_filter(args.rows) if args.rows else None
    emails, skipped = prepare_all_emails(df, sender_name, sender_title, sender_company, sender_phone, sender_email, row_filter)

    print(f"\n{'=' * 70}")
    print(f"  EMAIL PREVIEW — {len(emails)} emails ready, {skipped} skipped (no email)")
    print(f"{'=' * 70}\n")

    for i, em in enumerate(emails):
        print(f"--- Email {i+1}/{len(emails)} [Row {em['row_index']}] ---")
        print(f"  To:      {', '.join(em['to_emails'])}")
        print(f"  Subject: {em['subject']}")
        print(f"  School:  {em['school_name']}")
        print(f"  RFP:     {em['rfp_title'][:70]}")
        print(f"  Link:    {em['rfp_link'][:80]}")
        print()
        if i < 2:
            print("  --- Body ---")
            for line in em["body"].split("\n"):
                print(f"  {line}")
            print("  --- End Body ---\n")

    print(f"\nTotal: {len(emails)} emails ready to send")
    print(f"Skipped: {skipped} rows (no email address)")
    print(f"\nTo send, run: python phase4_email.py --send")


def cmd_export(df, args):
    """Export all prepared emails to CSV for review."""
    sender_name, sender_title, sender_company, sender_phone, sender_email = get_sender_info(args)
    emails, skipped = prepare_all_emails(df, sender_name, sender_title, sender_company, sender_phone, sender_email)

    rows = []
    for em in emails:
        rows.append({
            "row_index": em["row_index"],
            "school_name": em["school_name"],
            "to_emails": "; ".join(em["to_emails"]),
            "subject": em["subject"],
            "rfp_title": em["rfp_title"],
            "rfp_link": em["rfp_link"],
            "body": em["body"],
        })

    out_df = pd.DataFrame(rows)
    out_df.to_csv(EXPORT_FILE, index=False)
    print(f"\nExported {len(rows)} email previews to {EXPORT_FILE}")
    print(f"Skipped: {skipped} rows (no email address)")
    print(f"\nReview the file, then run: python phase4_email.py --send")


def cmd_send(df, args):
    """Send emails via SMTP."""
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        print("\nERROR: Set SMTP_EMAIL and SMTP_PASSWORD in your .env file")
        print("\nFor Gmail:")
        print("  1. Enable 2-Factor Authentication on your Google account")
        print("  2. Go to myaccount.google.com/apppasswords")
        print("  3. Generate an App Password for 'Mail'")
        print("  4. Add to .env:")
        print("     SMTP_EMAIL=your-email@gmail.com")
        print("     SMTP_PASSWORD=xxxx-xxxx-xxxx-xxxx")
        return

    sender_name, sender_title, sender_company, sender_phone, sender_email = get_sender_info(args)
    row_filter = parse_row_filter(args.rows) if args.rows else None
    emails, skipped = prepare_all_emails(df, sender_name, sender_title, sender_company, sender_phone, sender_email, row_filter)
    sent_log = load_send_log()

    new_emails = []
    already_sent = 0
    for em in emails:
        all_sent = True
        for addr in em["to_emails"]:
            key = f"{em['row_index']}_{addr}"
            if key not in sent_log:
                all_sent = False
                break
        if all_sent:
            already_sent += 1
        else:
            new_emails.append(em)

    print(f"\n{'=' * 70}")
    print(f"  EMAIL SENDER")
    print(f"{'=' * 70}")
    print(f"  Total prepared:  {len(emails)}")
    print(f"  Already sent:    {already_sent}")
    print(f"  To send now:     {len(new_emails)}")
    print(f"  Skipped (no email): {skipped}")
    print(f"  SMTP: {SMTP_EMAIL} via {SMTP_HOST}:{SMTP_PORT}")
    print(f"{'=' * 70}\n")

    if not new_emails:
        print("Nothing new to send.")
        return

    confirm = input(f"Send {len(new_emails)} emails? (yes/no): ").strip().lower()
    if confirm != "yes":
        print("Cancelled.")
        return

    print(f"\nConnecting to {SMTP_HOST}:{SMTP_PORT}...")
    try:
        smtp = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        smtp.ehlo()
        smtp.starttls()
        smtp.login(SMTP_EMAIL, SMTP_PASSWORD)
        print("Connected and authenticated.\n")
    except Exception as e:
        print(f"SMTP connection failed: {e}")
        return

    sent_count = 0
    fail_count = 0

    for i, em in enumerate(new_emails):
        to_addrs = em["to_emails"]
        try:
            send_email(smtp, to_addrs, em["subject"], em["body"])
            sent_count += 1
            print(f"  [{i+1}/{len(new_emails)}] SENT to {', '.join(to_addrs)} — {em['school_name']}")
            for addr in to_addrs:
                log_send(em["row_index"], addr, em["school_name"], "sent")
        except Exception as e:
            fail_count += 1
            err_msg = str(e)[:100]
            print(f"  [{i+1}/{len(new_emails)}] FAILED {', '.join(to_addrs)} — {err_msg}")
            for addr in to_addrs:
                log_send(em["row_index"], addr, em["school_name"], "failed", err_msg)

        if i < len(new_emails) - 1:
            time.sleep(SEND_DELAY)

    smtp.quit()
    print(f"\n{'=' * 70}")
    print(f"  DONE — Sent: {sent_count}, Failed: {fail_count}")
    print(f"  Log saved to: {LOG_FILE}")
    print(f"{'=' * 70}")


def get_sender_info(args):
    name = args.name if args.name else SENDER_NAME
    title = args.title if args.title else SENDER_TITLE
    company = args.company if args.company else SENDER_COMPANY
    phone = args.phone if args.phone else SENDER_PHONE
    email = args.email if args.email else SENDER_EMAIL
    return name, title, company, phone, email


def parse_row_filter(rows_str):
    """Parse comma-separated row numbers into a set."""
    if not rows_str:
        return None
    return set(int(x.strip()) for x in rows_str.split(",") if x.strip().isdigit())


def main():
    parser = argparse.ArgumentParser(description="TPIA Email Sender for RFP Response Requests")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preview", action="store_true", help="Preview all emails without sending")
    group.add_argument("--send", action="store_true", help="Send emails via SMTP")
    group.add_argument("--export", action="store_true", help="Export email previews to CSV")

    parser.add_argument("--rows", type=str, help="Comma-separated row indices to process (e.g., '0,1,5,10')")
    parser.add_argument("--name", type=str, help="Sender name")
    parser.add_argument("--title", type=str, help="Sender title")
    parser.add_argument("--company", type=str, help="Sender company")
    parser.add_argument("--phone", type=str, help="Sender phone")
    parser.add_argument("--email", type=str, help="Sender reply-to email")

    args = parser.parse_args()

    if not os.path.exists(RFP_FILE):
        print(f"ERROR: {RFP_FILE} not found")
        return

    df = pd.read_excel(RFP_FILE)
    print(f"Loaded {len(df)} RFPs from {RFP_FILE}")

    if args.preview:
        cmd_preview(df, args)
    elif args.export:
        cmd_export(df, args)
    elif args.send:
        cmd_send(df, args)


if __name__ == "__main__":
    main()
