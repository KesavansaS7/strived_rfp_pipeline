"""
Phase 4: Send outreach emails via Gmail SMTP.
Input:  output/phase3_rfps_with_emails.csv (is_relevant == True only)
Output: output/email_success_log.txt + output/email_tracking.csv
"""

import os
import time
import random
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import pandas as pd

from config import (
    PHASE3_OUTPUT, EMAIL_LOG, EMAIL_TRACKING_CSV,
    SMTP_EMAIL, SMTP_APP_PASSWORD, SMTP_SERVER, SMTP_PORT,
    EMAIL_TEST_LIMIT,
)
from utils import validate_email


def get_already_sent():
    if not os.path.exists(EMAIL_LOG):
        return set()
    with open(EMAIL_LOG, "r") as f:
        return set(line.strip() for line in f)


def log_success(email):
    with open(EMAIL_LOG, "a") as f:
        f.write(f"{email}\n")


def log_tracking(school_name, email, status):
    row = pd.DataFrame([{
        "school_name": school_name,
        "email": email,
        "status": status,
        "timestamp": pd.Timestamp.now().isoformat(),
    }])
    header = not os.path.exists(EMAIL_TRACKING_CSV)
    row.to_csv(EMAIL_TRACKING_CSV, mode="a", header=header, index=False)


def create_email_body(school_name):
    return f"""Hello Purchasing Department,

I was reviewing the recent RFP postings for {school_name} and wanted to reach out.

Strived.io is an AI-powered K-12 data analytics platform that consolidates fragmented student data from multiple sources - including attendance, assessments, grades, progress monitoring, and edtech tools - into a single hub. We then layer your district's unique frameworks, curriculum, and scope and sequence on top to deliver actionable, classroom-ready insights and recommendations for educators.

Unlike platforms that just show trends, Strived tells educators what to do next - from identifying skill gaps and forming effective student groups to enabling early intervention. Our platform syncs data automatically every night with no manual uploads required.

We are ESSA Tier IV evidence-based and backed by NewSchools Venture Fund, AWS, and Google Cloud.

We believe our platform aligns closely with your requirements and would welcome the opportunity to discuss how we can support your district.

Please let me know if there is a good time this week to connect.

Best regards,

Nadeem Shareef
Strived.io
"""


def send_via_smtp(target_email, subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = SMTP_EMAIL
        msg["To"] = target_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_EMAIL, target_email, msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"   Failed to send to {target_email}: {e}")
        return False


def main(input_csv=None, test_limit=None):
    if not SMTP_EMAIL or not SMTP_APP_PASSWORD:
        print("SMTP_EMAIL or SMTP_APP_PASSWORD not found in .env")
        return 0

    limit = test_limit if test_limit is not None else EMAIL_TEST_LIMIT
    input_file = input_csv or PHASE3_OUTPUT

    print(f"Loading {input_file}...")
    try:
        df = pd.read_csv(input_file)
    except FileNotFoundError:
        print(f"Cannot find {input_file}")
        return 0

    df = df[df["is_relevant"] == True]
    print(f"Relevant RFPs: {len(df)}")

    already_sent = get_already_sent()
    sent_count = 0
    skipped_nan = 0

    print(f"\nStarting SMTP Mailer (sending from {SMTP_EMAIL})...\n")

    for _, row in df.iterrows():
        if limit and sent_count >= limit:
            print(f"\nTest limit of {limit} reached. Stopping.")
            break

        school_name = str(row.get("school_name", ""))
        if school_name.lower() in ("nan", "", "none", "unknown", "unknown district"):
            skipped_nan += 1
            continue

        raw_email_val = row.get("emails")
        if pd.isna(raw_email_val):
            continue

        raw_emails = str(raw_email_val).strip()

        email_list = [e.strip() for e in raw_emails.split(",")]
        valid_emails = [e for e in email_list if validate_email(e)]

        for target in valid_emails:
            if target in already_sent:
                continue

            print(f"Sending to: {target} ({school_name[:30]})...")
            subject = f"Inquiry regarding {school_name} RFP & Procurement"
            body = create_email_body(school_name)

            if send_via_smtp(target, subject, body):
                print("   Sent successfully!")
                log_success(target)
                log_tracking(school_name, target, "sent")
                already_sent.add(target)
                sent_count += 1
                time.sleep(random.uniform(5, 10))
            else:
                log_tracking(school_name, target, "failed")

    print(f"\n{'='*50}")
    print(f"PHASE 4 COMPLETE")
    print(f"  Emails sent this session: {sent_count}")
    print(f"  Skipped (nan school):     {skipped_nan}")
    print(f"{'='*50}")

    return sent_count


if __name__ == "__main__":
    main()
