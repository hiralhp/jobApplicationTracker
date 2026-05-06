import json
import os
import re
import base64
import streamlit as st
import sqlite3
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parseaddr
from urllib.parse import urlparse
import pandas as pd
import altair as alt

from email_classifier import classify_email
from email_classifier.config import ATS_DOMAINS

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GMAIL_AVAILABLE = True
except ImportError:
    GMAIL_AVAILABLE = False

DB_PATH = "job_tracker.db"



CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

#MainMenu, footer, header { visibility: hidden; }

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* light lavender-grey background */
[data-testid="stAppViewContainer"] {
    background-color: #f4f4f8;
}
[data-testid="stHeader"] { background: transparent; }

/* gradient top border */
[data-testid="stAppViewContainer"]::before {
    content: '';
    position: fixed;
    top: 0; left: 0; right: 0;
    height: 4px;
    background: linear-gradient(90deg, #6366f1, #a855f7, #ec4899, #f97316, #eab308, #22c55e, #06b6d4);
    z-index: 9999;
}

/* gradient border around the main content block */
.block-container {
    padding-top: 2rem;
    padding-bottom: 2rem;
    max-width: 1350px;
    border: 1.5px solid transparent;
    border-radius: 16px;
    background-clip: padding-box;
    box-shadow: 0 0 0 1.5px rgba(99,102,241,0.15), 0 4px 24px rgba(99,102,241,0.07);
}

/* vertically center all column content, reduce default row gap */
div[data-testid="stHorizontalBlock"] {
    align-items: center;
    gap: 0.25rem;
}

/* tighten Streamlit's default element vertical spacing */
div[data-testid="stVerticalBlock"] > div { margin-bottom: -0.6rem; }

/* row divider */
.row-divider {
    border: none;
    border-top: 1px solid #ebebeb;
    margin: 0;
}

/* age badge pill */
.badge {
    display: inline-block;
    padding: 3px 11px;
    border-radius: 20px;
    font-weight: 700;
    font-size: 0.78rem;
    color: white;
    letter-spacing: 0.03em;
}

/* careers link pill */
.careers-link {
    display: inline-block;
    padding: 3px 11px;
    border-radius: 20px;
    background: #eff6ff;
    color: #2563eb !important;
    font-size: 0.78rem;
    font-weight: 600;
    text-decoration: none;
    border: 1px solid #bfdbfe;
    letter-spacing: 0.01em;
}
.careers-link:hover { background: #dbeafe; }

/* table header */
.tbl-header {
    background: #fafafa;
    border-radius: 10px;
    padding: 9px 8px 9px 4px;
    margin-bottom: 6px;
    border: 1px solid #ebebeb;
}

/* company name */
.company-name {
    font-weight: 600;
    font-size: 0.92rem;
    color: #111827;
    letter-spacing: -0.01em;
}

/* date text */
.date-text {
    font-size: 0.84rem;
    color: #6b7280;
    font-variant-numeric: tabular-nums;
}

/* note text */
.note-text {
    font-size: 0.82rem;
    color: #9ca3af;
    font-style: italic;
}

/* button sizing */
div[data-testid="stButton"] > button {
    font-family: 'Inter', sans-serif !important;
    font-size: 0.8rem;
    padding: 4px 12px;
    border-radius: 8px;
}

/* row hover highlight */
div[data-testid="stHorizontalBlock"]:hover {
    background: rgba(99, 102, 241, 0.04);
    border-radius: 6px;
    transition: background 0.12s;
}

/* sticky column header */
div[data-testid="stVerticalBlock"] div[data-testid="stHorizontalBlock"]:first-of-type {
    position: sticky;
    top: 3rem;
    z-index: 100;
    background: #f4f4f8;
    padding: 4px 0;
}
</style>
"""


# ── Database ──────────────────────────────────────────────────────────────────

def get_conn():
    return sqlite3.connect(DB_PATH)


def _backfill_v2(conn):
    """One-time backfill for schema version 2. Idempotent (uses COALESCE / IS NULL guards)."""
    # Normalize company name + job title
    conn.execute("""
        UPDATE applications SET
            company_normalized   = LOWER(TRIM(company_name)),
            job_title_normalized = LOWER(TRIM(REPLACE(REPLACE(COALESCE(job_title,''),'-',' '),'.',' ')))
        WHERE company_normalized IS NULL
    """)
    # Backfill timing columns from existing decision_date
    conn.execute("""
        UPDATE applications SET rejected_at = COALESCE(rejected_at, decision_date)
        WHERE decision_type = 'Rejected' AND decision_date IS NOT NULL
    """)
    conn.execute("""
        UPDATE applications SET interview_at = COALESCE(interview_at, decision_date)
        WHERE decision_type = 'Interviewing' AND decision_date IS NOT NULL
    """)
    conn.execute("""
        UPDATE applications SET first_response_at = COALESCE(first_response_at, decision_date)
        WHERE decision_date IS NOT NULL AND first_response_at IS NULL
    """)
    # latest_status / latest_status_at
    conn.execute("""
        UPDATE applications SET latest_status =
            COALESCE(CASE WHEN decision_type IN ('Rejected','Interviewing','Offer')
                          THEN decision_type END, 'Applied')
        WHERE latest_status IS NULL
    """)
    conn.execute("""
        UPDATE applications SET latest_status_at = COALESCE(latest_status_at, decision_date, applied_date)
        WHERE latest_status_at IS NULL
    """)
    # Re-link existing email_classifications rows
    label_to_type = {"rejection": "Rejected", "interview": "Interviewing", "offer": "Offer"}
    rows = conn.execute(
        "SELECT id, gmail_msg_id, email_date, label FROM email_classifications "
        "WHERE application_id IS NULL AND label IN ('rejection','interview','offer')"
    ).fetchall()
    for row_id, _, email_date, label in rows:
        decision_type = label_to_type[label]
        candidates = conn.execute(
            "SELECT id FROM applications WHERE decision_date = ? AND decision_type = ?",
            (email_date, decision_type),
        ).fetchall()
        if len(candidates) == 1:
            conn.execute(
                "UPDATE email_classifications SET application_id = ?, match_method = 'backfill_date_type' WHERE id = ?",
                (candidates[0][0], row_id),
            )



def init_db():
    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS companies (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,
                last_checked  DATE,
                interval_days INTEGER NOT NULL DEFAULT 7,
                notes         TEXT    DEFAULT '',
                careers_url   TEXT    DEFAULT ''
            )
        """)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()]
        # add missing careers_url column
        if "careers_url" not in cols:
            conn.execute("ALTER TABLE companies ADD COLUMN careers_url TEXT DEFAULT ''")
        # add missing recruiting_email column
        if "recruiting_email" not in cols:
            conn.execute("ALTER TABLE companies ADD COLUMN recruiting_email TEXT DEFAULT ''")
        if "last_applied" not in cols:
            conn.execute("ALTER TABLE companies ADD COLUMN last_applied DATE")
        if "last_scraped" in cols and "last_checked" not in cols:
            conn.execute("ALTER TABLE companies RENAME COLUMN last_scraped TO last_checked")
        # one-time backfill: move existing checked dates into last_applied (they came from the old "Date Applied" form)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            conn.execute("UPDATE companies SET last_applied = last_checked WHERE last_applied IS NULL AND last_checked IS NOT NULL")
            conn.execute("PRAGMA user_version = 1")
        # migrate: drop NOT NULL on last_scraped if it exists
        schema = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='companies'").fetchone()[0]
        if "last_scraped  DATE    NOT NULL" in schema or "last_scraped DATE NOT NULL" in schema:
            conn.execute("ALTER TABLE companies RENAME TO companies_old")
            conn.execute("""
                CREATE TABLE companies (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    name          TEXT    NOT NULL,
                    last_checked  DATE,
                    interval_days INTEGER NOT NULL DEFAULT 7,
                    notes         TEXT    DEFAULT '',
                    careers_url   TEXT    DEFAULT ''
                )
            """)
            conn.execute("INSERT INTO companies SELECT * FROM companies_old")
            conn.execute("DROP TABLE companies_old")
        # applications log — one row per application (multiple per company)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS applications (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT    NOT NULL,
                job_title    TEXT,
                applied_date DATE,
                email_subject TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # migrate: add decision columns if missing
        app_cols = [r[1] for r in conn.execute("PRAGMA table_info(applications)").fetchall()]
        if "decision_date" not in app_cols:
            conn.execute("ALTER TABLE applications ADD COLUMN decision_date DATE")
        if "decision_type" not in app_cols:
            conn.execute("ALTER TABLE applications ADD COLUMN decision_type TEXT")
        new_app_cols = {
            "company_normalized":         "TEXT",
            "job_title_normalized":       "TEXT",
            "latest_status":              "TEXT DEFAULT 'Applied'",
            "latest_status_at":           "DATE",
            "rejected_at":                "DATE",
            "interview_at":               "DATE",
            "first_response_at":          "DATE",
            "source_confirmation_msg_id": "TEXT",
            "source_rejection_msg_id":    "TEXT",
            "source_interview_msg_id":    "TEXT",
        }
        for col, typedef in new_app_cols.items():
            if col not in app_cols:
                conn.execute(f"ALTER TABLE applications ADD COLUMN {col} {typedef}")
        # job_postings table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS job_postings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                company     TEXT    NOT NULL,
                role        TEXT    NOT NULL,
                url         TEXT    DEFAULT '',
                date_added  DATE    NOT NULL,
                status      TEXT    NOT NULL DEFAULT 'To Do',
                notes       TEXT    DEFAULT ''
            )
        """)
        # email classifications log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_classifications (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                gmail_msg_id    TEXT    NOT NULL,
                gmail_thread_id TEXT,
                email_date      DATE    NOT NULL,
                sender          TEXT    NOT NULL DEFAULT '',
                subject         TEXT    NOT NULL DEFAULT '',
                label           TEXT    NOT NULL,
                confidence      REAL    NOT NULL,
                evidence        TEXT    NOT NULL DEFAULT '',
                classified_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                classifier_ver  TEXT    NOT NULL DEFAULT '1.0'
            )
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_email_clf_msg
                ON email_classifications (gmail_msg_id)
        """)
        clf_cols = [r[1] for r in conn.execute("PRAGMA table_info(email_classifications)").fetchall()]
        for col, typedef in [("application_id", "INTEGER"), ("match_method", "TEXT")]:
            if col not in clf_cols:
                conn.execute(f"ALTER TABLE email_classifications ADD COLUMN {col} {typedef}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_app_company_norm  ON applications (company_normalized)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_app_latest_status ON applications (latest_status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_clf_app_id        ON email_classifications (application_id)")
        # Q&A bank — company-specific and generic application answers
        conn.execute("""
            CREATE TABLE IF NOT EXISTS company_qa (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                company    TEXT NOT NULL DEFAULT '',
                question   TEXT NOT NULL,
                answer     TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            _backfill_v2(conn)
            conn.execute("PRAGMA user_version = 2")
        conn.commit()



@st.cache_data
def get_companies():
    with get_conn() as conn:
        return conn.execute(
            """SELECT id, name, last_checked, interval_days, notes, careers_url, recruiting_email, last_applied FROM companies ORDER BY
               CASE WHEN last_checked IS NULL AND last_applied IS NULL THEN 0 ELSE 1 END ASC,
               CASE
                 WHEN last_checked IS NULL THEN last_applied
                 WHEN last_applied IS NULL THEN last_checked
                 WHEN last_checked > last_applied THEN last_checked
                 ELSE last_applied
               END ASC,
               name ASC"""
        ).fetchall()


def add_company(name, last_applied=None, notes="", careers_url="", recruiting_email=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO companies (name, last_applied, interval_days, notes, careers_url, recruiting_email) VALUES (?, ?, 7, ?, ?, ?)",
            (name, last_applied, notes, careers_url, recruiting_email),
        )
        conn.commit()
    get_companies.clear()


def log_application(company_name, job_title, applied_date, email_subject="", gmail_msg_id=None):
    """Insert one row into the applications log (no-op if duplicate)."""
    with get_conn() as conn:
        if gmail_msg_id:
            exists = conn.execute(
                "SELECT 1 FROM applications WHERE source_confirmation_msg_id = ?",
                (gmail_msg_id,),
            ).fetchone()
            if exists:
                return
        exists = conn.execute(
            "SELECT 1 FROM applications WHERE LOWER(company_name)=LOWER(?) AND applied_date=? "
            "AND LOWER(COALESCE(job_title,''))=LOWER(COALESCE(?,''))",
            (company_name, applied_date, job_title),
        ).fetchone()
        if not exists:
            norm_company = company_name.lower().strip() if company_name else None
            norm_title   = _normalize_title(job_title)
            conn.execute(
                """INSERT INTO applications
                   (company_name, job_title, applied_date, email_subject,
                    company_normalized, job_title_normalized,
                    latest_status, latest_status_at, source_confirmation_msg_id)
                   VALUES (?, ?, ?, ?, ?, ?, 'Applied', ?, ?)""",
                (company_name, job_title or None, applied_date, email_subject or None,
                 norm_company, norm_title, applied_date, gmail_msg_id),
            )
            conn.commit()
            _load_stats_data.clear()


def update_application_decision(company_name, decision_type, decision_date,
                                application_id=None, gmail_msg_id=None):
    """Set decision columns on the matching application row."""
    with get_conn() as conn:
        if application_id is None:
            open_apps = conn.execute(
                "SELECT id FROM applications WHERE LOWER(company_name)=LOWER(?) AND decision_date IS NULL",
                (company_name,),
            ).fetchall()
            if len(open_apps) == 0:
                return
            if len(open_apps) >= 2:
                return  # ambiguous — don't guess
            application_id = open_apps[0][0]

        # Sanity check: don't apply a decision dated before the application itself.
        row = conn.execute(
            "SELECT applied_date FROM applications WHERE id = ?", (application_id,)
        ).fetchone()
        if row and row[0] and decision_date and decision_date < row[0]:
            return

        set_clauses = [
            "decision_date = ?", "decision_type = ?",
            "latest_status = ?", "latest_status_at = ?",
        ]
        params = [decision_date, decision_type, decision_type, decision_date]

        if decision_type == "Rejected":
            set_clauses.append("rejected_at = COALESCE(rejected_at, ?)")
            params.append(decision_date)
            if gmail_msg_id:
                set_clauses.append("source_rejection_msg_id = COALESCE(source_rejection_msg_id, ?)")
                params.append(gmail_msg_id)
        elif decision_type == "Interviewing":
            set_clauses.append("interview_at = COALESCE(interview_at, ?)")
            params.append(decision_date)
            if gmail_msg_id:
                set_clauses.append("source_interview_msg_id = COALESCE(source_interview_msg_id, ?)")
                params.append(gmail_msg_id)

        set_clauses.append("first_response_at = COALESCE(first_response_at, ?)")
        params.append(decision_date)
        params.append(application_id)

        conn.execute(
            f"UPDATE applications SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        conn.commit()
    _load_stats_data.clear()


def mark_scraped(company_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_checked = ? WHERE id = ?",
            (date.today().isoformat(), company_id),
        )
        conn.commit()
    get_companies.clear()



def mark_all_company_applied(company_name):
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE job_postings SET status = 'Applied' WHERE LOWER(company) = LOWER(?) AND status != 'Rejected'",
            (company_name,),
        )
        conn.execute(
            "UPDATE companies SET last_applied = ? WHERE LOWER(name) = LOWER(?)",
            (today, company_name),
        )
        conn.commit()
    get_companies.clear()
    get_postings.clear()


def update_company(company_id, notes, careers_url, recruiting_email=""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET notes = ?, careers_url = ?, recruiting_email = ? WHERE id = ?",
            (notes, careers_url, recruiting_email, company_id),
        )
        conn.commit()
    get_companies.clear()


def delete_company(company_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        conn.commit()
    get_companies.clear()


def delete_latest_application(company_id, company_name):
    """Delete only the most recent applications entry for this company.

    Keeps the company row in the tracker; updates last_applied to the new
    most-recent applied_date (or NULL if no entries remain).
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM applications WHERE LOWER(company_name)=LOWER(?)"
            " ORDER BY id DESC LIMIT 1",
            (company_name,),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM applications WHERE id = ?", (row[0],))
        new_latest = conn.execute(
            "SELECT MAX(applied_date) FROM applications WHERE LOWER(company_name)=LOWER(?)",
            (company_name,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE companies SET last_applied = ? WHERE id = ?",
            (new_latest, company_id),
        )
        conn.commit()
    get_companies.clear()
    _load_stats_data.clear()


# ── Q&A bank ──────────────────────────────────────────────────────────────────

@st.cache_data
def get_qa():
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, company, question, answer FROM company_qa ORDER BY company, id"
        ).fetchall()


def add_qa(company, question, answer=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO company_qa (company, question, answer) VALUES (?, ?, ?)",
            (company.strip(), question.strip(), answer.strip()),
        )
        conn.commit()
    get_qa.clear()


def update_qa(qa_id, answer):
    with get_conn() as conn:
        conn.execute(
            "UPDATE company_qa SET answer = ? WHERE id = ?",
            (answer.strip(), qa_id),
        )
        conn.commit()
    get_qa.clear()


def delete_qa(qa_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM company_qa WHERE id = ?", (qa_id,))
        conn.commit()
    get_qa.clear()


# ── Gmail constants ───────────────────────────────────────────────────────────

GMAIL_SCOPES     = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"

# Gmail subject-search terms used to widen the fetch beyond ATS domains
_GMAIL_SUBJECT_TERMS = [
    'subject:("thank you for applying")',
    'subject:("thanks for applying")',
    'subject:("thank you for your application")',
    'subject:("thanks for your application")',
    'subject:("thank you for your interest")',
    'subject:("thanks for your interest")',
    'subject:("application received")',
    'subject:("application confirmation")',
    'subject:("we received your application")',
    # Additional patterns for non-ATS senders (DoorDash, Amazon, etc.)
    'subject:("your application has been received")',
    'subject:("application has been received")',
    'subject:("application submitted")',
    'subject:("successfully applied")',
    'subject:("we\'ve received your application")',
    'subject:("thank you for submitting")',
    'subject:("thanks for submitting")',
]

_STATUS_RANK = {"To Do": 0, "In Progress": 1, "Applied": 2, "Interviewing": 3, "Offer": 4}


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(d):
    return date.fromisoformat(d) if isinstance(d, str) else d


def days_ago(last_scraped):
    if not last_scraped:
        return None
    return (date.today() - parse_date(last_scraped)).days


def _unwrap_forwarded(subject, sender, body):
    """If this is a forwarded email, return the original subject, sender, and body."""
    if not re.match(r'^fwd?:\s*', subject, re.IGNORECASE):
        return subject, sender, body
    # Extract original headers from the forwarded block
    fwd_match = re.search(r'-{3,}\s*Forwarded message\s*-{3,}', body, re.IGNORECASE)
    if not fwd_match:
        return re.sub(r'^fwd?:\s*', '', subject, flags=re.IGNORECASE), sender, body
    fwd_block = body[fwd_match.start():]
    orig_from    = re.search(r'From:\s*(.+)', fwd_block)
    orig_subject = re.search(r'Subject:\s*(.+)', fwd_block)
    orig_body    = body[fwd_match.end():]
    eff_sender  = orig_from.group(1).strip()    if orig_from    else sender
    eff_subject = orig_subject.group(1).strip() if orig_subject else re.sub(r'^fwd?:\s*', '', subject, flags=re.IGNORECASE)
    return eff_subject, eff_sender, orig_body


def _favicon_domain(company_name, careers_url):
    """Return the best domain to fetch a favicon for, preferring careers URL then name guess."""
    if careers_url:
        domain = urlparse(careers_url).netloc
        if domain:
            return domain
    # Strip common suffixes/words and guess domain from name
    slug = re.sub(r"[^a-z0-9]", "", company_name.lower())
    return f"{slug}.com"


def relative_date(date_str):
    d = days_ago(date_str)
    if d is None:   return None
    if d == 0:      return "today"
    if d == 1:      return "yesterday"
    if d < 7:       return f"{d} days ago"
    if d < 14:      return "1 week ago"
    if d < 30:      return f"{d // 7} weeks ago"
    if d < 60:      return "1 month ago"
    return f"{d // 30} months ago"


def staleness_color(days):
    """Bright green (#00c853) at 1d → bright red (#ff1744) at 40d, clamped."""
    if days is None:
        return "#d1d5db"
    t = max(0.0, min(1.0, (days - 1) / 39))
    r = int(0   + t * (255 - 0))
    g = int(200 + t * (23  - 200))
    b = int(83  + t * (68  - 83))
    return f"rgb({r},{g},{b})"


# ── Gmail helpers ─────────────────────────────────────────────────────────────

def _get_gmail_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as fh:
            fh.write(creds.to_json())
    return creds


def trash_gmail_message(msg_id):
    creds = _get_gmail_credentials()
    if not creds:
        raise RuntimeError("Gmail credentials unavailable")
    svc = build("gmail", "v1", credentials=creds)
    svc.users().messages().trash(userId="me", id=msg_id).execute()


def trash_gmail_thread(thread_id):
    creds = _get_gmail_credentials()
    if not creds:
        raise RuntimeError("Gmail credentials unavailable")
    svc = build("gmail", "v1", credentials=creds)
    svc.users().threads().trash(userId="me", id=thread_id).execute()


def _strip_html(html: str) -> str:
    """Strip HTML tags and return readable plain text."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self._parts: list = []
            self._skip = False

        def handle_starttag(self, tag, attrs):
            if tag in ("style", "script", "head"):
                self._skip = True
            elif tag in ("br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4"):
                self._parts.append("\n")

        def handle_endtag(self, tag):
            if tag in ("style", "script", "head"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                self._parts.append(data)

    try:
        parser = _Stripper()
        parser.feed(html)
        text = "".join(parser._parts)
    except Exception:
        return ""
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_email_body(payload, max_chars=3000):
    """Extract body text from a Gmail message payload.

    Prefers text/plain; falls back to stripped text/html when no plain-text
    part exists (common for HTML-only corporate/ATS emails).
    For multipart/alternative, RFC 2046 guarantees plain comes before HTML in
    the parts list, so iterating in order naturally picks plain first.
    """
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")[:max_chars]
    if mime == "text/html":
        data = payload.get("body", {}).get("data", "")
        if data:
            html = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")
            return _strip_html(html)[:max_chars]
    if mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_email_body(part, max_chars)
            if text:
                return text
    return ""


def _match_company(sender, subject, companies, body=""):
    _, addr = parseaddr(sender)
    addr_l  = addr.lower()
    domain  = addr_l.split("@")[-1] if "@" in addr_l else ""
    is_ats  = any(d in domain for d in ATS_DOMAINS)
    subj_l  = subject.lower()

    # Exact recruiting email match (most explicit — user-supplied)
    for row in companies:
        rec = (row[6] or "").lower().strip()
        if rec and addr_l == rec:
            return row[1]

    # Recruiting email domain match (e.g. jobs@ vs recruiting@ same company)
    if not is_ats and domain:
        for row in companies:
            rec = (row[6] or "").lower().strip()
            if rec and "@" in rec and rec.split("@")[1] == domain:
                return row[1]

    # Company name word-boundary match in subject line (longest names first to avoid partial matches)
    for row in sorted(companies, key=lambda r: len(r[1]), reverse=True):
        name_l = row[1].lower()
        m = re.search(r'\b' + re.escape(name_l) + r'\b', subj_l)
        if not m:
            continue
        # If single-word company name, skip if it appears inside a larger capitalized company name
        # e.g. "Runway" should not match in "...application to Rent the Runway"
        if len(row[1].split()) == 1:
            pre = subject[:m.start()]
            if re.search(r'[A-Z][A-Za-z]+\s+(?:the\s+|a\s+|an\s+|of\s+|&\s+)?$', pre):
                continue
            # Skip if followed by a word that signals a multi-word company name (e.g. "Listen Labs")
            post = subject[m.end():m.end() + 20]
            wm = re.match(r'\s+([A-Z][a-z]+)', post)
            if wm and wm.group(1).lower() in _COMPANY_NAME_INDICATORS:
                continue
        return row[1]

    # For ATS senders: match local part of email address against company slug first
    # e.g. zillow@myworkday.com → "zillow" → matches "Zillow" before body scan finds "Workday"
    # Strip sub-addressing tag first (e.g. "githubinc+autoreply" → "githubinc" for iCIMS)
    if is_ats and "@" in addr_l:
        local = addr_l.split("@")[0].split("+")[0]
        if local:
            for row in sorted(companies, key=lambda r: len(r[1]), reverse=True):
                slug = row[1].lower().replace(" ", "").replace("-", "")
                # Exact match, slug embedded in local (e.g. "github" ⊆ "githubinc"),
                # or local embedded in slug (e.g. "univision" ⊆ "televisaunivision")
                if (local == slug
                        or (len(slug) >= 5 and slug in local)
                        or (len(local) >= 5 and local in slug)):
                    return row[1]

    # For ATS senders: fall back to searching company name in email body (case-sensitive, longest first)
    if is_ats and body:
        for row in sorted(companies, key=lambda r: len(r[1]), reverse=True):
            for m in re.finditer(r'\b' + re.escape(row[1]) + r'\b', body):
                # Skip social-media-link context e.g. "Flexport on LinkedIn", "apply via Indeed",
                # or possessive context e.g. "Lyft's LinkedIn page"
                ctx = body[max(0, m.start() - 15):m.start()]
                if re.search(r'\bon\s+$|\bvia\s+$|\b\w+\u2019s\s+$|\b\w+\'s\s+$', ctx, re.IGNORECASE):
                    continue
                # Skip possessive reference e.g. "Microsoft's infrastructure" (not the hiring co.)
                if m.end() < len(body) and body[m.end()] in ("'", "\u2019"):
                    continue
                # Skip single-word names followed by a company-indicator word (e.g. "Listen Labs")
                if len(row[1].split()) == 1:
                    post = body[m.end():m.end() + 20]
                    wm = re.match(r'\s+([A-Z][a-z]+)', post)
                    if wm and wm.group(1).lower() in _COMPANY_NAME_INDICATORS:
                        continue
                    # Skip phrasal-verb contexts e.g. "Check out", "Log in", "Sign up"
                    if re.match(r'\s+(?:out|in|up|back)\b', post, re.IGNORECASE):
                        continue
                return row[1]

    # Sender domain → company name slug fallback (use second-to-last part, e.g. "anthropic" from "mail.anthropic.com")
    if not is_ats and domain:
        parts = domain.split(".")
        base  = parts[-2] if len(parts) >= 2 else parts[0]
        for row in companies:
            slug = row[1].lower().replace(" ", "").replace("-", "")
            if base and base == slug:
                return row[1]
    return None


_SKIP_NAMES = {"our", "the", "a", "an", "your", "this", "that", "us", "we", "we've", "i", "you"}

# Words that, when following a single-word company name, indicate the name is part of
# a longer multi-word company (e.g. "Listen Labs", "Synapse Health", "Waymo Technologies").
_COMPANY_NAME_INDICATORS = frozenset({
    "labs", "lab", "technologies", "tech", "software", "systems",
    "analytics", "health", "sciences", "networks", "digital",
    "solutions", "studio", "studios", "media", "ai", "data",
    "platform", "platforms", "robotics", "ventures", "institute",
})

_JOB_BOARD_DOMAINS = {
    "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "careerbuilder.com", "dice.com", "simplyhired.com",
    "hired.com", "wellfound.com", "angellist.com", "builtin.com",
    "handshake.com", "joinhandshake.com", "idealist.org",
}

# Domain base names that don't identify a specific company
_GENERIC_DOMAIN_BASES = {
    "gmail", "yahoo", "outlook", "hotmail", "icloud", "protonmail",
    "mail", "email", "info", "support", "contact", "help", "hr",
    "jobs", "recruiting", "careers", "apply", "talent",
    "notification", "notifications", "noreply", "no-reply",
    "service", "services", "team", "hello", "bounce",
    "send", "mg", "smtp", "relay", "sendgrid", "mailgun",
    "mailchimp", "constantcontact", "klaviyo", "marketo",
}

def _is_job_board_sender(domain):
    return any(domain == b or domain.endswith("." + b) for b in _JOB_BOARD_DOMAINS)

# Words that indicate a job level/title, never a company name
_JOB_LEVEL_WORDS = {"senior", "junior", "staff", "principal", "sr", "jr", "entry", "mid"}

# Ordered patterns: each captures the company name in group 1
_COMPANY_SUBJECT_PATTERNS = [
    # "applying/applied/application to Company"
    r"\bappl(?:ying|ied|ication)\s+to\s+([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?=\s+in\s+[A-Z]|\s+[a-z]|\s*(?:has been|is |are |\-|[|!?,]|$))",
    # "interest in working at/joining Company" — requires explicit prefix so job titles aren't caught
    r"\binterest in\s+(?:working at |joining )([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?=\s+in\s+[A-Z]|\s+[a-z]|\s*(?:has been|is |are |\-|[|!?,]|$))",
    # "including Company in your job search" (iCIMS format)
    r"\bincluding\s+([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)\s+in your\b",
    # "at Company" — stops at lowercase word (phrase), location ("in City"), or punctuation
    r"\bat\s+([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?=\s+in\s+[A-Z]|\s+[a-z]|\s*(?:has been|is |are |we |they |\-|[|!?,]|$))",
    # "joining Company"
    r"\bjoining\s+([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?=\s+in\s+[A-Z]|\s+[a-z]|\s*(?:has been|is |are |\-|[|!?,]|$))",
    # "by Team Company" — e.g. "Application received by Team Flexport!"
    r"\bby\s+team\s+([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?=\s+in\s+[A-Z]|\s+[a-z]|\s*(?:has been|is |are |\-|[|!?,]|$))",
    # "Company - Application…" at subject start
    r"^([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)\s*[-|]\s*(?:application|your application|we(?:'ve)? received|thank you)",
    # "… | Company" at subject end
    r"[|]\s*([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)\s*$",
]

# Signature-line pattern handled separately (allows "The ..." company names)
_TALENT_ACQUISITION_PAT = re.compile(
    r"^((?:The\s+)?[A-Z][A-Za-z0-9 &.,'\-]{1,60}?)\s+Talent\s+(?:Acquisition|Team)\b"
)

def _extract_company_from_subject(subject):
    """Parse a company name from an email line. Returns None if uncertain."""
    # Check signature line first (allows leading "The")
    m = _TALENT_ACQUISITION_PAT.match(subject)
    if m:
        name = m.group(1).strip().rstrip(".,- ")
        if len(name) > 2:
            return name
    for pat in _COMPANY_SUBJECT_PATTERNS:
        m = re.search(pat, subject, re.IGNORECASE)
        if m:
            name = m.group(1).strip().rstrip(".,- ")
            # Company name must still start with an uppercase letter
            if not name or not name[0].isupper():
                continue
            first = name.split()[0].lower()
            # Allow "The ProperNoun" (e.g. "The New York Times", "The Trade Desk")
            if first == "the" and len(name.split()) >= 2 and name.split()[1][0].isupper():
                return name
            if len(name) > 2 and first not in _SKIP_NAMES and first not in _JOB_LEVEL_WORDS:
                return name
    return None


def _normalize_title(title) -> str | None:
    """Lowercase, strip punctuation (keep word chars + spaces), collapse whitespace."""
    if not title:
        return None
    t = re.sub(r"[^\w\s]", " ", title.lower())
    t = re.sub(r"\s+", " ", t).strip()
    return t if t else None


_ROLE_FAMILY_MAP = [
    ("Engineering",        ["engineer", "developer", "swe", "software", "backend", "frontend", "devops", "platform"]),
    ("Data·ML",            ["data", "machine learning", "ml", "analyst", "scientist", "ai", "nlp", "llm"]),
    ("Product",            ["product manager", "product management", "pm", "product owner"]),
    ("Design",             ["design", "ux", "ui", "user experience"]),
    ("Program Management", ["program manager", "tpm", "technical program", "project manager"]),
]

def _role_family(title) -> str:
    """Map a job title to a role family bucket."""
    if not title:
        return "Other"
    t = title.lower()
    for family, keywords in _ROLE_FAMILY_MAP:
        if any(kw in t for kw in keywords):
            return family
    return "Other"


def _age_bucket(days) -> str:
    """Bucket age in days into one of four ranges."""
    if days is None:
        return "Unknown"
    if days <= 7:
        return "0–7d"
    if days <= 14:
        return "8–14d"
    if days <= 30:
        return "15–30d"
    return "30d+"



_JOB_TITLE_PATTERNS = [
    # "applying to the [Title] position/role"
    # [^.!?\n] prevents crossing sentence boundaries
    r"appl(?:ying|ied|ication)\s+(?:to\s+)?(?:for\s+)?the\s+([^.!?\n]{3,80}?)\s+(?:position|role|opening|opportunity)\b",
    # "for the [Title] position/role"
    r"\bfor\s+the\s+([^.!?\n]{3,80}?)\s+(?:position|role|opening)\b",
    # "interest in the [Title] role"
    r"\binterest\s+in\s+the\s+([^.!?\n]{3,80}?)\s+(?:position|role|opening)\b",
]

_TITLE_JUNK_WORDS = {
    "following", "this", "the", "that", "your", "our", "next", "any",
    "each", "a", "an", "another", "said", "aforementioned",
}

def _is_valid_job_title(title):
    """Return False for obvious non-titles (sentence fragments, generic words)."""
    # Sentence fragment: contains a period with text after it
    if re.search(r'\.\s*\S', title):
        return False
    # Single generic word
    words = title.split()
    if len(words) == 1 and title.lower() in _TITLE_JUNK_WORDS:
        return False
    # Must start with a capital letter or digit
    if title and not (title[0].isupper() or title[0].isdigit()):
        return False
    return True


def _extract_job_title(subject, body=""):
    """Try to extract a job title from subject then first 15 body lines."""
    for text in [subject] + (body.splitlines()[:15] if body else []):
        text = text.strip()
        if not text:
            continue
        for pat in _JOB_TITLE_PATTERNS:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                title = m.group(1).strip().rstrip(".,- ")
                if 3 < len(title) < 120 and _is_valid_job_title(title):
                    return title
    return None


def _match_application_for_email(company_name, subject, body):
    """Find the specific application row for a decision email.
    
    Returns (application_id, match_method).
    """
    with get_conn() as conn:
        open_apps = conn.execute(
            "SELECT id, job_title, job_title_normalized FROM applications "
            "WHERE LOWER(company_name)=LOWER(?) AND decision_date IS NULL",
            (company_name,),
        ).fetchall()

    if not open_apps:
        return (None, "no_apps")

    if len(open_apps) == 1:
        return (open_apps[0][0], "company_only_single")

    # Multiple open apps — try to match by title
    email_title_raw = _extract_job_title(subject, body)
    email_title_norm = _normalize_title(email_title_raw)

    if email_title_norm:
        # Exact match
        for app_id, _, app_title_norm in open_apps:
            if app_title_norm and app_title_norm == email_title_norm:
                return (app_id, "company_title_exact")

        # Fuzzy match
        best_ratio  = 0.0
        best_app_id = None
        for app_id, _, app_title_norm in open_apps:
            if not app_title_norm:
                continue
            ratio = SequenceMatcher(None, email_title_norm, app_title_norm).ratio()
            if ratio > best_ratio:
                best_ratio  = ratio
                best_app_id = app_id
        if best_ratio >= 0.75 and best_app_id is not None:
            return (best_app_id, "company_title_fuzzy")

    return (None, "unmatched_ambiguous")



def _should_update(current, new):
    if current == new or current == "Rejected":
        return False
    if new == "Rejected":
        return True
    return _STATUS_RANK.get(new, 0) > _STATUS_RANK.get(current, 0)


def run_gmail_sync(days=3):
    """Fetch emails, find application confirmations. Returns log — does NOT write to DB."""
    try:
        svc = build("gmail", "v1", credentials=_get_gmail_credentials())
    except Exception as e:
        return [{"type": "error", "message": f"Gmail auth failed: {e}"}]

    companies = get_companies()

    email_terms = [f"from:{row[6]}" for row in companies if (row[6] or "").strip()]
    all_terms   = [f"from:{d}" for d in ATS_DOMAINS] + email_terms + _GMAIL_SUBJECT_TERMS
    q = f"({' OR '.join(all_terms)}) newer_than:{days}d"
    try:
        msgs = []
        page_token = None
        while len(msgs) < 2000:
            kwargs = {"userId": "me", "q": q, "maxResults": 500}
            if page_token:
                kwargs["pageToken"] = page_token
            resp = svc.users().messages().list(**kwargs).execute()
            batch = resp.get("messages", [])
            msgs.extend(batch)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    except Exception as e:
        return [{"type": "error", "message": f"Gmail search failed: {e}"}]

    if not msgs:
        return [{"type": "info", "message": "No matching emails found."}]

    # ── Batch-fetch all messages (100 per HTTP round-trip) ──
    fetched: dict[str, dict] = {}

    def _on_fetch(request_id, response, exception):
        if exception is None:
            fetched[request_id] = response

    for i in range(0, len(msgs), 100):
        chunk_refs = msgs[i : i + 100]
        batch_req = svc.new_batch_http_request(callback=_on_fetch)
        for ref in chunk_refs:
            batch_req.add(
                svc.users().messages().get(userId="me", id=ref["id"], format="full"),
                request_id=ref["id"],
            )
        try:
            batch_req.execute()
        except Exception:
            for ref in chunk_refs:  # fallback: individual fetches
                try:
                    fetched[ref["id"]] = svc.users().messages().get(
                        userId="me", id=ref["id"], format="full"
                    ).execute()
                except Exception:
                    pass

    tracked_names = {r[1].lower() for r in companies}

    # Gmail returns newest first — keep only the most recent email per company
    best            = {}   # company_lower → entry (tracked companies, confirmations)
    best_new        = {}   # name_lower    → entry (companies not yet in tracker)
    best_rejections = {}   # company_lower → entry (tracked companies, rejections)

    for ref in msgs:
        msg = fetched.get(ref["id"])
        if not msg:
            continue
        hdrs    = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = hdrs.get("Subject", "")
        sender  = hdrs.get("From", "")
        body    = _extract_email_body(msg["payload"])

        # Unwrap forwarded emails so we match on the original sender/subject
        subject, sender, body = _unwrap_forwarded(subject, sender, body)

        internal_ms = int(msg.get("internalDate", 0))
        email_date  = datetime.fromtimestamp(internal_ms / 1000).date() if internal_ms else date.today()
        new_age     = (date.today() - email_date).days

        _, sender_addr = parseaddr(sender)
        sender_addr_l  = sender_addr.lower()
        sender_domain  = sender_addr_l.split("@")[-1] if "@" in sender_addr_l else ""
        is_ats_sender  = any(d in sender_domain for d in ATS_DOMAINS)

        company = _match_company(sender, subject, companies, body)

        result = classify_email(subject, body, sender)

        # Match to specific application (for non-confirmation emails)
        app_id, match_method = (None, "no_apps")
        if company and result.label != "confirmation":
            app_id, match_method = _match_application_for_email(company, subject, body)

        # Persist classification (best-effort)
        try:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO email_classifications "
                    "(gmail_msg_id, gmail_thread_id, email_date, sender, subject, "
                    " label, confidence, evidence, application_id, match_method) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (ref["id"], msg.get("threadId"), email_date.isoformat(),
                     sender, subject, result.label, result.confidence,
                     json.dumps(result.evidence), app_id, match_method),
                )
        except Exception:
            pass

        if company and result.legacy_status in ("Rejected", "Interviewing", "Offer"):
            update_application_decision(company, result.legacy_status, email_date.isoformat(),
                                        application_id=app_id, gmail_msg_id=ref["id"])

        # Collect rejection emails for the Rejections tab (one per company, most recent)
        if result.label == "rejection" and company:
            rej_key = company.lower()
            if rej_key not in best_rejections:
                company_row = next((r for r in companies if r[1].lower() == rej_key), None)
                best_rejections[rej_key] = {
                    "type": "rejection",
                    "company": company,
                    "subject": subject,
                    "sender": sender,
                    "body": body,
                    "last_checked": company_row[2] if company_row else None,
                    "last_applied": company_row[7] if company_row else None,
                    "email_date": email_date.isoformat(),
                    "new_age": new_age,
                    "msg_id": ref["id"],
                    "thread_id": msg.get("threadId"),
                    "confidence": result.confidence,
                    "evidence": result.evidence,
                }

        # Only surface application-received emails in the applied sync log
        if result.label != "confirmation":
            continue

        if company:
            key = company.lower()
            company_row  = next((r for r in companies if r[1].lower() == key), None)

            # Auto-save recruiting email if sender is a real company address
            if (company_row
                    and not (company_row[6] or "").strip()
                    and sender_addr_l
                    and not is_ats_sender
                    and not _is_job_board_sender(sender_domain)):
                update_company(company_row[0], company_row[4] or "",
                               company_row[5] or "", sender_addr_l)

            # Auto-log job title to applications table (runs for every confirmation email found)
            log_application(company, _extract_job_title(subject, body),
                            email_date.isoformat(), subject, gmail_msg_id=ref["id"])

            if key not in best:
                last_checked  = company_row[2] if company_row else None
                last_applied  = company_row[7] if company_row else None
                best[key] = {
                    "type": "pending", "company": company, "subject": subject,
                    "sender": sender, "body": body, "last_checked": last_checked,
                    "last_applied": last_applied,
                    "email_date": email_date.isoformat(), "new_age": new_age,
                    "msg_id": ref["id"],
                    "thread_id": msg.get("threadId"),
                }
        else:
            # Try to extract company name for untracked companies
            extracted = _extract_company_from_subject(subject)

            # Scan first 15 body lines when subject is generic
            if not extracted and body:
                for line in body.splitlines()[:15]:
                    line = line.strip()
                    if len(line) > 5:
                        extracted = _extract_company_from_subject(line)
                        if extracted:
                            break

            # Full-body scan for "Company Talent Acquisition" signature (can appear anywhere)
            if not extracted and body:
                for line in body.splitlines():
                    m = _TALENT_ACQUISITION_PAT.match(line.strip())
                    if m:
                        name = m.group(1).strip().rstrip(".,- ")
                        if len(name) > 2:
                            extracted = name
                            break

            # Fallback: infer from sender domain (e.g. no-reply@stripe.com → "Stripe")
            if (not extracted
                    and not is_ats_sender
                    and sender_domain
                    and not _is_job_board_sender(sender_domain)):
                parts = sender_domain.split(".")
                base  = parts[-2] if len(parts) >= 2 else parts[0]
                if base and base not in _GENERIC_DOMAIN_BASES:
                    extracted = base.capitalize()

            if extracted:
                key = extracted.lower()
                if key not in tracked_names and key not in best_new:
                    best_new[key] = {
                        "type": "new", "company": extracted, "subject": subject,
                        "sender": sender, "body": body,
                        "email_date": email_date.isoformat(), "new_age": new_age,
                        "msg_id": ref["id"], "thread_id": msg.get("threadId"),
                    }

    if not best and not best_new and not best_rejections:
        return [{"type": "info", "message": "No application emails found."}]

    return list(best.values()) + list(best_new.values()) + list(best_rejections.values())


def mark_company_rejected(company):
    """Set matching job postings to Rejected."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE job_postings SET status = 'Rejected'"
            " WHERE LOWER(company) = LOWER(?) AND status != 'Rejected'",
            (company,),
        )
        conn.commit()
    get_postings.clear()


def apply_gmail_match(company, email_date):
    """Set company last_applied to the email's date and mark matching postings Applied."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_applied = ? WHERE LOWER(name) = LOWER(?)",
            (email_date, company),
        )
        conn.execute(
            "UPDATE job_postings SET status = 'Applied'"
            " WHERE LOWER(company) = LOWER(?) AND status IN ('To Do', 'In Progress')",
            (company,),
        )
        conn.commit()
    get_companies.clear()
    get_postings.clear()


# ── UI ────────────────────────────────────────────────────────────────────────

COLS    = [2.0, 1.6, 0.9, 1.5, 2.4, 0.55, 0.55, 0.55, 2.8]
HEADERS = ["Company", "Activity", "Age", "Careers", "Action", "", "", "", "Notes"]


@st.fragment
def render_companies_tab():
    if "editing_url_id" not in st.session_state:
        st.session_state.editing_url_id = None
    if "editing_email_id" not in st.session_state:
        st.session_state.editing_email_id = None

    companies = get_companies()

    if not companies:
        st.info("No companies yet — add one in the ➕ tab.")
        return

    # ── search + thresholds ──
    s1, t1, t2, _ = st.columns([3.0, 1.2, 1.2, 3])
    search      = s1.text_input("", placeholder="🔍  Filter companies…", key="company_search", label_visibility="collapsed")
    applied_max = t1.number_input("Applied window (days)", min_value=1, max_value=365, value=28, step=1, key="applied_threshold")
    checked_max = t2.number_input("Checked window (days)", min_value=1, max_value=365, value=15, step=1, key="checked_threshold")

    if search:
        companies = [r for r in companies if search.lower() in r[1].lower()]

    # ── split into 4 groups ──
    # r[7]=last_applied, r[2]=last_checked; pre-compute days_ago once per company
    _da = {r[0]: (days_ago(r[7]), days_ago(r[2])) for r in companies}
    never_applied_group = [r for r in companies if r[7] is None]
    never_applied_ids   = {r[0] for r in never_applied_group}
    applied_group = [r for r in companies if r[0] not in never_applied_ids and _da[r[0]][0] is not None and _da[r[0]][0] <= applied_max]
    applied_ids   = {r[0] for r in applied_group}
    checked_group = [r for r in companies if r[0] not in never_applied_ids and r[0] not in applied_ids and _da[r[0]][1] is not None and _da[r[0]][1] < checked_max]
    checked_ids   = {r[0] for r in checked_group}
    action_group  = [r for r in companies if r[0] not in never_applied_ids and r[0] not in applied_ids and r[0] not in checked_ids]

    def render_header():
        hcols = st.columns(COLS)
        for col, label in zip(hcols, HEADERS):
            col.markdown(
                f'<span style="font-size:0.72rem;font-weight:700;color:#9ca3af;'
                f'text-transform:uppercase;letter-spacing:0.07em">{label}</span>',
                unsafe_allow_html=True,
            )
        st.markdown('<hr style="border:none;border-top:1.5px solid #e5e7eb;margin:4px 0 6px">', unsafe_allow_html=True)

    _CO_PAGE_SIZE = 30

    def render_rows(group, empty_msg="No companies here.", page_key="co_page"):
        if not group:
            st.markdown(f'<div style="padding:1.5rem 0;text-align:center;color:#9ca3af;font-size:0.9rem">{empty_msg}</div>', unsafe_allow_html=True)
            return
        total = len(group)
        n_pages = max(1, (total + _CO_PAGE_SIZE - 1) // _CO_PAGE_SIZE)
        page = min(st.session_state.get(page_key, 0), n_pages - 1)
        page_group = group[page * _CO_PAGE_SIZE : (page + 1) * _CO_PAGE_SIZE]
        for cid, name, last_checked, _, notes, careers_url, recruiting_email, last_applied in page_group:
            # Age badge = most recent activity (applied or checked)
            most_recent = last_applied if (last_checked is None or (last_applied and last_applied > last_checked)) else last_checked
            d     = days_ago(most_recent)
            color = staleness_color(d)

            age_html  = (
                f'<span class="badge" style="background:{color}">{d}d</span>'
                if d is not None else
                f'<span class="badge" style="background:#d1d5db;color:#6b7280">never</span>'
            )
            applied_rel = relative_date(last_applied)
            checked_rel = relative_date(last_checked)
            applied_line = (
                f'<div><span style="color:#16a34a;font-size:0.72rem;font-weight:600">Applied</span> '
                f'<span class="date-text">{applied_rel}</span></div>'
            ) if applied_rel else ''
            checked_line = (
                f'<div><span style="color:#6b7280;font-size:0.72rem">Checked</span> '
                f'<span class="date-text">{checked_rel}</span></div>'
            ) if checked_rel else ''
            date_html = applied_line + checked_line or '<span style="color:#d1d5db;font-size:0.84rem">—</span>'

            # favicon — tries careers URL domain first, falls back to guessing from company name
            fav_domain   = _favicon_domain(name, careers_url)
            favicon_html = (
                f'<img src="https://www.google.com/s2/favicons?domain={fav_domain}&sz=32" '
                f'style="width:16px;height:16px;border-radius:3px;vertical-align:middle;'
                f'margin-right:5px;margin-bottom:2px" onerror="this.style.display=\'none\'">'
            )

            # colored left border strip inline with name
            name_html = (
                f'<div style="display:flex;align-items:center;gap:6px">'
                f'<div style="width:3px;min-height:20px;background:{color};border-radius:2px;flex-shrink:0"></div>'
                f'{favicon_html}<span class="company-name">{name}</span>'
                f'</div>'
            )
            note_html = f'<span class="note-text">{notes}</span>' if notes else '<span style="color:#bdc1c6">—</span>'
            c = st.columns(COLS)
            c[0].markdown(name_html, unsafe_allow_html=True)
            c[1].markdown(date_html, unsafe_allow_html=True)
            c[2].markdown(age_html,  unsafe_allow_html=True)
            if careers_url:
                clnk, cclr = c[3].columns([5, 1])
                clnk.markdown(f'<a class="careers-link" href="{careers_url}" target="_blank">↗ Careers</a>', unsafe_allow_html=True)
                if cclr.button("✕", key=f"clr_{cid}", help="Clear careers URL"):
                    update_company(cid, notes or "", "", recruiting_email or "")
                    st.rerun()
            else:
                c[3].markdown('<span style="color:#bdc1c6;font-size:0.82rem">—</span>', unsafe_allow_html=True)

            app_col, chk_col = c[4].columns(2)
            if app_col.button("✓ Applied", key=f"app_{cid}", type="primary"):
                mark_all_company_applied(name)
                st.toast(f"Marked **{name}** as applied", icon="✅")
                st.rerun()
            if chk_col.button("✓ Checked", key=f"ms_{cid}"):
                mark_scraped(cid)
                st.rerun()

            link_btn_label = "✎" if careers_url else "🔗"
            link_btn_help  = "Edit careers URL" if careers_url else "Add careers URL"
            if c[5].button(link_btn_label, key=f"lnk_{cid}", help=link_btn_help):
                st.session_state.editing_url_id   = cid if st.session_state.editing_url_id != cid else None
                st.session_state.editing_email_id = None

            email_btn_label = "✎" if recruiting_email else "✉"
            email_btn_help  = "Edit recruiting email" if recruiting_email else "Add recruiting email"
            if c[6].button(email_btn_label, key=f"eml_{cid}", help=email_btn_help):
                st.session_state.editing_email_id = cid if st.session_state.editing_email_id != cid else None
                st.session_state.editing_url_id   = None

            if c[7].button("🗑", key=f"del_{cid}", help="Delete last application entry"):
                delete_latest_application(cid, name)
                st.rerun()

            c[8].markdown(note_html, unsafe_allow_html=True)

            if st.session_state.editing_url_id == cid:
                _, ec1, ec2, ec3 = st.columns([0.3, 4.5, 1.0, 1.0])
                new_url = ec1.text_input(
                    "Careers URL", value=careers_url or "",
                    placeholder="https://company.com/careers",
                    key=f"url_input_{cid}", label_visibility="collapsed",
                )
                if ec2.button("Save", key=f"url_save_{cid}", type="primary"):
                    update_company(cid, notes or "", new_url.strip(), recruiting_email or "")
                    st.session_state.editing_url_id = None
                if ec3.button("Cancel", key=f"url_cancel_{cid}"):
                    st.session_state.editing_url_id = None

            if st.session_state.editing_email_id == cid:
                _, ec1, ec2, ec3 = st.columns([0.3, 4.5, 1.0, 1.0])
                new_email = ec1.text_input(
                    "Recruiting Email", value=recruiting_email or "",
                    placeholder="jobs@company.com",
                    key=f"email_input_{cid}", label_visibility="collapsed",
                )
                if ec2.button("Save", key=f"email_save_{cid}", type="primary"):
                    update_company(cid, notes or "", careers_url or "", new_email.strip())
                    st.session_state.editing_email_id = None
                if ec3.button("Cancel", key=f"email_cancel_{cid}"):
                    st.session_state.editing_email_id = None

            st.markdown('<hr class="row-divider">', unsafe_allow_html=True)

        if n_pages > 1:
            pc = st.columns([1, 4, 1])
            if page > 0 and pc[0].button("← Prev", key=f"{page_key}_prev"):
                st.session_state[page_key] = page - 1
                st.rerun()
            pc[1].markdown(
                f'<div style="text-align:center;color:#9ca3af;font-size:0.82rem;padding-top:6px">'
                f'Page {page + 1} of {n_pages} &nbsp;·&nbsp; {total} companies</div>',
                unsafe_allow_html=True,
            )
            if page < n_pages - 1 and pc[2].button("Next →", key=f"{page_key}_next"):
                st.session_state[page_key] = page + 1
                st.rerun()

    tab1, tab_never, tab2, tab3 = st.tabs([
        f"Need Action  ({len(action_group)})",
        f"Never Applied  ({len(never_applied_group)})",
        f"Applied  ({len(applied_group)})",
        f"Checked  ({len(checked_group)})",
    ])
    with tab1:
        render_header()
        render_rows(action_group, empty_msg="✓ All caught up!", page_key="co_page_action")
    with tab_never:
        render_header()
        render_rows(never_applied_group, empty_msg="No companies without applications.", page_key="co_page_never")
    with tab2:
        render_header()
        render_rows(applied_group, empty_msg="No recent applications.", page_key="co_page_applied")
    with tab3:
        render_header()
        render_rows(checked_group, empty_msg="No recent checks.", page_key="co_page_checked")

    # ── edit expander (bulk notes) ──
    st.markdown("<br>", unsafe_allow_html=True)
    with st.expander("✏️  Edit notes"):
        all_companies = get_companies()
        df = pd.DataFrame(all_companies, columns=["id", "name", "last_checked", "interval_days", "notes", "careers_url", "recruiting_email", "last_applied"])
        edited = st.data_editor(
            df[["id", "name", "notes"]],
            column_config={
                "id":    st.column_config.NumberColumn("ID", disabled=True, width="small"),
                "name":  st.column_config.TextColumn("Company", disabled=True, width="medium"),
                "notes": st.column_config.TextColumn("Notes", width="large"),
            },
            hide_index=True,
            use_container_width=True,
        )
        if st.button("💾  Save Notes", type="primary"):
            for _, row in edited.iterrows():
                orig = next(c for c in all_companies if c[0] == row["id"])
                update_company(int(row["id"]), row["notes"] or "", orig[5] or "", orig[6] or "")
            st.success("Saved!")
            st.rerun()


def render_add_tab():
    st.subheader("Add New Company")
    st.markdown("<br>", unsafe_allow_html=True)

    with st.form("add_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        name         = col1.text_input("Company Name *")
        no_date      = col2.checkbox("Haven't applied yet", value=False)
        date_applied = st.date_input("Date Applied", value=date.today(), disabled=no_date)
        careers_url      = st.text_input("Careers Site URL", placeholder="https://company.com/careers")
        recruiting_email = st.text_input("Recruiting / Jobs Email", placeholder="jobs@company.com")
        notes            = st.text_area("Notes (optional)", height=100)
        submitted        = st.form_submit_button("Add Company", type="primary", use_container_width=True)

        if submitted:
            if name.strip():
                applied_str = None if no_date else date_applied.isoformat()
                add_company(name.strip(), applied_str, notes.strip(), careers_url.strip(), recruiting_email.strip())
                st.success(f"Added **{name.strip()}**")
            else:
                st.error("Company name is required.")


# ── Gmail Tab UI ──────────────────────────────────────────────────────────────

@st.fragment
def render_gmail_tab():
    st.subheader("Gmail Sync")
    st.markdown(
        '<p style="color:#6b7280;font-size:0.9rem;margin-bottom:1rem">'
        "Scans your inbox for application confirmation emails and lets you choose what to update."
        "</p>",
        unsafe_allow_html=True,
    )

    if not GMAIL_AVAILABLE:
        st.error("Google API libraries are not installed.")
        st.code("pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        return

    if not os.path.exists(CREDENTIALS_FILE):
        st.warning(f"`{CREDENTIALS_FILE}` not found in the app folder.")
        with st.expander("Setup instructions"):
            st.markdown(
                """
1. Go to **[Google Cloud Console](https://console.cloud.google.com/)**
2. Create or select a project
3. Enable the **Gmail API**: APIs & Services → Library → search "Gmail API" → Enable
4. Create credentials: APIs & Services → Credentials → **Create Credentials → OAuth client ID**
5. Application type: **Desktop app**, then click Create
6. Download the JSON and save it as **`credentials.json`** next to `app.py`
7. Refresh this page
                """
            )
        return

    # ── Controls ──
    c1, c2, c3 = st.columns([1.3, 1.0, 1])
    sync_days = c3.number_input("Days to scan", min_value=1, value=3, step=1, key="gmail_days")
    if c1.button("🔄  Sync Gmail", type="primary"):
        with st.spinner("Syncing…  (a browser window may open for first-time authorization)"):
            log = run_gmail_sync(days=sync_days)
        st.session_state["gmail_log"]       = log
        st.session_state["gmail_synced"]    = datetime.now().strftime("%b %d %Y · %I:%M %p")
        st.session_state["gmail_updated"]   = set()
        st.session_state["gmail_dismissed"] = set()
        st.session_state["gmail_rejected"]  = set()
        st.session_state["gmail_undo"]      = {}
        for k in list(st.session_state.keys()):
            if k.startswith("new_sel_") or k.startswith("chg_sel_"):
                del st.session_state[k]
        st.rerun()

    if os.path.exists(TOKEN_FILE) and c2.button("Disconnect Gmail"):
        os.remove(TOKEN_FILE)
        for k in ("gmail_log", "gmail_synced", "gmail_updated", "gmail_dismissed", "gmail_rejected"):
            st.session_state.pop(k, None)
        st.rerun()

    if "gmail_synced" in st.session_state:
        st.caption(f"Last synced: {st.session_state['gmail_synced']}")

    if "gmail_log" not in st.session_state:
        return

    log       = st.session_state["gmail_log"]
    updated   = st.session_state.get("gmail_updated",   set())
    dismissed = st.session_state.get("gmail_dismissed", set())
    rejected  = st.session_state.get("gmail_rejected",  set())
    undo_data = st.session_state.get("gmail_undo",       {})

    errors  = [e for e in log if e["type"] == "error"]
    infos   = [e for e in log if e["type"] == "info"]
    items   = [e for e in log if e["type"] in ("pending", "new")]

    st.markdown("---")
    st.markdown("**Sync Log**")

    for e in errors:
        st.error(f"❌ {e['message']}")
    for e in infos:
        st.info(e["message"])

    def _age_badge(d):
        if d is None:
            return '<span class="badge" style="background:#d1d5db;color:#6b7280">never</span>'
        return f'<span class="badge" style="background:{staleness_color(d)}">{d}d</span>'

    def _most_recent_age(e):
        ages = [days_ago(e.get("last_checked")), days_ago(e.get("last_applied"))]
        valid = [a for a in ages if a is not None]
        return min(valid) if valid else None

    def _trash_e(ev):
        thread_id = ev.get("thread_id")
        msg_id = ev.get("msg_id")
        if thread_id:
            trash_gmail_thread(thread_id)
        elif msg_id:
            trash_gmail_message(msg_id)

    def _render_item(i, e, key_prefix, already_uptodate=False, show_checkbox=False, inside_expander=False):
        current_age = _most_recent_age(e)
        new_age     = e.get("new_age")
        age_html    = f'{_age_badge(current_age)} → {_age_badge(new_age)}'
        _, sender_addr = parseaddr(e.get("sender", ""))
        sender_html = (
            f'<span style="color:#6b7280;font-size:0.78rem">{sender_addr}</span>'
            if sender_addr else ""
        )
        if i in updated:
            ucols = st.columns([7.5, 1.0])
            ucols[0].markdown(
                f'✅ &nbsp;<span class="company-name">{e["company"]}</span>'
                f'&nbsp;{age_html}&nbsp;'
                f'<span style="color:#16a34a;font-size:0.82rem">marked applied</span>'
                f'<br><span style="color:#9ca3af;font-size:0.78rem;padding-left:1.4rem">{e["subject"]}</span>'
                f'<br><span style="padding-left:1.4rem">{sender_html}</span>',
                unsafe_allow_html=True,
            )
            if i in undo_data and ucols[1].button("Undo", key=f"{key_prefix}_undo_{i}"):
                info = undo_data[i]
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE companies SET last_applied = ? WHERE LOWER(name) = LOWER(?)",
                        (info.get("old_applied"), info["company"]),
                    )
                    conn.commit()
                updated.discard(i)
                del undo_data[i]
                st.session_state["gmail_updated"] = updated
                st.session_state["gmail_undo"]    = undo_data
                st.rerun()
        elif i in rejected:
            st.markdown(
                f'❌ &nbsp;<span class="company-name">{e["company"]}</span>'
                f'&nbsp;{age_html}&nbsp;'
                f'<span style="color:#dc2626;font-size:0.82rem">marked rejected</span>'
                f'<br><span style="color:#9ca3af;font-size:0.78rem;padding-left:1.4rem">{e["subject"]}</span>'
                f'<br><span style="padding-left:1.4rem">{sender_html}</span>',
                unsafe_allow_html=True,
            )
        else:
            use_cb = show_checkbox and not already_uptodate
            # When an item is checked, skip per-row buttons — user will use bulk action.
            # This cuts widget count from ~8 to ~3 per selected row.
            is_sel = use_cb and st.session_state.get(f"chg_sel_{i}", False)
            if is_sel:
                row = st.columns([0.35, 6.65])
                row[0].checkbox("", key=f"chg_sel_{i}", label_visibility="collapsed")
                row[1].markdown(
                    f'<span class="company-name">{e["company"]}</span>'
                    f'&nbsp;{age_html}'
                    f'<br><span style="color:#9ca3af;font-size:0.78rem">{e["subject"]}</span>'
                    f'<br>{sender_html}',
                    unsafe_allow_html=True,
                )
                body_text = (e.get("body") or "").strip()
                if body_text:
                    with st.expander("View email body"):
                        st.text(body_text)
                return
            if already_uptodate:
                row = st.columns([3.5, 1.0, 0.9, 1.3, 0.8])
            elif use_cb:
                row = st.columns([0.35, 2.65, 1.2, 1.5, 0.9, 1.3, 0.8])
            else:
                row = st.columns([3.0, 1.2, 1.5, 0.9, 1.3, 0.8])
            ci = 0
            if use_cb:
                row[0].checkbox("", key=f"chg_sel_{i}", label_visibility="collapsed")
                ci = 1
            row[ci].markdown(
                f'<span class="company-name">{e["company"]}</span>'
                f'&nbsp;{age_html}'
                f'<br><span style="color:#9ca3af;font-size:0.78rem">{e["subject"]}</span>'
                f'<br>{sender_html}',
                unsafe_allow_html=True,
            )
            if already_uptodate:
                if row[ci+1].button("🗑 Delete", key=f"{key_prefix}_trash_{i}"):
                    try:
                        _trash_e(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
                if row[ci+2].button("Reject", key=f"{key_prefix}_reject_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    st.rerun()
                if row[ci+3].button("Reject & 🗑", key=f"{key_prefix}_reject_del_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    try:
                        _trash_e(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
                if row[ci+4].button("Dismiss", key=f"{key_prefix}_dismiss_{i}"):
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
            else:
                if row[ci+1].button("Update Tracker", key=f"{key_prefix}_apply_{i}", type="primary"):
                    undo_data[i] = {"company": e["company"], "old_applied": e.get("last_applied")}
                    apply_gmail_match(e["company"], e["email_date"])
                    updated.add(i)
                    st.session_state["gmail_updated"] = updated
                    st.session_state["gmail_undo"]    = undo_data
                    st.rerun()
                if row[ci+2].button("Update & 🗑", key=f"{key_prefix}_apply_del_{i}"):
                    undo_data[i] = {"company": e["company"], "old_applied": e.get("last_applied")}
                    apply_gmail_match(e["company"], e["email_date"])
                    try:
                        _trash_e(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    updated.add(i)
                    st.session_state["gmail_updated"] = updated
                    st.session_state["gmail_undo"]    = undo_data
                    st.rerun()
                if row[ci+3].button("Reject", key=f"{key_prefix}_reject_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    st.rerun()
                if row[ci+4].button("Reject & 🗑", key=f"{key_prefix}_reject_del_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    try:
                        _trash_e(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
                if row[ci+5].button("Dismiss", key=f"{key_prefix}_dismiss_{i}"):
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
        body_text = (e.get("body") or "").strip()
        if body_text:
            if inside_expander:
                with st.popover("View email body"):
                    st.text(body_text)
            else:
                with st.expander("View email body"):
                    st.text(body_text)

    pending_items = [e for e in items if e["type"] == "pending"]
    new_items     = [e for e in items if e["type"] == "new"]

    # For tracked companies: split changed vs same age
    # "changed" = email is newer than the most recent recorded activity
    def _email_is_newer(e):
        cur = _most_recent_age(e)
        new = e.get("new_age")
        if cur is None:  return True   # nothing recorded yet
        if new is None:  return False
        return new < cur               # email is more recent

    changed = [(i, e) for i, e in enumerate(items)
               if e["type"] == "pending"
               and i not in dismissed
               and i not in rejected
               and _email_is_newer(e)]
    same    = [(i, e) for i, e in enumerate(items)
               if e["type"] == "pending"
               and i not in dismissed
               and (not _email_is_newer(e) or i in rejected)]
    new_vis = [(i, e) for i, e in enumerate(items)
               if e["type"] == "new" and i not in dismissed]

    if not changed and not same and not new_vis and not errors and not infos:
        st.info("No application confirmation emails found.")
        return

    # ── Tracked companies with a different age ──
    if changed:
        chg_action  = [(i, e) for i, e in changed if i not in updated and i not in rejected]
        chg_sel_ids = [i for i, _ in chg_action if st.session_state.get(f"chg_sel_{i}", False)]
        n_chg_sel   = len(chg_sel_ids)
        n_chg_total = len(chg_action)

        n_pending = sum(1 for i, _ in changed if i not in updated and i not in rejected)
        n_done    = sum(1 for i, _ in changed if i in updated)
        parts = []
        if n_pending: parts.append(f"**{n_pending}** to review")
        if n_done:    parts.append(f"**{n_done}** updated")

        hc1, hc2 = st.columns([3.5, 1.0])
        if parts: hc1.markdown(" · ".join(parts))
        if chg_action:
            sel_label = "Deselect All" if n_chg_sel == n_chg_total else "Select All"
            if hc2.button(sel_label, key="chg_sel_all_btn"):
                new_val = n_chg_sel < n_chg_total
                for i, _ in chg_action:
                    st.session_state[f"chg_sel_{i}"] = new_val
                st.rerun()

        if n_chg_sel > 0:
            bc1, bc2, bc3, _ = st.columns([1.4, 1.6, 1.1, 2.5])
            if bc1.button(f"Update {n_chg_sel}", type="primary", key="chg_bulk_update"):
                for bi in chg_sel_ids:
                    be = next(e for j, e in chg_action if j == bi)
                    undo_data[bi] = {"company": be["company"], "old_applied": be.get("last_applied")}
                    apply_gmail_match(be["company"], be["email_date"])
                    updated.add(bi)
                    st.session_state.pop(f"chg_sel_{bi}", None)
                st.session_state["gmail_updated"] = updated
                st.session_state["gmail_undo"]    = undo_data
                st.rerun()
            if bc2.button(f"Update & 🗑 {n_chg_sel}", key="chg_bulk_update_del"):
                for bi in chg_sel_ids:
                    be = next(e for j, e in chg_action if j == bi)
                    undo_data[bi] = {"company": be["company"], "old_applied": be.get("last_applied")}
                    apply_gmail_match(be["company"], be["email_date"])
                    try:
                        _trash_e(be)
                    except Exception:
                        pass
                    updated.add(bi)
                    st.session_state.pop(f"chg_sel_{bi}", None)
                st.session_state["gmail_updated"] = updated
                st.session_state["gmail_undo"]    = undo_data
                st.rerun()
            if bc3.button(f"Dismiss {n_chg_sel}", key="chg_bulk_dismiss"):
                for bi in chg_sel_ids:
                    dismissed.add(bi)
                    st.session_state.pop(f"chg_sel_{bi}", None)
                st.session_state["gmail_dismissed"] = dismissed
                st.rerun()

        st.markdown("")
        for i, e in changed:
            _render_item(i, e, "main", show_checkbox=True)
    elif not new_vis and not errors and not infos:
        st.info("No new changes — all matched companies are already up to date.")

    # ── New companies not yet in tracker ──
    if new_vis:
        st.markdown("")

        def _trash_new(e):
            tid = e.get("thread_id")
            mid = e.get("msg_id")
            if tid:  trash_gmail_thread(tid)
            elif mid: trash_gmail_message(mid)

        action_vis   = [(i, e) for i, e in new_vis if i not in updated]
        selected_ids = [i for i, _ in action_vis if st.session_state.get(f"new_sel_{i}", False)]
        n_sel        = len(selected_ids)
        n_total      = len(action_vis)

        # Header row
        hc1, hc2 = st.columns([3.5, 1.0])
        hc1.markdown(f"**New companies found ({len(new_vis)})**")
        if action_vis:
            sel_label = "Deselect All" if n_sel == n_total else "Select All"
            if hc2.button(sel_label, key="new_sel_all_btn"):
                new_val = n_sel < n_total
                for i, _ in action_vis:
                    st.session_state[f"new_sel_{i}"] = new_val
                st.rerun()

        st.caption("These companies were in your emails but aren't in your tracker yet.")

        # Bulk action bar — shown when at least one is checked
        if n_sel > 0:
            bc1, bc2, bc3, _ = st.columns([1.6, 1.1, 1.0, 2.0])
            if bc1.button(f"Add {n_sel} to Tracker", type="primary", key="new_bulk_add"):
                for bi in selected_ids:
                    be = next(e for j, e in action_vis if j == bi)
                    add_company(be["company"], be["email_date"])
                    undo_data[bi] = {"company": be["company"]}
                    updated.add(bi)
                    st.session_state.pop(f"new_sel_{bi}", None)
                st.session_state["gmail_updated"] = updated
                st.session_state["gmail_undo"]    = undo_data
                st.rerun()
            if bc2.button(f"🗑 Delete {n_sel}", key="new_bulk_trash"):
                for bi in selected_ids:
                    be = next(e for j, e in action_vis if j == bi)
                    try:
                        _trash_new(be)
                    except Exception:
                        pass
                    dismissed.add(bi)
                    st.session_state.pop(f"new_sel_{bi}", None)
                st.session_state["gmail_dismissed"] = dismissed
                st.rerun()
            if bc3.button(f"Dismiss {n_sel}", key="new_bulk_dismiss"):
                for bi in selected_ids:
                    dismissed.add(bi)
                    st.session_state.pop(f"new_sel_{bi}", None)
                st.session_state["gmail_dismissed"] = dismissed
                st.rerun()

        # Per-row rendering
        for i, e in new_vis:
            new_age   = e.get("new_age")
            age_badge = _age_badge(new_age)
            _, sender_addr = parseaddr(e.get("sender", ""))
            sender_html = (
                f'<span style="color:#6b7280;font-size:0.78rem">{sender_addr}</span>'
                if sender_addr else ""
            )
            if i in updated:
                ucols = st.columns([7.5, 1.0])
                ucols[0].markdown(
                    f'✅ &nbsp;<span class="company-name">{e["company"]}</span>'
                    f'&nbsp;{age_badge}&nbsp;'
                    f'<span style="color:#16a34a;font-size:0.82rem">added to tracker</span>'
                    f'<br><span style="color:#9ca3af;font-size:0.78rem;padding-left:1.4rem">{e["subject"]}</span>'
                    f'<br><span style="padding-left:1.4rem">{sender_html}</span>',
                    unsafe_allow_html=True,
                )
                if i in undo_data and ucols[1].button("Undo", key=f"new_undo_{i}"):
                    for r in get_companies():
                        if r[1].lower() == e["company"].lower():
                            delete_company(r[0])
                            break
                    updated.discard(i)
                    del undo_data[i]
                    st.session_state["gmail_updated"] = updated
                    st.session_state["gmail_undo"]    = undo_data
                    st.rerun()
            elif st.session_state.get(f"new_sel_{i}", False):
                # Compact mode when selected — skip per-row buttons, use bulk action
                row = st.columns([0.35, 6.65])
                row[0].checkbox("", key=f"new_sel_{i}", label_visibility="collapsed")
                row[1].markdown(
                    f'<span class="company-name">{e["company"]}</span>'
                    f'&nbsp;{age_badge}'
                    f'<br><span style="color:#9ca3af;font-size:0.78rem">{e["subject"]}</span>'
                    f'<br>{sender_html}',
                    unsafe_allow_html=True,
                )
            else:
                row = st.columns([0.35, 2.65, 1.4, 1.3, 0.9, 0.85])
                row[0].checkbox("", key=f"new_sel_{i}", label_visibility="collapsed")
                row[1].markdown(
                    f'<span class="company-name">{e["company"]}</span>'
                    f'&nbsp;{age_badge}'
                    f'<br><span style="color:#9ca3af;font-size:0.78rem">{e["subject"]}</span>'
                    f'<br>{sender_html}',
                    unsafe_allow_html=True,
                )
                if row[2].button("Add to Tracker", key=f"new_apply_{i}", type="primary"):
                    add_company(e["company"], e["email_date"])
                    undo_data[i] = {"company": e["company"]}
                    updated.add(i)
                    st.session_state.pop(f"new_sel_{i}", None)
                    st.session_state["gmail_updated"] = updated
                    st.session_state["gmail_undo"]    = undo_data
                    st.rerun()
                if row[3].button("Add & 🗑", key=f"new_apply_del_{i}"):
                    add_company(e["company"], e["email_date"])
                    undo_data[i] = {"company": e["company"]}
                    try:
                        _trash_new(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    updated.add(i)
                    st.session_state.pop(f"new_sel_{i}", None)
                    st.session_state["gmail_updated"] = updated
                    st.session_state["gmail_undo"]    = undo_data
                    st.rerun()
                if row[4].button("🗑", key=f"new_trash_{i}", help="Delete email"):
                    try:
                        _trash_new(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    dismissed.add(i)
                    st.session_state.pop(f"new_sel_{i}", None)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
                if row[5].button("Dismiss", key=f"new_dismiss_{i}"):
                    dismissed.add(i)
                    st.session_state.pop(f"new_sel_{i}", None)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
            body_text = (e.get("body") or "").strip()
            if body_text:
                with st.expander("View email body"):
                    st.text(body_text)

    # ── Already up to date (expander) ──
    if same:
        with st.expander(f"Already up to date ({len(same)})"):
            visible_same = [(i, e) for i, e in same if i not in dismissed]
            if visible_same:
                if st.button("🗑 Delete All", key="same_delete_all"):
                    for i, e in visible_same:
                        thread_id = e.get("thread_id")
                        msg_id = e.get("msg_id")
                        try:
                            if thread_id:
                                trash_gmail_thread(thread_id)
                            elif msg_id:
                                trash_gmail_message(msg_id)
                            dismissed.add(i)
                        except Exception:
                            dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
            for i, e in same:
                _render_item(i, e, "same", already_uptodate=True, inside_expander=True)


# ── Job Postings DB ───────────────────────────────────────────────────────────

STATUS_OPTIONS = ["To Do", "In Progress", "Applied", "Interviewing", "Offer", "Rejected"]

STATUS_STYLE = {
    "To Do":        ("bg", "#f3f4f6", "#6b7280"),
    "In Progress":  ("bg", "#eff6ff", "#2563eb"),
    "Applied":      ("bg", "#f0fdf4", "#16a34a"),
    "Interviewing": ("bg", "#fefce8", "#ca8a04"),
    "Offer":        ("bg", "#f0fdf4", "#15803d"),
    "Rejected":     ("bg", "#fef2f2", "#dc2626"),
}

@st.cache_data
def get_postings():
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, company, role, url, date_added, status, notes FROM job_postings ORDER BY date_added ASC, company ASC"
        ).fetchall()


def add_posting(company, role, url, date_added, notes=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO job_postings (company, role, url, date_added, notes) VALUES (?, ?, ?, ?, ?)",
            (company, role, url, date_added, notes),
        )
        conn.commit()
    get_postings.clear()


def update_posting_status(posting_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE job_postings SET status = ? WHERE id = ?", (status, posting_id))
        conn.commit()
    get_postings.clear()


def sync_company_date(company_name):
    """Update matching company's last_checked to today (case-insensitive name match)."""
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_checked = ? WHERE LOWER(name) = LOWER(?)",
            (today, company_name),
        )
        conn.commit()
    get_companies.clear()


def mark_posting_applied(posting_id, company_name):
    update_posting_status(posting_id, "Applied")
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_applied = ? WHERE LOWER(name) = LOWER(?)",
            (today, company_name),
        )
        conn.commit()
    get_companies.clear()


