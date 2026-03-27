import os
import re
import base64
import streamlit as st
import sqlite3
from datetime import date, datetime, timedelta
from email.utils import parseaddr
from urllib.parse import urlparse
import pandas as pd

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


def init_db():
    with get_conn() as conn:
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
        conn.commit()



def get_companies():
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, last_checked, interval_days, notes, careers_url, recruiting_email, last_applied FROM companies ORDER BY last_checked IS NOT NULL, last_checked ASC, name ASC"
        ).fetchall()


def add_company(name, last_applied=None, notes="", careers_url="", recruiting_email=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO companies (name, last_applied, interval_days, notes, careers_url, recruiting_email) VALUES (?, ?, 7, ?, ?, ?)",
            (name, last_applied, notes, careers_url, recruiting_email),
        )
        conn.commit()


def mark_scraped(company_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_checked = ? WHERE id = ?",
            (date.today().isoformat(), company_id),
        )
        conn.commit()



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


def update_company(company_id, notes, careers_url, recruiting_email=""):
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET notes = ?, careers_url = ?, recruiting_email = ? WHERE id = ?",
            (notes, careers_url, recruiting_email, company_id),
        )
        conn.commit()


def delete_company(company_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
        conn.commit()


# ── Gmail constants ───────────────────────────────────────────────────────────

GMAIL_SCOPES     = ["https://www.googleapis.com/auth/gmail.modify"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE       = "token.json"

ATS_DOMAINS = [
    "lever.co", "greenhouse.io", "greenhouse-mail.io",
    "ashbyhq.com", "workday.com", "smartrecruiters.com",
]

# Priority-ordered: first match wins
KEYWORD_STATUS_MAP = [
    (["offer letter", "pleased to offer", "extend an offer",
      "job offer", "employment offer"], "Offer"),
    (["we'd like to move forward", "move forward with your application",
      "next steps", "interview", "schedule a call", "phone screen",
      "technical assessment", "take-home"], "Interviewing"),
    (["unfortunately", "not moving forward", "not selected", "other candidates",
      "will not be moving", "won't be moving", "decided not to proceed",
      "going in a different direction"], "Rejected"),
    (["application received", "received your application", "thank you for applying",
      "successfully submitted", "we've received", "have received your application",
      "application has been received"], "Applied"),
]

# Keywords that reliably identify application-confirmation emails (subject takes priority)
_CONFIRM_SUBJ_KWS = [
    "thank you for applying",
    "thanks for applying",
    "thank you for your application",
    "thanks for your application",
    "application received",
    "application confirmation",
    "we received your application",
    "application submitted",
    "successfully applied",
    "you've applied",
]
_CONFIRM_BODY_KWS = [
    "received your application",
    "we received your application",
    "have received your application",
    "thank you for applying",
    "thanks for applying",
    "application has been received",
    "we've received your application",
    "successfully submitted your application",
]
# Gmail subject-search terms used to widen the fetch beyond ATS domains
_GMAIL_SUBJECT_TERMS = [
    'subject:("thank you for applying")',
    'subject:("thanks for applying")',
    'subject:("thank you for your application")',
    'subject:("thanks for your application")',
    'subject:("application received")',
    'subject:("application confirmation")',
    'subject:("we received your application")',
]

_STATUS_RANK = {"To Do": 0, "In Progress": 1, "Applied": 2, "Interviewing": 3, "Offer": 4}


# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_date(d):
    return date.fromisoformat(d) if isinstance(d, str) else d


def days_ago(last_scraped):
    if not last_scraped:
        return None
    return (date.today() - parse_date(last_scraped)).days


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


def _extract_email_body(payload, max_chars=3000):
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")[:max_chars]
    if mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_email_body(part, max_chars)
            if text:
                return text
    return ""


def _infer_status(subject, body):
    text = (subject + " " + body).lower()
    for keywords, status in KEYWORD_STATUS_MAP:
        if any(kw in text for kw in keywords):
            return status
    return None


_REJECT_SIGNALS = [
    "regret to inform you",
    "regret to let you know",
    "decided not to move forward",
    "not moving forward with your",
    "will not be moving forward",
    "decided to proceed with other candidates",
    "decided to move forward with other",
    "chosen to move forward with other",
    "moving forward with other candidates",
    "not selected for this position",
    "not selected for this role",
    "not a fit for this role",
    "not a fit for this position",
    "have decided not to",
    "chosen not to move forward",
]

def _is_application_confirmation(subject, body):
    """True if this email is an application-received confirmation.

    Uses subject line first (most reliable) then body, stripping out
    boilerplate conditional phrases like 'if you are not selected…'
    that would otherwise trigger false rejection matches.
    """
    body_l = body.lower()
    # Strip conditional/hypothetical negatives before any signal checks
    body_clean = re.sub(
        r'\bif\b[^.!?\n]*\b(?:not selected|not a match|not moving|no longer consider)\b[^.!?\n]*',
        '', body_l
    )
    # Direct rejection signals — checked against cleaned body
    if any(sig in body_clean for sig in _REJECT_SIGNALS):
        return False
    subj_l = subject.lower()
    if any(kw in subj_l for kw in _CONFIRM_SUBJ_KWS):
        return True
    return any(kw in body_clean for kw in _CONFIRM_BODY_KWS)


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

    # Company name word-boundary match in subject line
    for row in companies:
        name_l = row[1].lower()
        if re.search(r'\b' + re.escape(name_l) + r'\b', subj_l):
            return row[1]

    # For ATS senders: fall back to searching company name in email body
    if is_ats and body:
        body_l = body.lower()
        for row in companies:
            name_l = row[1].lower()
            if re.search(r'\b' + re.escape(name_l) + r'\b', body_l):
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


_SKIP_NAMES = {"our", "the", "a", "an", "your", "this", "that", "us", "we", "i", "you"}

_JOB_BOARD_DOMAINS = {
    "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com",
    "monster.com", "careerbuilder.com", "dice.com", "simplyhired.com",
    "hired.com", "wellfound.com", "angellist.com", "builtin.com",
    "handshake.com", "joinhandshake.com", "idealist.org",
}

def _is_job_board_sender(domain):
    return any(domain == b or domain.endswith("." + b) for b in _JOB_BOARD_DOMAINS)

# Ordered patterns: each captures the company name in group 1
_COMPANY_SUBJECT_PATTERNS = [
    # "applying/applied/application to Company"
    r"\bappl(?:ying|ied|ication)\s+to\s+([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?=\s*(?:has been|is |are |\-|[|!?,]|$))",
    # "at Company" near end
    r"\bat\s+([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)(?=\s*(?:has been|is |are |\-|[|!?,]|$))",
    # "Company - Application…" at subject start
    r"^([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)\s*[-|]\s*(?:application|your application|we received)",
    # "… | Company" at subject end
    r"[|]\s*([A-Z][A-Za-z0-9 &.,'\-]{1,50}?)\s*$",
]

def _extract_company_from_subject(subject):
    """Parse a company name from an ATS email subject. Returns None if uncertain."""
    for pat in _COMPANY_SUBJECT_PATTERNS:
        m = re.search(pat, subject)
        if m:
            name = m.group(1).strip().rstrip(".,- ")
            if len(name) > 2 and name.split()[0].lower() not in _SKIP_NAMES:
                return name
    return None


def _should_update(current, new):
    if current == new or current == "Rejected":
        return False
    if new == "Rejected":
        return True
    return _STATUS_RANK.get(new, 0) > _STATUS_RANK.get(current, 0)


def run_gmail_sync(days=90):
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

    tracked_names = {r[1].lower() for r in companies}

    # Gmail returns newest first — keep only the most recent email per company
    best     = {}   # company_lower → entry (tracked companies)
    best_new = {}   # name_lower    → entry (companies not yet in tracker)

    for ref in msgs:
        try:
            msg = svc.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        except Exception:
            continue
        hdrs    = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        subject = hdrs.get("Subject", "")
        sender  = hdrs.get("From", "")
        body    = _extract_email_body(msg["payload"])

        # Only surface application-received emails
        if not _is_application_confirmation(subject, body):
            continue

        internal_ms = int(msg.get("internalDate", 0))
        email_date  = datetime.fromtimestamp(internal_ms / 1000).date() if internal_ms else date.today()
        new_age     = (date.today() - email_date).days

        _, sender_addr = parseaddr(sender)
        sender_addr_l  = sender_addr.lower()
        sender_domain  = sender_addr_l.split("@")[-1] if "@" in sender_addr_l else ""
        is_ats_sender  = any(d in sender_domain for d in ATS_DOMAINS)

        company = _match_company(sender, subject, companies, body)
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

            if key not in best:
                last_checked = company_row[2] if company_row else None
                best[key] = {
                    "type": "pending", "company": company, "subject": subject,
                    "sender": sender, "body": body, "last_checked": last_checked,
                    "email_date": email_date.isoformat(), "new_age": new_age,
                    "msg_id": ref["id"],
                    "thread_id": msg.get("threadId"),
                }
        else:
            # Try to extract company name from subject for untracked companies
            extracted = _extract_company_from_subject(subject)
            if extracted:
                key = extracted.lower()
                if key not in tracked_names and key not in best_new:
                    best_new[key] = {
                        "type": "new", "company": extracted, "subject": subject,
                        "sender": sender, "body": body,
                        "email_date": email_date.isoformat(), "new_age": new_age,
                    }

    if not best and not best_new:
        return [{"type": "info", "message": "No application confirmation emails found."}]

    return list(best.values()) + list(best_new.values())


def mark_company_rejected(company):
    """Set matching job postings to Rejected."""
    for pid, co, _, _, _, status, _ in get_postings():
        if co.lower() == company.lower() and status != "Rejected":
            update_posting_status(pid, "Rejected")


def apply_gmail_match(company, email_date):
    """Set company last_applied to the email's date and mark matching postings Applied."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_applied = ? WHERE LOWER(name) = LOWER(?)",
            (email_date, company),
        )
        conn.commit()
    for pid, co, _, _, _, status, _ in get_postings():
        if co.lower() == company.lower() and status in ("To Do", "In Progress"):
            update_posting_status(pid, "Applied")


# ── UI ────────────────────────────────────────────────────────────────────────

COLS    = [2.0, 1.6, 0.9, 1.5, 2.4, 0.55, 0.55, 0.55, 2.8]
HEADERS = ["Company", "Activity", "Age", "Careers", "Action", "", "", "", "Notes"]


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

    # ── split into 3 groups ──
    # r[7]=last_applied, r[2]=last_checked
    applied_group = [r for r in companies if days_ago(r[7]) is not None and days_ago(r[7]) <= applied_max]
    applied_ids   = {r[0] for r in applied_group}
    checked_group = [r for r in companies if r[0] not in applied_ids and days_ago(r[2]) is not None and days_ago(r[2]) < checked_max]
    checked_ids   = {r[0] for r in checked_group}
    action_group  = [r for r in companies if r[0] not in applied_ids and r[0] not in checked_ids]

    def render_header():
        hcols = st.columns(COLS)
        for col, label in zip(hcols, HEADERS):
            col.markdown(
                f'<span style="font-size:0.72rem;font-weight:700;color:#9ca3af;'
                f'text-transform:uppercase;letter-spacing:0.07em">{label}</span>',
                unsafe_allow_html=True,
            )
        st.markdown('<hr style="border:none;border-top:1.5px solid #e5e7eb;margin:4px 0 6px">', unsafe_allow_html=True)

    def render_rows(group, empty_msg="No companies here."):
        if not group:
            st.markdown(f'<div style="padding:1.5rem 0;text-align:center;color:#9ca3af;font-size:0.9rem">{empty_msg}</div>', unsafe_allow_html=True)
            return
        for cid, name, last_checked, _, notes, careers_url, recruiting_email, last_applied in group:
            d     = days_ago(last_checked)
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
            link_html = (
                f'<a class="careers-link" href="{careers_url}" target="_blank">↗ Careers</a>'
                if careers_url else
                '<span style="color:#bdc1c6;font-size:0.82rem">—</span>'
            )

            c = st.columns(COLS)
            c[0].markdown(name_html, unsafe_allow_html=True)
            c[1].markdown(date_html, unsafe_allow_html=True)
            c[2].markdown(age_html,  unsafe_allow_html=True)
            c[3].markdown(link_html, unsafe_allow_html=True)

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
                st.rerun()

            email_btn_label = "✎" if recruiting_email else "✉"
            email_btn_help  = "Edit recruiting email" if recruiting_email else "Add recruiting email"
            if c[6].button(email_btn_label, key=f"eml_{cid}", help=email_btn_help):
                st.session_state.editing_email_id = cid if st.session_state.editing_email_id != cid else None
                st.session_state.editing_url_id   = None
                st.rerun()

            if c[7].button("🗑", key=f"del_{cid}", help="Delete"):
                delete_company(cid)
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
                    st.rerun()
                if ec3.button("Cancel", key=f"url_cancel_{cid}"):
                    st.session_state.editing_url_id = None
                    st.rerun()

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
                    st.rerun()
                if ec3.button("Cancel", key=f"email_cancel_{cid}"):
                    st.session_state.editing_email_id = None
                    st.rerun()

            st.markdown('<hr class="row-divider">', unsafe_allow_html=True)

    tab1, tab2, tab3 = st.tabs([
        f"Need Action  ({len(action_group)})",
        f"Applied  ({len(applied_group)})",
        f"Checked  ({len(checked_group)})",
    ])
    with tab1:
        render_header()
        render_rows(action_group, empty_msg="✓ All caught up!")
    with tab2:
        render_header()
        render_rows(applied_group, empty_msg="No recent applications.")
    with tab3:
        render_header()
        render_rows(checked_group, empty_msg="No recent checks.")

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
    sync_days = c3.number_input("Days to scan", min_value=1, max_value=365, value=90, step=1, key="gmail_days")
    if c1.button("🔄  Sync Gmail", type="primary"):
        with st.spinner("Syncing…  (a browser window may open for first-time authorization)"):
            log = run_gmail_sync(days=sync_days)
        st.session_state["gmail_log"]       = log
        st.session_state["gmail_synced"]    = datetime.now().strftime("%b %d %Y · %I:%M %p")
        st.session_state["gmail_updated"]   = set()
        st.session_state["gmail_dismissed"] = set()
        st.session_state["gmail_rejected"]  = set()
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

    errors  = [e for e in log if e["type"] == "error"]
    infos   = [e for e in log if e["type"] == "info"]
    items   = [e for e in log if e["type"] == "pending"]

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

    def _render_item(i, e, key_prefix, already_uptodate=False):
        current_age = days_ago(e.get("last_checked"))
        new_age     = e.get("new_age")
        age_html    = f'{_age_badge(current_age)} → {_age_badge(new_age)}'
        _, sender_addr = parseaddr(e.get("sender", ""))
        sender_html = (
            f'<span style="color:#6b7280;font-size:0.78rem">{sender_addr}</span>'
            if sender_addr else ""
        )
        if i in updated:
            st.markdown(
                f'✅ &nbsp;<span class="company-name">{e["company"]}</span>'
                f'&nbsp;{age_html}&nbsp;'
                f'<span style="color:#16a34a;font-size:0.82rem">marked applied</span>'
                f'<br><span style="color:#9ca3af;font-size:0.78rem;padding-left:1.4rem">{e["subject"]}</span>'
                f'<br><span style="padding-left:1.4rem">{sender_html}</span>',
                unsafe_allow_html=True,
            )
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
            def _trash_thread(e):
                thread_id = e.get("thread_id")
                msg_id = e.get("msg_id")
                if thread_id:
                    trash_gmail_thread(thread_id)
                elif msg_id:
                    trash_gmail_message(msg_id)

            if already_uptodate:
                row = st.columns([3.5, 1.0, 0.9, 1.3, 0.8])
            else:
                row = st.columns([3.0, 1.2, 1.5, 0.9, 1.3, 0.8])
            row[0].markdown(
                f'<span class="company-name">{e["company"]}</span>'
                f'&nbsp;{age_html}'
                f'<br><span style="color:#9ca3af;font-size:0.78rem">{e["subject"]}</span>'
                f'<br>{sender_html}',
                unsafe_allow_html=True,
            )
            if already_uptodate:
                if row[1].button("🗑 Delete", key=f"{key_prefix}_trash_{i}"):
                    try:
                        _trash_thread(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
                if row[2].button("Reject", key=f"{key_prefix}_reject_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    st.rerun()
                if row[3].button("Reject & 🗑", key=f"{key_prefix}_reject_del_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    try:
                        _trash_thread(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
                if row[4].button("Dismiss", key=f"{key_prefix}_dismiss_{i}"):
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
            else:
                if row[1].button("Update Tracker", key=f"{key_prefix}_apply_{i}", type="primary"):
                    apply_gmail_match(e["company"], e["email_date"])
                    updated.add(i)
                    st.session_state["gmail_updated"] = updated
                    st.rerun()
                if row[2].button("Update & 🗑", key=f"{key_prefix}_apply_del_{i}"):
                    apply_gmail_match(e["company"], e["email_date"])
                    try:
                        _trash_thread(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    updated.add(i)
                    st.session_state["gmail_updated"] = updated
                    st.rerun()
                if row[3].button("Reject", key=f"{key_prefix}_reject_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    st.rerun()
                if row[4].button("Reject & 🗑", key=f"{key_prefix}_reject_del_{i}"):
                    mark_company_rejected(e["company"])
                    rejected.add(i)
                    st.session_state["gmail_rejected"] = rejected
                    try:
                        _trash_thread(e)
                    except Exception as ex:
                        st.error(f"Failed to delete: {ex}")
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
                if row[5].button("Dismiss", key=f"{key_prefix}_dismiss_{i}"):
                    dismissed.add(i)
                    st.session_state["gmail_dismissed"] = dismissed
                    st.rerun()
        body_text = (e.get("body") or "").strip()
        if body_text:
            with st.expander("View email body"):
                st.text(body_text)

    pending_items = [e for e in items if e["type"] == "pending"]
    new_items     = [e for e in items if e["type"] == "new"]

    # For tracked companies: split changed vs same age
    changed = [(i, e) for i, e in enumerate(items)
               if e["type"] == "pending"
               and i not in dismissed
               and i not in rejected
               and days_ago(e.get("last_checked")) != e.get("new_age")]
    same    = [(i, e) for i, e in enumerate(items)
               if e["type"] == "pending"
               and i not in dismissed
               and (days_ago(e.get("last_checked")) == e.get("new_age")
                    or i in rejected)]
    new_vis = [(i, e) for i, e in enumerate(items)
               if e["type"] == "new" and i not in dismissed]

    if not changed and not same and not new_vis and not errors and not infos:
        st.info("No application confirmation emails found.")
        return

    # ── Tracked companies with a different age ──
    if changed:
        parts = []
        pending = sum(1 for i, _ in changed if i not in updated)
        done    = sum(1 for i, _ in changed if i in updated)
        if pending: parts.append(f"**{pending}** to review")
        if done:    parts.append(f"**{done}** updated")
        if parts:   st.markdown(" · ".join(parts))
        st.markdown("")
        for i, e in changed:
            _render_item(i, e, "main")
    elif not new_vis and not errors and not infos:
        st.info("No new changes — all matched companies are already up to date.")

    # ── New companies not yet in tracker ──
    if new_vis:
        st.markdown("")
        st.markdown(f"**New companies found ({len(new_vis)})**")
        st.caption("These companies were in your ATS emails but aren't in your tracker yet.")
        for i, e in new_vis:
            new_age  = e.get("new_age")
            age_badge = _age_badge(new_age)
            _, sender_addr = parseaddr(e.get("sender", ""))
            sender_html = (
                f'<span style="color:#6b7280;font-size:0.78rem">{sender_addr}</span>'
                if sender_addr else ""
            )
            if i in updated:
                st.markdown(
                    f'✅ &nbsp;<span class="company-name">{e["company"]}</span>'
                    f'&nbsp;{age_badge}&nbsp;'
                    f'<span style="color:#16a34a;font-size:0.82rem">added to tracker</span>'
                    f'<br><span style="color:#9ca3af;font-size:0.78rem;padding-left:1.4rem">{e["subject"]}</span>'
                    f'<br><span style="padding-left:1.4rem">{sender_html}</span>',
                    unsafe_allow_html=True,
                )
            else:
                row = st.columns([5, 1.4, 0.85])
                row[0].markdown(
                    f'<span class="company-name">{e["company"]}</span>'
                    f'&nbsp;{age_badge}'
                    f'<br><span style="color:#9ca3af;font-size:0.78rem">{e["subject"]}</span>'
                    f'<br>{sender_html}',
                    unsafe_allow_html=True,
                )
                if row[1].button("Add to Tracker", key=f"new_apply_{i}", type="primary"):
                    add_company(e["company"], e["email_date"])
                    updated.add(i)
                    st.session_state["gmail_updated"] = updated
                    st.rerun()
                if row[2].button("Dismiss", key=f"new_dismiss_{i}"):
                    dismissed.add(i)
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
                _render_item(i, e, "same", already_uptodate=True)


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


def update_posting_status(posting_id, status):
    with get_conn() as conn:
        conn.execute("UPDATE job_postings SET status = ? WHERE id = ?", (status, posting_id))
        conn.commit()


def sync_company_date(company_name):
    """Update matching company's last_checked to today (case-insensitive name match)."""
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_checked = ? WHERE LOWER(name) = LOWER(?)",
            (today, company_name),
        )
        conn.commit()


def mark_posting_applied(posting_id, company_name):
    update_posting_status(posting_id, "Applied")
    today = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(
            "UPDATE companies SET last_applied = ? WHERE LOWER(name) = LOWER(?)",
            (today, company_name),
        )
        conn.commit()


def delete_posting(posting_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM job_postings WHERE id = ?", (posting_id,))
        conn.commit()


# ── Jobs Tab UI ───────────────────────────────────────────────────────────────

JCOLS    = [1.6, 2.0, 1.1, 1.6, 1.4, 2.2, 1.5, 0.6]
JHEADERS = ["Company", "Role", "Added", "Status", "Link", "Notes", "", ""]


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
2. Enter an app name (e.g. *Job Tracker*) and your email — leave everything else blank
3. On the **Test users** page add your Gmail address, then save
4. No need to publish — testing mode works fine for personal use

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

def main():
    st.set_page_config(page_title="Job Application Tracker", layout="wide", page_icon="🔍")
    st.markdown(CSS, unsafe_allow_html=True)
    init_db()

    st.markdown("## 🔍 Job Application Tracker")
    st.caption(f"Today · {date.today().strftime('%B %d, %Y')}")
    st.markdown("<br>", unsafe_allow_html=True)

    tab_view, tab_add, tab_jobs, tab_gmail, tab_setup = st.tabs([
        "📋  Companies", "➕  Add Company", "🎯  High-Effort Jobs", "📧  Gmail Sync", "⚙️  Gmail Setup",
    ])

    with tab_view:
        render_companies_tab()

    with tab_add:
        render_add_tab()

    with tab_jobs:
        render_jobs_tab()

    with tab_gmail:
        render_gmail_tab()

    with tab_setup:
        render_setup_tab()


if __name__ == "__main__":
    main()
