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


# ---------------------------------------------------------------------------
# iCIMS sub-address tag + company-slug-in-local matching (GitHub regression)
# ---------------------------------------------------------------------------

def test_github_icims_with_subaddress_tag_not_microsoft():
    """githubinc+autoreply@talent.icims.com must resolve to GitHub, not Microsoft.

    Root cause: local part "githubinc+autoreply" didn't exact-match slug "github",
    so the body scan ran and returned "Microsoft" (longer name appearing in body).
    Fix: strip +tag, then check slug-in-local substring match.
    """
    companies = _make_companies("GitHub", "Microsoft")
    body = (
        "Thank you for including GitHub in your job search. "
        "In this role you will influence partners across GitHub and Microsoft."
    )
    result = _match_company(
        sender="githubinc+autoreply@talent.icims.com",
        subject="Thank You For Your Application",
        companies=companies,
        body=body,
    )
    assert result == "GitHub", f"Expected 'GitHub', got {result!r}"


def test_ats_local_slug_embedded_in_local_part():
    """AdobeJobs@lever.co-style: slug 'adobe' ⊆ local 'adobejobs' must match Adobe."""
    companies = _make_companies("Adobe")
    result = _match_company(
        sender="adobejobs@hire.lever.co",
        subject="Application received",
        companies=companies,
        body="",
    )
    assert result == "Adobe", f"Expected 'Adobe', got {result!r}"


def test_ats_subaddress_tag_stripped_before_slug_match():
    """noreply+confirmation@myworkday.com: stripping tag gives 'noreply', no match."""
    companies = _make_companies("Stripe")
    result = _match_company(
        sender="noreply+confirmation@myworkday.com",
        subject="Application received",
        companies=companies,
        body="",
    )
    # 'noreply' is not a company slug for Stripe — should fall through to body/domain
    assert result is None, f"Expected None, got {result!r}"


# ---------------------------------------------------------------------------
# Social-media-context skip in ATS body scan (Flexport / LinkedIn regression)
# ---------------------------------------------------------------------------

def test_linkedin_in_on_context_not_matched():
    """'LinkedIn' in 'Flexport on LinkedIn' footer must not be returned.

    Root cause: ATS body scan found 'LinkedIn' (tracked) in a social-media footer
    link 'Flexport on LinkedIn', returning 'LinkedIn' instead of None.
    Fix: skip body-scan matches where the 15-char window before the match
    ends with 'on ' or 'via '.
    """
    companies = _make_companies("LinkedIn")   # only LinkedIn tracked; Flexport is not
    body = (
        "Thank you for applying to the Senior Product Manager role.\n"
        "Stay connected: Flexport on LinkedIn | flexport.com/blog"
    )
    result = _match_company(
        sender="no-reply@us.greenhouse-mail.io",
        subject="Application for Senior Product Manager received",
        companies=companies,
        body=body,
    )
    assert result is None, f"Expected None (social footer), got {result!r}"


def test_via_context_also_skipped():
    """'Indeed' in 'apply via Indeed' must also be skipped."""
    companies = _make_companies("Indeed")
    body = "You can apply via Indeed or directly at our careers page."
    result = _match_company(
        sender="no-reply@greenhouse.io",
        subject="Application received",
        companies=companies,
        body=body,
    )
    assert result is None, f"Expected None (via-context), got {result!r}"


def test_company_legitimately_in_body_not_skipped():
    """Company name in normal body context must still be returned."""
    companies = _make_companies("Flexport")
    body = (
        "Thank you for your interest in Flexport. We appreciate your application "
        "and will be in touch shortly. Follow us on LinkedIn."
    )
    result = _match_company(
        sender="no-reply@greenhouse.io",
        subject="Application received",
        companies=companies,
        body=body,
    )
    assert result == "Flexport", f"Expected 'Flexport', got {result!r}"


# ---------------------------------------------------------------------------
# Single-word company name + company-indicator post-word (Listen Labs regression)
# ---------------------------------------------------------------------------

def test_single_word_not_matched_when_followed_by_indicator_word():
    """'Listen' must not be matched when body contains 'at Listen Labs'.

    Root cause: single-word name 'Listen' word-boundary matched in 'at Listen Labs',
    returning 'Listen' instead of None (since 'Listen Labs' is not tracked).
    Fix: skip match when a company-indicator word (e.g. 'Labs') immediately follows.
    """
    companies = _make_companies("Listen")   # 'Listen Labs' is NOT tracked
    body = (
        "Thank you for applying to the Insight Strategist role at Listen Labs. "
        "We appreciate your interest in joining our team."
    )
    result = _match_company(
        sender="no-reply@greenhouse.io",
        subject="Thank you for your application",
        companies=companies,
        body=body,
    )
    assert result is None, f"Expected None ('Listen Labs' not tracked), got {result!r}"


