"""Regression tests for _match_company() — company identification from email metadata.

These tests cover ATS-sender edge cases where the email comes from a shared ATS
platform (myworkday.com, lever.co, ashbyhq.com) and the company name must be
extracted correctly without false-matching the ATS platform itself.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import _match_company


def _make_companies(*names):
    """Build minimal mock company rows: (id, name, -, -, -, -, recruiting_email, -)."""
    return [(i + 1, name, None, None, None, None, None, None) for i, name in enumerate(names)]


# ---------------------------------------------------------------------------
# ATS local-part matching (Zillow / Redfin regression)
# ---------------------------------------------------------------------------

def test_zillow_from_myworkday_matches_zillow_not_workday():
    """zillow@myworkday.com must resolve to Zillow, not Workday.

    Root cause: ATS body scan found 'Workday' in the email body before the
    local-part check ran.  Fix: match local part of ATS address first.
    """
    companies = _make_companies("Zillow", "Workday")
    body = (
        "Thank you for submitting your application for Senior Product Manager, Workday. "
        "We\u2019re thrilled that you\u2019re interested in being part of our mission "
        "to help more people get home.\n\nThank you,\nZillow Talent Acquisition"
    )
    result = _match_company(
        sender="zillow@myworkday.com",
        subject="Thank you for your application!",
        companies=companies,
        body=body,
    )
    assert result == "Zillow", f"Expected 'Zillow', got {result!r}"


def test_redfin_from_myworkday_matches_redfin_not_workday():
    """redfin@myworkday.com must resolve to Redfin, not Workday."""
    companies = _make_companies("Redfin", "Workday")
    body = (
        "Dear Hiral, Thank you for your interest in the Software Developer I position. "
        "Unfortunately, this position has been filled.\n\nSincerely,\nThe Redfin Recruiting Team"
    )
    result = _match_company(
        sender="redfin@myworkday.com",
        subject="Your application for Software Developer I",
        companies=companies,
        body=body,
    )
    assert result == "Redfin", f"Expected 'Redfin', got {result!r}"


def test_televisaunivision_body_fallback_when_local_part_is_partial():
    """univision@myworkday.com: local part 'univision' won't match 'TelevisaUnivision' slug,
    so the body scan should still find 'TelevisaUnivision' in the body text.
    """
    companies = _make_companies("TelevisaUnivision", "Workday")
    body = (
        "Dear HIRAL Thank you for your interest in employment with TelevisaUnivision. "
        "This position is no longer accepting candidates.\n\n"
        "TelevisaUnivision Talent Acquisition Team"
    )
    result = _match_company(
        sender="univision@myworkday.com",
        subject="Thank you for your interest",
        companies=companies,
        body=body,
    )
    assert result == "TelevisaUnivision", f"Expected 'TelevisaUnivision', got {result!r}"


def test_ats_local_part_match_is_case_insensitive():
    """Local part matching normalises to lowercase before slug comparison."""
    companies = _make_companies("Spotify")
    result = _match_company(
        sender="Spotify@hire.lever.co",
        subject="Thanks for your interest",
        companies=companies,
        body="Your resume was not selected for the next round.",
    )
    assert result == "Spotify", f"Expected 'Spotify', got {result!r}"


def test_ats_local_part_does_not_match_generic_noreply():
    """'no-reply' local part must not match any company (e.g. 'Noreply' doesn't exist)."""
    companies = _make_companies("Veeva", "Rho")
    body = (
        "Thank you for your interest in Veeva and for giving us the opportunity to review "
        "your application. After reviewing your resume, we've made the decision to not move "
        "forward at this time.\n\nVeeva Talent Attraction Team"
    )
    # no-reply@hire.lever.co should fall through to body scan, finding "Veeva"
    result = _match_company(
        sender="no-reply@hire.lever.co",
        subject="Thank you for your interest in Veeva",
        companies=companies,
        body=body,
    )
    assert result == "Veeva", f"Expected 'Veeva', got {result!r}"


# ---------------------------------------------------------------------------
# Sanity checks: existing behaviour must be preserved
# ---------------------------------------------------------------------------

def test_non_ats_sender_uses_domain_slug():
    """For non-ATS senders, domain slug fallback should still work."""
    companies = _make_companies("Stripe")
    result = _match_company(
        sender="jobs@stripe.com",
        subject="Application received",
        companies=companies,
        body="",
    )
    assert result == "Stripe", f"Expected 'Stripe', got {result!r}"


def test_company_name_in_subject_matched_first():
    """Company name in subject takes priority over ATS local-part matching."""
    companies = _make_companies("Acme", "Other")
    result = _match_company(
        sender="other@myworkday.com",
        subject="Thank you for applying to Acme!",
        companies=companies,
        body="",
    )
    assert result == "Acme", f"Expected 'Acme', got {result!r}"


def test_no_match_returns_none():
    """Returns None when the company is not in the DB and no fallback applies."""
    companies = _make_companies("SomeOtherCo")
    result = _match_company(
        sender="noreply@unknownats.com",
        subject="Application update",
        companies=companies,
        body="",
    )
    assert result is None, f"Expected None, got {result!r}"