def delete_posting(posting_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM job_postings WHERE id = ?", (posting_id,))
        conn.commit()
    get_postings.clear()


# ── Jobs Tab UI ───────────────────────────────────────────────────────────────

JCOLS    = [1.6, 2.0, 1.1, 1.6, 1.4, 2.2, 1.5, 0.6]
JHEADERS = ["Company", "Role", "Added", "Status", "Link", "Notes", "", ""]


@st.fragment
def render_jobs_tab():
    postings = get_postings()

    # ── add form ──
    with st.expander("➕  Add Job Posting", expanded=not postings):
        with st.form("add_posting_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            company    = c1.text_input("Company *")
            role       = c2.text_input("Role / Position *")
            url        = st.text_input("Job Posting URL", placeholder="https://...")
            c3, c4     = st.columns(2)
            date_added = c3.date_input("Date Added", value=date.today())
            notes      = c4.text_input("Notes (optional)")
            if st.form_submit_button("Add", type="primary", use_container_width=True):
                if company.strip():
                    add_posting(company.strip(), role.strip(), url.strip(), date_added.isoformat(), notes.strip())
                    label = f"**{role.strip()}** at **{company.strip()}**" if role.strip() else f"**{company.strip()}**"
                    st.success(f"Added {label}")
                    st.rerun()
                else:
                    st.error("Company is required.")

    if not postings:
        st.info("No job postings yet — add one above.")
        return

    st.markdown("<br>", unsafe_allow_html=True)

    # header
    hcols = st.columns(JCOLS)
    for col, label in zip(hcols, JHEADERS):
        col.markdown(
            f'<span style="font-size:0.72rem;font-weight:700;color:#9ca3af;'
            f'text-transform:uppercase;letter-spacing:0.07em">{label}</span>',
            unsafe_allow_html=True,
        )
    st.markdown('<hr style="border:none;border-top:1.5px solid #e5e7eb;margin:4px 0 6px">', unsafe_allow_html=True)

    for pid, company, role, url, date_added, status, notes in postings:
        _, bg, fg = STATUS_STYLE.get(status, ("bg", "#f3f4f6", "#6b7280"))
        status_badge = (
            f'<span style="display:inline-block;padding:2px 10px;border-radius:20px;'
            f'background:{bg};color:{fg};font-size:0.76rem;font-weight:600;border:1px solid {fg}33">'
            f'{status}</span>'
        )
        link_html = (
            f'<a class="careers-link" href="{url}" target="_blank">↗ View</a>'
            if url else '<span style="color:#d1d5db;font-size:0.82rem">—</span>'
        )
        note_html = f'<span class="note-text">{notes}</span>' if notes else '<span style="color:#d1d5db">—</span>'

        row = st.columns(JCOLS)
        row[0].markdown(f'<span class="company-name">{company}</span>', unsafe_allow_html=True)
        row[1].markdown(f'<span style="font-size:0.88rem;color:#374151">{role}</span>', unsafe_allow_html=True)
        row[2].markdown(f'<span class="date-text">{date_added}</span>', unsafe_allow_html=True)
        row[3].markdown(status_badge, unsafe_allow_html=True)
        row[4].markdown(link_html, unsafe_allow_html=True)
        row[5].markdown(note_html, unsafe_allow_html=True)

        if status != "Applied":
            if row[6].button("✓ Applied", key=f"app_{pid}", type="primary", help="Mark applied & sync company date"):
                mark_posting_applied(pid, company)
                st.rerun()
        else:
            row[6].markdown('<span style="color:#16a34a;font-size:0.8rem;font-weight:600">✓ Done</span>', unsafe_allow_html=True)

        if row[7].button("🗑", key=f"del_p_{pid}", help="Delete"):
            delete_posting(pid)
            st.rerun()


        st.markdown('<hr class="row-divider">', unsafe_allow_html=True)


@st.cache_data(ttl=300)
def _load_stats_data():
    with get_conn() as conn:
        return conn.execute(
            "SELECT company_name, job_title, applied_date, rejected_at "
            "FROM applications WHERE applied_date IS NOT NULL ORDER BY applied_date DESC"
        ).fetchall()


def render_stats_tab():
    st.subheader("Application Stats")

    apps_raw = _load_stats_data()

    if not apps_raw:
        st.info("No data yet — run Gmail Sync on tracked companies to populate this tab.")
        return

    df = pd.DataFrame(apps_raw, columns=["company", "job_title", "applied_date", "rejected_at"])
    df["applied_date"] = pd.to_datetime(df["applied_date"], errors="coerce")
    df["rejected_at"]  = pd.to_datetime(df["rejected_at"],  errors="coerce")
    df = df.dropna(subset=["applied_date"])

    today    = pd.Timestamp.today().normalize()
    df["days_ago"] = (today - df["applied_date"]).dt.days

    total    = len(df)
    active   = int(df["rejected_at"].isna().sum())
    rej_df   = df.dropna(subset=["rejected_at"]).copy()
    rej_df["days_to_reject"] = (rej_df["rejected_at"] - rej_df["applied_date"]).dt.days.clip(lower=0)

    # ── Summary cards ──
    c1, c2, c3 = st.columns(3)
    c1.metric("Total Applications", total)
    c2.metric("Active",             active)
    c3.metric("Rejected",           len(rej_df))

    # ── Time-to-rejection metrics ──
    if not rej_df.empty:
        st.markdown("")
        avg_rej = rej_df["days_to_reject"].mean()
        med_rej = rej_df["days_to_reject"].median()
        pct_3d  = (rej_df["days_to_reject"] <= 3).mean()
        t1, t2, t3 = st.columns(3)
        t1.metric("Avg days to rejection",    f"{avg_rej:.0f}d")
        t2.metric("Median days to rejection", f"{med_rej:.0f}d")
        t3.metric("Rejected within 3 days",   f"{pct_3d:.0%}",
                  help="High % may indicate ATS/resume screening rather than manual review")

    st.markdown("---")

    # ── Chart: applications + rejections with selectable window ──
    _WINDOWS = {"Last 14 days": 14, "Last 30 days": 30, "Last 12 weeks": 84}
    window_label = st.radio(
        "Window", list(_WINDOWS.keys()), horizontal=True, key="stats_window"
    )
    window_days  = _WINDOWS[window_label]
    use_weekly   = window_label == "Last 12 weeks"

    df_win = df[df["days_ago"] <= window_days].copy()
    if not df_win.empty:
        rej_win = rej_df[rej_df["rejected_at"] >= (today - pd.Timedelta(days=window_days))].copy()

        if use_weekly:
            df_win["bucket"] = df_win["applied_date"].dt.to_period("W").apply(lambda p: p.start_time)
            apps_grouped = df_win.groupby("bucket").size().rename("Applied")
            if not rej_win.empty:
                rej_win["bucket"] = rej_win["rejected_at"].dt.to_period("W").apply(lambda p: p.start_time)
                rej_grouped = rej_win.groupby("bucket").size().rename("Rejections")
            else:
                rej_grouped = pd.Series(dtype=int, name="Rejections")
            combined = pd.concat([apps_grouped, rej_grouped], axis=1).fillna(0).astype(int)
            this_monday = today - pd.Timedelta(days=today.weekday())
            all_buckets = pd.date_range(end=this_monday, periods=12, freq="W-MON")
        else:
            df_win["bucket"] = df_win["applied_date"].dt.normalize()
            apps_grouped = df_win.groupby("bucket").size().rename("Applied")
            if not rej_win.empty:
                rej_win["bucket"] = rej_win["rejected_at"].dt.normalize()
                rej_grouped = rej_win.groupby("bucket").size().rename("Rejections")
            else:
                rej_grouped = pd.Series(dtype=int, name="Rejections")
            combined = pd.concat([apps_grouped, rej_grouped], axis=1).fillna(0).astype(int)
            all_buckets = pd.date_range(end=today, periods=window_days, freq="D")

        combined = combined.reindex(all_buckets, fill_value=0)
        combined_reset = combined.reset_index().rename(columns={"index": "bucket"})
        combined_reset["label"] = combined_reset["bucket"].apply(lambda d: f"{d.month}/{d.day}")
        label_order  = combined_reset["label"].tolist()
        combined_long = combined_reset.melt(
            id_vars=["bucket", "label"], var_name="Type", value_name="Count"
        )

        chart = (
            alt.Chart(combined_long)
            .mark_bar()
            .encode(
                x=alt.X("label:N", title=None, sort=label_order,
                         axis=alt.Axis(labelAngle=0 if use_weekly else -45)),
                xOffset=alt.XOffset("Type:N",
                                    scale=alt.Scale(domain=["Applied", "Rejections"])),
                y=alt.Y("Count:Q", title="Count", axis=alt.Axis(tickMinStep=1)),
                color=alt.Color(
                    "Type:N",
                    scale=alt.Scale(domain=["Applied", "Rejections"],
                                    range=["#3b82f6", "#ef4444"]),
                    legend=alt.Legend(orient="top", title=None),
                ),
            )
            .properties(height=250)
        )
        st.altair_chart(chart, use_container_width=True)

    st.markdown("")

    # ── Velocity ──
    last7   = int((df["days_ago"] <= 7).sum())
    v1, v2  = st.columns(2)
    v1.metric("Applied last 7 days", last7)
    sorted_dates = df["applied_date"].sort_values()
    if len(sorted_dates) > 1:
        avg_gap = sorted_dates.diff().dt.days.dropna().mean()
        v2.metric("Avg days between apps", f"{avg_gap:.1f}d")

    st.markdown("---")

    # ── Pipeline aging (active apps only) ──
    active_df = df[df["rejected_at"].isna()].copy()
    if not active_df.empty:
        st.markdown("**Active pipeline**")

        def _simple_bucket(d):
            if d <= 7:  return "0–7d"
            if d <= 14: return "7–14d"
            return "14+d"

        active_df["Bucket"] = active_df["days_ago"].apply(_simple_bucket)
        bucket_order  = ["0–7d", "7–14d", "14+d"]
        bucket_counts = active_df["Bucket"].value_counts().reindex(bucket_order, fill_value=0)
        st.bar_chart(bucket_counts.rename("Active Applications"))

    st.markdown("---")

    # ── Applications table ──
    st.markdown("**Applications log**")
    tbl_df = df[df["job_title"].notna() & (df["job_title"].str.strip() != "")].copy()
    if tbl_df.empty:
        st.info("No applications with a job title recorded yet.")
    else:
        tbl_df = tbl_df[["company", "job_title", "applied_date", "rejected_at"]].copy()
        tbl_df["applied_date"] = tbl_df["applied_date"].dt.strftime("%Y-%m-%d").fillna("")
        tbl_df["rejected_at"]  = tbl_df["rejected_at"].dt.strftime("%Y-%m-%d").fillna("")
        tbl_df.columns = ["Company", "Job Title", "Date Applied", "Date Rejected"]
        st.dataframe(tbl_df, use_container_width=True, hide_index=True)


def _age_badge_html(d):
    """Return an HTML badge for an age in days (module-level, used by multiple tabs)."""
    if d is None:
        return '<span class="badge" style="background:#d1d5db;color:#6b7280">never</span>'
    return f'<span class="badge" style="background:{staleness_color(d)}">{d}d</span>'


def _trash_email_entry(e):
    """Trash a Gmail email entry dict (trashes by thread, falls back to message)."""
    thread_id = e.get("thread_id")
    msg_id    = e.get("msg_id")
    if thread_id:
        trash_gmail_thread(thread_id)
    elif msg_id:
        trash_gmail_message(msg_id)


@st.fragment
def render_rejections_tab():
    st.subheader("Rejection Sync")
    st.markdown(
        '<p style="color:#6b7280;font-size:0.9rem;margin-bottom:1rem">'
        "Rejection emails found during your last Gmail sync. "
        "Click <b>Update Tracker</b> to mark matching job postings as Rejected.</p>",
        unsafe_allow_html=True,
    )

    if not GMAIL_AVAILABLE:
        st.error("Google API libraries are not installed.")
        st.code("pip install google-auth-oauthlib google-auth-httplib2 google-api-python-client")
        return

    if not os.path.exists(CREDENTIALS_FILE):
        st.warning(f"`{CREDENTIALS_FILE}` not found. Complete Gmail setup on the ⚙️ Setup tab first.")
        return

    # ── Controls ──
    c1, c2, c3 = st.columns([1.3, 1.0, 1])
    sync_days = c3.number_input("Days to scan", min_value=1, value=3, step=1, key="rej_days")
    if c1.button("🔄  Sync Gmail", type="primary", key="rej_sync_btn"):
        with st.spinner("Syncing…  (a browser window may open for first-time authorization)"):
            log = run_gmail_sync(days=sync_days)
        st.session_state["gmail_log"]     = log
        st.session_state["gmail_synced"]  = datetime.now().strftime("%b %d %Y · %I:%M %p")
        st.session_state["rej_updated"]   = set()
        st.session_state["rej_dismissed"] = set()
        for k in list(st.session_state.keys()):
            if k.startswith("rej_sel_"):
                del st.session_state[k]
        st.rerun()

    if os.path.exists(TOKEN_FILE) and c2.button("Disconnect Gmail", key="rej_disconnect_btn"):
        os.remove(TOKEN_FILE)
        for k in ("gmail_log", "gmail_synced"):
            st.session_state.pop(k, None)
        st.rerun()

    if "gmail_synced" in st.session_state:
        st.caption(f"Last synced: {st.session_state['gmail_synced']}")

    if "gmail_log" not in st.session_state:
        return

    log           = st.session_state["gmail_log"]
    rej_updated   = st.session_state.get("rej_updated",   set())
    rej_dismissed = st.session_state.get("rej_dismissed", set())

    all_items = [(i, e) for i, e in enumerate(log) if e.get("type") == "rejection"]
    visible   = [(i, e) for i, e in all_items if i not in rej_dismissed]

    st.markdown("---")

    if not visible:
        has_synced = bool(all_items) or any(e.get("type") != "rejection" for e in log)
        if has_synced:
            st.info("No rejection emails found in the last sync.")
        else:
            st.info("Run a sync to check for rejection emails.")
        return

    # ── Header + Select All ──
    action_vis = [(i, e) for i, e in visible if i not in rej_updated]
    sel_ids    = [i for i, _ in action_vis if st.session_state.get(f"rej_sel_{i}", False)]
    n_pending  = len(action_vis)
    n_done     = sum(1 for i, _ in visible if i in rej_updated)

    parts = []
    if n_pending: parts.append(f"**{n_pending}** to review")
    if n_done:    parts.append(f"**{n_done}** updated")

    hc1, hc2 = st.columns([3.5, 1.0])
    if parts: hc1.markdown(" · ".join(parts))
    if action_vis:
        sel_label = "Deselect All" if len(sel_ids) == n_pending else "Select All"
        if hc2.button(sel_label, key="rej_sel_all_btn"):
            new_val = len(sel_ids) < n_pending
            for i, _ in action_vis:
                st.session_state[f"rej_sel_{i}"] = new_val
            st.rerun()

    # ── Bulk action bar ──
    if sel_ids:
        bc1, bc2, bc3, _ = st.columns([1.5, 1.8, 1.1, 2.0])
        if bc1.button(f"Update {len(sel_ids)}", type="primary", key="rej_bulk_update"):
            for bi in sel_ids:
                be = next(e for j, e in action_vis if j == bi)
                mark_company_rejected(be["company"])
                rej_updated.add(bi)
                st.session_state.pop(f"rej_sel_{bi}", None)
            st.session_state["rej_updated"] = rej_updated
            st.rerun()
        if bc2.button(f"Update & 🗑 {len(sel_ids)}", key="rej_bulk_update_del"):
            for bi in sel_ids:
                be = next(e for j, e in action_vis if j == bi)
                mark_company_rejected(be["company"])
                try:
                    _trash_email_entry(be)
                except Exception:
                    pass
                rej_updated.add(bi)
                st.session_state.pop(f"rej_sel_{bi}", None)
            st.session_state["rej_updated"] = rej_updated
            st.rerun()
        if bc3.button(f"Dismiss {len(sel_ids)}", key="rej_bulk_dismiss"):
            for bi in sel_ids:
                rej_dismissed.add(bi)
                st.session_state.pop(f"rej_sel_{bi}", None)
            st.session_state["rej_dismissed"] = rej_dismissed
            st.rerun()

    st.markdown("")

    # ── Per-item rows ──
    for i, e in visible:
        company    = e["company"]
        age_badge  = _age_badge_html(e.get("new_age"))
        confidence = e.get("confidence", 0.0)
        evidence   = e.get("evidence", [])

        _, sender_addr = parseaddr(e.get("sender", ""))
        sender_html = (
            f'<span style="color:#6b7280;font-size:0.78rem">{sender_addr}</span>'
            if sender_addr else ""
        )

        if i in rej_updated:
            st.markdown(
                f'❌ &nbsp;<span class="company-name">{company}</span>'
                f'&nbsp;{age_badge}&nbsp;'
                f'<span style="color:#dc2626;font-size:0.82rem">marked rejected</span>'
                f'<br><span style="color:#9ca3af;font-size:0.78rem;padding-left:1.4rem">'
                f'{e.get("subject", "")}</span>'
                f'<br><span style="padding-left:1.4rem">{sender_html}</span>',
                unsafe_allow_html=True,
            )
        else:
            row = st.columns([0.35, 3.0, 1.5, 1.8, 0.9])
            row[0].checkbox("", key=f"rej_sel_{i}", label_visibility="collapsed")
            row[1].markdown(
                f'<span class="company-name">{company}</span>'
                f'&nbsp;{age_badge}'
                f'<br><span style="color:#9ca3af;font-size:0.78rem">{e.get("subject", "")}</span>'
                f'<br>{sender_html}',
                unsafe_allow_html=True,
            )
            if row[2].button("Update Tracker", key=f"rej_apply_{i}", type="primary"):
                mark_company_rejected(company)
                rej_updated.add(i)
                st.session_state["rej_updated"] = rej_updated
                st.rerun()
            if row[3].button("Update & 🗑", key=f"rej_apply_del_{i}"):
                mark_company_rejected(company)
                try:
                    _trash_email_entry(e)
                except Exception as ex:
                    st.error(f"Failed to delete: {ex}")
                rej_updated.add(i)
                st.session_state["rej_updated"] = rej_updated
                st.rerun()
            if row[4].button("Dismiss", key=f"rej_dismiss_{i}"):
                rej_dismissed.add(i)
                st.session_state["rej_dismissed"] = rej_dismissed
                st.rerun()

        # Confidence + matched phrases
        if evidence:
            ev_str = ", ".join(f'"{p}"' for p in evidence[:4])
            extra  = f" (+{len(evidence) - 4} more)" if len(evidence) > 4 else ""
            st.caption(f"Confidence {confidence:.0%} · Matched: {ev_str}{extra}")

        body_text = (e.get("body") or "").strip()
        if body_text:
            with st.expander("View email body"):
                st.text(body_text)


def render_setup_tab():
    st.subheader("Setup Guide")
    st.markdown(
        '<p style="color:#6b7280;font-size:0.9rem;margin-bottom:1.5rem">'
        "Get the app running on your own computer in a few steps — everything stays local and private."
        "</p>",
        unsafe_allow_html=True,
    )

    with st.expander("① Set up Gmail sync", expanded=True):
        st.markdown("""
Gmail sync is free to set up through Google Cloud.

**Step 1 — Enable the Gmail API:**
1. Go to **[Google Cloud Console](https://console.cloud.google.com/)** and create a new project
2. Go to **APIs & Services → Library**, search **Gmail API**, click **Enable**

**Step 2 — Create credentials:**
1. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**
2. Set **Application type** to **Desktop app**, click **Create**
3. Click **Download JSON** and save it as **`credentials.json`** in the app folder

**Step 3 — Configure the consent screen:**
1. Go to **APIs & Services → OAuth consent screen**, choose **External**, click **Create**
2. Fill in the required fields on the first page:
   - **App name** — e.g. *Job Tracker*
   - **User support email** — select your email from the dropdown
   - **Developer contact email** (at the bottom) — enter your email
3. Click **Save and Continue**
4. On the **Scopes** page click **Save and Continue** without adding anything
5. On the **Test users** page click **Add Users**, enter your Gmail address, click **Add**, then **Save and Continue**
   - If you don't see a Test users page during setup, go back to **OAuth consent screen → Audience** and add yourself there
6. No need to publish — testing mode works fine for personal use

**Step 4 — Authorize:**
1. Restart the app (`streamlit run app.py`)
2. Open the **Gmail Sync** tab and click **Sync Gmail**
3. A browser window will open — sign in and approve access
4. Done! A `token.json` file is saved so you won't need to authorize again
        """)

    with st.expander("② Keeping your data private", expanded=False):
        st.markdown("""
These files live only on your computer and should never be shared:
- `credentials.json` — your Google OAuth credentials
- `token.json` — your Gmail auth token
- `job_tracker.db` — your personal data

If you ever push code changes back to GitHub, these are already listed in `.gitignore` so they won't be included.
        """)


# ── Entry point ───────────────────────────────────────────────────────────────

@st.fragment
def render_qa_tab():
    st.subheader("Q&A Bank")
    st.markdown(
        '<p style="color:#6b7280;font-size:0.9rem;margin-bottom:1rem">'
        "Store your answers to common application questions. Filter by company when preparing to apply."
        "</p>",
        unsafe_allow_html=True,
    )

    if "qa_editing_id" not in st.session_state:
        st.session_state.qa_editing_id = None

    all_qa = get_qa()

    # ── company filter ──
    qa_companies = sorted({r[1] for r in all_qa if r[1]})
    filter_options = ["All", "General"] + [c for c in qa_companies if c != "General"]
    company_filter = st.selectbox(
        "Company",
        filter_options,
        key="qa_filter",
        label_visibility="collapsed",
    )

    # ── add form ──
    with st.expander("➕  Add Question", expanded=not bool(all_qa)):
        with st.form("qa_add_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            default_co = "" if company_filter == "All" else company_filter
            company_input = c1.text_input(
                "Company",
                value=default_co,
                placeholder="Figma  (blank = General)",
                key="qa_new_co",
            )
            question_input = c2.text_input(
                "Question *",
                placeholder="Why do you want to work here?",
                key="qa_new_q",
            )
            answer_input = st.text_area(
                "Answer",
                placeholder="Your answer…",
                height=130,
                key="qa_new_a",
            )
            if st.form_submit_button("Add Question", type="primary", use_container_width=True):
                if question_input.strip():
                    co = company_input.strip() if company_input.strip() else "General"
                    add_qa(co, question_input.strip(), answer_input.strip())
                    st.success(f"Added for **{co}**.")
                    st.rerun()
                else:
                    st.error("Question is required.")

    st.markdown("<br>", unsafe_allow_html=True)

    # ── filtered list ──
    if company_filter == "All":
        filtered = all_qa
    elif company_filter == "General":
        filtered = [r for r in all_qa if not r[1] or r[1] == "General"]
    else:
        filtered = [r for r in all_qa if r[1] == company_filter]

    if not filtered:
        st.info("No questions yet for this filter — add one above.")
        return

    current_company = object()  # sentinel
    for qa_id, company, question, answer in filtered:
        # Section header when showing all
        if company_filter == "All" and company != current_company:
            current_company = company
            st.markdown(
                f'<p style="font-size:0.78rem;font-weight:700;color:#9ca3af;'
                f'text-transform:uppercase;letter-spacing:0.07em;margin:12px 0 4px">'
                f'{company or "General"}</p>',
                unsafe_allow_html=True,
            )

        hcols = st.columns([7.5, 0.6, 0.6])
        hcols[0].markdown(f"**{question}**")
        if hcols[1].button("✎", key=f"qa_e_{qa_id}", help="Edit answer"):
            st.session_state.qa_editing_id = (
                qa_id if st.session_state.qa_editing_id != qa_id else None
            )
        if hcols[2].button("🗑", key=f"qa_d_{qa_id}", help="Delete question"):
            delete_qa(qa_id)
            if st.session_state.qa_editing_id == qa_id:
                st.session_state.qa_editing_id = None
            st.rerun()

        if st.session_state.qa_editing_id == qa_id:
            new_ans = st.text_area(
                "answer",
                value=answer or "",
                key=f"qa_ta_{qa_id}",
                height=160,
                label_visibility="collapsed",
            )
            s1, s2, _ = st.columns([1, 1, 6])
            if s1.button("Save", key=f"qa_sv_{qa_id}", type="primary"):
                update_qa(qa_id, new_ans)
                st.session_state.qa_editing_id = None
            if s2.button("Cancel", key=f"qa_cn_{qa_id}"):
                st.session_state.qa_editing_id = None
        else:
            if answer:
                st.markdown(
                    f'<div style="color:#374151;font-size:0.88rem;padding:4px 0 6px 10px;'
                    f'border-left:3px solid #e5e7eb;white-space:pre-wrap">{answer}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.caption("_No answer yet — click ✎ to add one_")

        st.markdown('<hr class="row-divider">', unsafe_allow_html=True)


def main():
    st.set_page_config(page_title="Job Application Tracker", layout="wide", page_icon="🔍")
    st.markdown(CSS, unsafe_allow_html=True)
    init_db()

    st.markdown("## 🔍 Job Application Tracker")
    st.caption(f"Today · {date.today().strftime('%B %d, %Y')}")
    st.markdown("<br>", unsafe_allow_html=True)

    tab_view, tab_add, tab_qa, tab_jobs, tab_stats, tab_gmail, tab_reject, tab_setup = st.tabs([
        "📋  Companies", "➕  Add Company", "💬  Q&A", "🎯  High-Effort Jobs", "📊  Stats",
        "📧  Gmail Sync", "❌  Rejections", "⚙️  Gmail Setup",
    ])

    with tab_view:
        render_companies_tab()

    with tab_add:
        render_add_tab()

    with tab_qa:
        render_qa_tab()

    with tab_jobs:
        render_jobs_tab()

    with tab_stats:
        render_stats_tab()

    with tab_gmail:
        render_gmail_tab()

    with tab_reject:
        render_rejections_tab()

    with tab_setup:
        render_setup_tab()


if __name__ == "__main__":
    main()