def test_single_word_matched_when_no_indicator_word_follows():
    """'Listen' IS returned when it appears alone without a following indicator word."""
    companies = _make_companies("Listen")
    body = "Thank you for applying to Listen. We will review your application."
    result = _match_company(
        sender="no-reply@greenhouse.io",
        subject="Application received",
        companies=companies,
        body=body,
    )
    assert result == "Listen", f"Expected 'Listen', got {result!r}"


def test_indicator_word_check_also_in_subject_scan():
    """Subject scan must also skip 'Listen' when followed by 'Labs' in subject."""
    companies = _make_companies("Listen")
    result = _match_company(
        sender="jobs@stripe.com",   # non-ATS so subject scan runs
        subject="Thank you for applying to Listen Labs",
        companies=companies,
        body="",
    )
    assert result is None, f"Expected None (subject: 'Listen Labs'), got {result!r}"


# ---------------------------------------------------------------------------
# Possessive-reference skip in ATS body scan (XBOW / Microsoft regression)
# ---------------------------------------------------------------------------

def test_possessive_reference_not_matched_as_hiring_company():
    """'Microsoft's infrastructure' must not return Microsoft as the hiring company.

    Root cause: ATS body scan matched 'Microsoft' in the possessive phrase
    'Microsoft's infrastructure', returning 'Microsoft' instead of None.
    Fix: skip body-scan match when immediately followed by an apostrophe.
    """
    companies = _make_companies("Microsoft")   # XBOW not tracked
    body = (
        "Thank you for applying for the Product Manager role at XBOW!\n"
        "We were recently credited with finding the most critical bug in "
        "Microsoft\u2019s infrastructure.\n"
        "XBOW Hiring Team"
    )
    result = _match_company(
        sender="no-reply@ashbyhq.com",
        subject="Thanks for applying to XBOW!",
        companies=companies,
        body=body,
    )
    assert result is None, f"Expected None (possessive reference), got {result!r}"


def test_company_matched_legitimately_in_body():
    """Non-possessive company name in body IS still matched."""
    companies = _make_companies("Microsoft")
    body = (
        "Thank you for applying to Microsoft. "
        "Microsoft\u2019s team will review your application."
    )
    result = _match_company(
        sender="no-reply@ashbyhq.com",
        subject="Application received",
        companies=companies,
        body=body,
    )
    assert result == "Microsoft", f"Expected 'Microsoft', got {result!r}"


# ---------------------------------------------------------------------------
# "institute" in _COMPANY_NAME_INDICATORS (Expert Institute regression)
# ---------------------------------------------------------------------------

def test_single_word_not_matched_before_institute():
    """'Expert' must not be matched when subject contains 'Expert Institute'.

    Root cause: 'institute' was missing from _COMPANY_NAME_INDICATORS, so
    'Expert' was returned instead of None for an email from Expert Institute.
    Fix: add 'institute' to _COMPANY_NAME_INDICATORS.
    """
    companies = _make_companies("Expert")   # "Expert Institute" is NOT tracked
    result = _match_company(
        sender="notification@careers.expertinstitute.com",
        subject="Thank you for your application to Expert Institute!",
        companies=companies,
        body="",
    )
    assert result is None, f"Expected None ('Expert Institute' not tracked), got {result!r}"


# ---------------------------------------------------------------------------
# Listen Labs (ATS subject + body) — confirms existing indicator fix holds
# ---------------------------------------------------------------------------

def test_listen_labs_ats_subject_and_body():
    """'Listen' not matched when ATS subject + body both contain 'Listen Labs'."""
    companies = _make_companies("Listen")
    body = (
        "Thank you for applying to the Insight Strategist role at Listen Labs.\n"
        "The Listen Labs Recruiting Team"
    )
    result = _match_company(
        sender="no-reply@ashbyhq.com",
        subject="Thanks for applying to Listen Labs!",
        companies=companies,
        body=body,
    )
    assert result is None, f"Expected None ('Listen Labs' not tracked), got {result!r}"
