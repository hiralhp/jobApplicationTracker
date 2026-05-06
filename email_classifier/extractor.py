"""Signal extraction: phrase scanning and conditional stripping."""
from __future__ import annotations

import re
from email.utils import parseaddr

from .config import (
    ATS_DOMAINS,
    CONDITIONAL_STRIP_PATTERN,
    ATS_CONFIRMATION_MULT,
    SUBJECT_MULTIPLIER,
    OFFER_PHRASES,
    INTERVIEW_PHRASES,
    REJECTION_PHRASES,
    REJECTION_PATTERNS,
    STRONG_REJECTION_PATTERNS,
    CONFIRMATION_PHRASES,
    UPDATE_PHRASES,
)
from .models import EmailSignals


def _normalize(text: str) -> str:
    """Lowercase + normalize Unicode apostrophes/quotes + collapse whitespace.

    Handles curly quotes from HTML emails so patterns like "won't" match
    regardless of whether the source used a straight or curly apostrophe.
    """
    text = (
        text
        .replace("\u2019", "'").replace("\u2018", "'")   # ' '
        .replace("\u201c", '"').replace("\u201d", '"')   # " "
        .replace("\u2013", "-").replace("\u2014", "-")   # – —
    )
    text = text.lower()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _scan_regex(body_clean: str, patterns: list) -> tuple[list[str], float]:
    """Scan compiled regex patterns against body_clean.

    Uses the actual matched text (m.group(0)) as the evidence string so callers
    can see exactly what fragment triggered the pattern.
    """
    hits: list[str] = []
    score = 0.0
    for pattern, weight in patterns:
        m = pattern.search(body_clean)
        if m:
            hits.append(m.group(0))
            score += weight
    return hits, score


def _parse_domain(sender: str) -> str:
    _, addr = parseaddr(sender)
    addr_l = addr.lower()
    return addr_l.split("@")[-1] if "@" in addr_l else addr_l


def _scan(subject_lower: str, body_clean: str, phrases: list[tuple[str, float]]) -> tuple[list[str], float]:
    hits: list[str] = []
    score = 0.0
    for phrase, weight in phrases:
        in_subject = phrase in subject_lower
        in_body = phrase in body_clean
        if in_subject:
            hits.append(phrase)
            score += weight * SUBJECT_MULTIPLIER
        if in_body:
            if not in_subject:
                hits.append(phrase)
            score += weight * 1.0
    # deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            deduped.append(h)
    return deduped, score


def extract_signals(subject: str, body: str, sender: str) -> EmailSignals:
    subject_lower = _normalize(subject)
    body_lower = _normalize(body)

    sender_domain = _parse_domain(sender)
    is_ats_sender = any(d in sender_domain for d in ATS_DOMAINS)

    # Strip conditional/hypothetical negatives from body before scanning
    body_clean = re.sub(CONDITIONAL_STRIP_PATTERN, "", body_lower, flags=re.IGNORECASE)

    # For reply/forward threads the subject belongs to the original email, not the current
    # message — suppress subject scoring to avoid stale signals boosting the wrong label.
    scoring_subject = "" if re.match(r'^(?:re|fwd?)\s*:', subject_lower) else subject_lower

    offer_hits, offer_score = _scan(scoring_subject, body_clean, OFFER_PHRASES)
    interview_hits, interview_score = _scan(scoring_subject, body_clean, INTERVIEW_PHRASES)

    # Rejection: exact phrase scan + regex pattern scan (merged, deduped)
    rejection_hits, rejection_score = _scan(scoring_subject, body_clean, REJECTION_PHRASES)
    regex_hits, regex_score = _scan_regex(body_clean, REJECTION_PATTERNS)
    rejection_score += regex_score
    rejection_hits = list(dict.fromkeys(rejection_hits + regex_hits))

    # Strong-rejection check: unambiguous patterns that trigger an override in the classifier
    strong_rejection_hits = [m.group(0) for pat in STRONG_REJECTION_PATTERNS
                             if (m := pat.search(body_clean))]

    confirmation_hits, confirmation_score = _scan(scoring_subject, body_clean, CONFIRMATION_PHRASES)
    update_hits, update_score = _scan(scoring_subject, body_clean, UPDATE_PHRASES)

    if is_ats_sender:
        confirmation_score *= ATS_CONFIRMATION_MULT

    return EmailSignals(
        subject_lower=subject_lower,
        body_clean=body_clean,
        sender_domain=sender_domain,
        is_ats_sender=is_ats_sender,
        offer_hits=offer_hits,
        interview_hits=interview_hits,
        rejection_hits=rejection_hits,
        confirmation_hits=confirmation_hits,
        update_hits=update_hits,
        offer_score=offer_score,
        interview_score=interview_score,
        rejection_score=rejection_score,
        confirmation_score=confirmation_score,
        update_score=update_score,
        strong_rejection_hits=strong_rejection_hits,
    )
