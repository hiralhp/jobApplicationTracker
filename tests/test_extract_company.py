"""Regression tests for _extract_company_from_subject()."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app import _extract_company_from_subject


# ---------------------------------------------------------------------------
# Location stripping — "at/applying to Company in City, ST"
# ---------------------------------------------------------------------------

def test_at_company_strips_city_location():
    """'at Predactiv in Palo Alto, CA' must return 'Predactiv', not 'Predactiv in Palo Alto'."""
    result = _extract_company_from_subject(
        "Job Application Confirmation: Product Manager at Predactiv in Palo Alto, CA"
    )
    assert result == "Predactiv", f"Expected 'Predactiv', got {result!r}"


def test_applying_to_company_strips_city_location():
    """'applying to Acme in San Francisco, CA' must return 'Acme'."""
    result = _extract_company_from_subject(
        "Thank you for applying to Acme in San Francisco, CA"
    )
    assert result == "Acme", f"Expected 'Acme', got {result!r}"


# ---------------------------------------------------------------------------
# Case-insensitive keyword matching — "Applying" (capital A) must be found
# ---------------------------------------------------------------------------

def test_applying_uppercase_keyword():
    """'Thank you for Applying to Tableau!' must extract 'Tableau'."""
    result = _extract_company_from_subject("Thank you for Applying to Tableau!")
    assert result == "Tableau", f"Expected 'Tableau', got {result!r}"


def test_applying_lowercase_keyword_still_works():
    """Lowercase 'applying to Stripe!' must still extract 'Stripe'."""
    result = _extract_company_from_subject("Thank you for applying to Stripe!")
    assert result == "Stripe", f"Expected 'Stripe', got {result!r}"


def test_application_mixed_case_keyword():
    """'Application to Acme' (capitalised noun) must extract 'Acme'."""
    result = _extract_company_from_subject("Application to Acme has been received")
    assert result == "Acme", f"Expected 'Acme', got {result!r}"


# ---------------------------------------------------------------------------
# Body-line scan — trailing phrase must not bleed into the company name
# ---------------------------------------------------------------------------

def test_at_company_body_line_does_not_include_trailing_phrase():
    """'at Tableau right away,' must not return 'Tableau right away'."""
    result = _extract_company_from_subject(
        "While not every applicant will find a home at Tableau right away, we appreciate every one"
    )
    assert result != "Tableau right away", (
        f"Trailing phrase leaked into company name: got {result!r}"
    )


# ---------------------------------------------------------------------------
# Existing behaviour must be preserved
# ---------------------------------------------------------------------------

def test_at_company_simple():
    """Plain 'at Stripe' without a location must still extract 'Stripe'."""
    result = _extract_company_from_subject(
        "Thank you for applying to the Software Engineer role at Stripe"
    )
    assert result == "Stripe", f"Expected 'Stripe', got {result!r}"


def test_at_company_with_we():
    """'at Acme we believe' — lookahead on 'we ' must extract 'Acme'."""
    result = _extract_company_from_subject("At Acme we believe in great software")
    assert result == "Acme", f"Expected 'Acme', got {result!r}"


def test_unknown_subject_returns_none():
    """Generic subject with no company cues must return None."""
    result = _extract_company_from_subject("Your application has been received")
    assert result is None, f"Expected None, got {result!r}"


# ---------------------------------------------------------------------------
# "by Team Company" pattern (Greenhouse-style subjects)
# ---------------------------------------------------------------------------

def test_by_team_extracts_company():
    """'received by Team Flexport!' must extract 'Flexport'."""
    result = _extract_company_from_subject(
        "Application for Senior Product Manager received by Team Flexport!"
    )
    assert result == "Flexport", f"Expected 'Flexport', got {result!r}"


def test_by_team_case_insensitive_keyword():
    """'by team' keyword must be matched case-insensitively."""
    result = _extract_company_from_subject(
        "Application received By Team Stripe"
    )
    assert result == "Stripe", f"Expected 'Stripe', got {result!r}"
