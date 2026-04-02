"""28 pytest cases for the email_classifier package. Zero Streamlit/Gmail deps."""
import pytest
from email_classifier import classify_email


ATS_SENDER = "noreply@greenhouse.io"
PLAIN_SENDER = "recruiting@acme.com"


# ── Offer ──────────────────────────────────────────────────────────────────────

def test_01_offer_subject():
    result = classify_email(
        subject="We're pleased to offer you the Software Engineer position",
        body="Congratulations! We are excited to welcome you to the team.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "offer"


def test_02_offer_body_letter():
    result = classify_email(
        subject="Next steps",
        body="Please review the attached offer letter and sign by Friday.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "offer"


def test_03_offer_body_compensation():
    result = classify_email(
        subject="Your offer",
        body="We are extending an offer. Your compensation package includes base salary and equity.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "offer"


# ── Interview ─────────────────────────────────────────────────────────────────

def test_04_interview_subject_and_body():
    result = classify_email(
        subject="Interview invitation — Software Engineer",
        body="We'd like to invite you to interview with our team next week.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "interview"


def test_05_interview_phone_screen_ats():
    result = classify_email(
        subject="Next steps with Acme",
        body="We'd like to schedule a phone screen with you. Please use the link below.",
        sender=ATS_SENDER,
    )
    assert result.label == "interview"


def test_06_interview_technical_assessment():
    result = classify_email(
        subject="Technical Assessment — Backend Engineer",
        body="Please complete the attached technical assessment within 72 hours.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "interview"


def test_07_interview_take_home():
    result = classify_email(
        subject="Your application update",
        body="We'd like to send you a take-home assignment as the next step in our process.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "interview"


# ── Rejection ─────────────────────────────────────────────────────────────────

def test_08_rejection_regret():
    result = classify_email(
        subject="Your application",
        body="We regret to inform you we will not be moving forward with your application.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


def test_09_rejection_other_candidates():
    result = classify_email(
        subject="Update on your application",
        body="After careful consideration, we have decided to move forward with other candidates.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


def test_10_rejection_not_selected():
    result = classify_email(
        subject="Application Update",
        body="Unfortunately, you were not selected for this position. We appreciate your interest.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


def test_11_rejection_position_filled():
    result = classify_email(
        subject="Regarding your application",
        body="We wanted to let you know that the position has been filled. Thank you for applying.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


# ── Confirmation ──────────────────────────────────────────────────────────────

def test_12_confirmation_ats_subject():
    result = classify_email(
        subject="Thank you for applying to Acme",
        body="We have received your application and will be in touch.",
        sender=ATS_SENDER,
    )
    assert result.label == "confirmation"


def test_13_confirmation_application_received_subject():
    result = classify_email(
        subject="Application received — Software Engineer",
        body="Thanks for submitting your application. Our team will review it shortly.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "confirmation"


def test_14_confirmation_body_only():
    result = classify_email(
        subject="Thanks!",
        body="We have received your application for the Software Engineer role.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "confirmation"


# ── Precedence ────────────────────────────────────────────────────────────────

def test_15_precedence_rejection_beats_confirmation():
    result = classify_email(
        subject="Thank you for applying",
        body="Thank you for your application. Regret to inform you we will not be moving forward.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


def test_16_precedence_offer_beats_interview():
    result = classify_email(
        subject="Next steps",
        body="We'd like to discuss next steps and share your offer letter. Congrats!",
        sender=PLAIN_SENDER,
    )
    assert result.label == "offer"


def test_17_precedence_interview_beats_confirmation():
    result = classify_email(
        subject="Update on your application",
        body="We have received your application and would like to schedule an interview.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "interview"


def test_18_precedence_rejection_beats_update():
    result = classify_email(
        subject="An update on your application",
        body="We have an update on your application. We have decided not to move forward.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


# ── Conditional stripping ─────────────────────────────────────────────────────

def test_19_conditional_strip_not_selected():
    result = classify_email(
        subject="Thank you for applying",
        body=(
            "Thank you for applying to the Software Engineer role. "
            "If you are not selected, we will notify you within two weeks. "
            "We have received your application and will be reviewing it shortly."
        ),
        sender=ATS_SENDER,
    )
    assert result.label == "confirmation"


def test_20_conditional_strip_not_moving():
    result = classify_email(
        subject="Application received",
        body=(
            "We've received your application! "
            "If you are not moving forward you will hear from us. "
            "Thank you for your interest."
        ),
        sender=PLAIN_SENDER,
    )
    assert result.label == "confirmation"


def test_21_unconditional_not_moving_is_rejection():
    result = classify_email(
        subject="Your application status",
        body="After review, we will not be moving forward with your application at this time.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_22_empty_email():
    result = classify_email(subject="", body="", sender="")
    assert result.label == "unknown"


def test_23_generic_non_job_email():
    result = classify_email(
        subject="Your account has been created",
        body="Welcome! Your account is ready. Click here to log in.",
        sender="noreply@someservice.com",
    )
    assert result.label == "unknown"


def test_24_update_label():
    result = classify_email(
        subject="Update on your application",
        body="We wanted to provide an update on your application — we are still reviewing your materials.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "update"


def test_25_unfortunately_alone_does_not_reject():
    """'unfortunately' alone (weight 3) is below MIN_REJECTION_SCORE=5, so unknown."""
    result = classify_email(
        subject="Service notification",
        body="Unfortunately we can't process your request at this time.",
        sender="noreply@someservice.com",
    )
    assert result.label in ("unknown", "rejection")
    # If it resolves to rejection, confidence must be low
    if result.label == "rejection":
        assert result.confidence < 0.40


# ── Legacy status ─────────────────────────────────────────────────────────────

def test_26_legacy_status_rejection():
    result = classify_email(
        subject="Your application",
        body="We regret to inform you that we have decided not to move forward with your candidacy.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"
    assert result.legacy_status == "Rejected"


def test_27_legacy_status_unknown_is_none():
    result = classify_email(subject="", body="", sender="")
    assert result.legacy_status is None


# ── Evidence ──────────────────────────────────────────────────────────────────

def test_28_evidence_populated_on_confirmation():
    result = classify_email(
        subject="Thank you for applying",
        body="We have received your application for the role.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "confirmation"
    assert len(result.evidence) > 0


# ── Regression: real-world misclassification bugs ─────────────────────────────

_OKTA_SUBJECT = "Your Okta Application - Staff Technical Program Manager"
_OKTA_BODY = (
    "Thank you for your interest in Okta and for taking the time to apply "
    "for the Staff Technical Program Manager position.\n\n"
    "After reviewing your application, we have decided to move forward with "
    "another candidate at this time.\n\n"
    "We appreciate the time you invested and encourage you to check out "
    "future opportunities at Okta."
)

_DUOLINGO_SUBJECT = "Duolingo Application Update"
_DUOLINGO_BODY = (
    "Hi,\n\n"
    "Thanks for your interest in joining Duolingo and applying for the "
    "Software Engineer role.\n\n"
    "At this time, we won't be moving forward with your application.\n\n"
    "We appreciate the time you took to apply and wish you the best in your search."
)


def test_29_regression_okta_another_candidate_is_rejection():
    """'move forward with another candidate' must not classify as confirmation.

    Root cause: phrase gap — all prior rejection phrases used 'other candidates',
    never 'another candidate'.
    """
    result = classify_email(
        subject=_OKTA_SUBJECT,
        body=_OKTA_BODY,
        sender="recruiting@okta.com",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. "
        f"score_breakdown={result.score_breakdown}"
    )
    assert result.legacy_status == "Rejected"
    # Rejection phrase should be in evidence
    assert any("another candidate" in p for p in result.evidence), (
        f"Expected 'another candidate' phrase in evidence, got: {result.evidence}"
    )


def test_30_regression_duolingo_wont_move_forward_is_rejection():
    """'won't be moving forward with your application' must classify as rejection
    even when polite boilerplate ('thanks for your interest') is also present.
    """
    result = classify_email(
        subject=_DUOLINGO_SUBJECT,
        body=_DUOLINGO_BODY,
        sender="recruiting@duolingo.com",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. "
        f"score_breakdown={result.score_breakdown}"
    )
    assert result.legacy_status == "Rejected"


def test_31_regression_okta_score_breakdown_populated():
    """score_breakdown must include raw scores for all five labels."""
    result = classify_email(subject=_OKTA_SUBJECT, body=_OKTA_BODY, sender="recruiting@okta.com")
    assert set(result.score_breakdown.keys()) == {"offer", "interview", "rejection", "confirmation", "update"}
    assert result.score_breakdown["rejection"] > result.score_breakdown["confirmation"]


def test_32_regression_explanation_non_empty():
    """explanation must be a non-empty string for any classified email."""
    result = classify_email(subject=_OKTA_SUBJECT, body=_OKTA_BODY, sender="recruiting@okta.com")
    assert isinstance(result.explanation, str)
    assert len(result.explanation) > 0
    assert "rejection" in result.explanation


def test_33_matched_phrases_alias_evidence():
    """matched_phrases property must return the same list as evidence."""
    result = classify_email(subject=_OKTA_SUBJECT, body=_OKTA_BODY, sender="recruiting@okta.com")
    assert result.matched_phrases is result.evidence


def test_34_thank_you_for_interest_alone_not_confirmation():
    """'thank you for your interest' alone (weight 2) must not trigger confirmation.

    Root cause: weight was 4, which alone exceeded MIN_SCORE_THRESHOLD=3.0.
    """
    result = classify_email(
        subject="",
        body="Thank you for your interest.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "unknown"


# ── Regression: regex-pattern false negatives ────────────────────────────────

_ADOBE_SUBJECT = "Thank you for applying to Adobe"
_ADOBE_BODY = (
    "Thank you for applying to Adobe.\n\n"
    "After careful consideration, we are no longer moving forward with "
    "hiring for this position.\n\n"
    "We appreciate your interest and encourage you to apply for future "
    "openings.\n\nAdobe Recruiting Team"
)

_AMAZON_SUBJECT = "Amazon application: Status update"
_AMAZON_BODY = (
    "Hi,\n\n"
    "Thank you for your application to Amazon.\n\n"
    "After reviewing all applications, we have decided to progress with "
    "other candidates for this role.\n\n"
    "We appreciate the time you took to apply.\n\nAmazon Talent Acquisition"
)


def test_35_regression_adobe_no_longer_moving_forward():
    """'no longer moving forward with hiring' must classify as rejection.

    Root cause: phrase gap — no exact phrase covered this variant.
    Even though confirmation_score is very high (ATS sender + subject + body
    all firing 'thank you for applying'), the strong-rejection override must win.
    """
    result = classify_email(
        subject=_ADOBE_SUBJECT,
        body=_ADOBE_BODY,
        sender="adobe@myworkday.com",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. "
        f"score_breakdown={result.score_breakdown}"
    )
    assert result.legacy_status == "Rejected"
    assert any("no longer moving forward" in p for p in result.evidence), (
        f"Expected 'no longer moving forward' in evidence, got: {result.evidence}"
    )


def test_36_regression_amazon_progress_with_other_candidates():
    """'decided to progress with other candidates' must classify as rejection.

    Root cause: 'progress with other candidates' not in any phrase list;
    only 'other candidates' (weight 5) fired, failing the 0.75 ratio check
    against confirmation_score=9.0 (needed ≥6.75, got 5.0).
    """
    result = classify_email(
        subject=_AMAZON_SUBJECT,
        body=_AMAZON_BODY,
        sender="recruiting@amazon.com",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. "
        f"score_breakdown={result.score_breakdown}"
    )
    assert result.legacy_status == "Rejected"
    assert any("progress" in p and "candidates" in p for p in result.evidence), (
        f"Expected 'progress with other candidates' phrase in evidence, got: {result.evidence}"
    )


def test_37_regression_adobe_score_breakdown():
    """Rejection score must exceed confirmation score for Adobe email."""
    result = classify_email(subject=_ADOBE_SUBJECT, body=_ADOBE_BODY, sender="adobe@myworkday.com")
    assert result.score_breakdown["rejection"] > 0
    assert result.score_breakdown["rejection"] > result.score_breakdown["confirmation"] * 0.5


def test_38_no_longer_moving_forward_strong_override():
    """Strong-rejection pattern overrides even a perfect confirmation subject+body."""
    result = classify_email(
        subject="Thank you for your application",
        body=(
            "Thank you for applying. We have received your application. "
            "Unfortunately, we are no longer moving forward with hiring for this role."
        ),
        sender=ATS_SENDER,
    )
    assert result.label == "rejection"


def test_39_progressing_with_other_candidates_variant():
    """'progressing with other candidates' (gerund form) must classify as rejection."""
    result = classify_email(
        subject="Update on your application",
        body="After careful review, we will be progressing with other candidates at this time.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


def test_40_curly_apostrophe_normalization():
    """Curly apostrophe in 'won\u2019t be moving' must still fire rejection pattern."""
    result = classify_email(
        subject="Application update",
        body="We\u2019re sorry to inform you that we won\u2019t be moving forward with your application.",
        sender=PLAIN_SENDER,
    )
    assert result.label == "rejection"


def test_41_regression_veeva_decision_to_not_move_forward():
    """Split-infinitive 'decision to not move forward' (Veeva/Lime) must be rejection."""
    result = classify_email(
        subject="Thank you for your interest in Veeva",
        body=(
            "Hi Hiral,\n\nThank you for your interest in Veeva and for giving us the opportunity "
            "to review your application for the Product Manager - Vault CRM Suite position.\n\n"
            "After reviewing your resume, we've made the decision to not move forward at this time.\n\n"
            "We appreciate your interest in Veeva and wish you success in your job search.\n\n"
            "Kind regards,\nVeeva Talent Attraction Team"
        ),
        sender="no-reply@hire.lever.co",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


def test_42_regression_rho_decided_to_move_forward_with_candidates():
    """'decided to move forward with candidates whose experience' (Rho/Klaviyo) must be rejection."""
    result = classify_email(
        subject="Thank you for your interest in Rho",
        body=(
            "Hi Hiral,\n\nThank you for your interest in the Product Manager position at Rho.\n\n"
            "Unfortunately, we have decided to move forward with candidates whose experience more "
            "closely aligns with our team's current needs. While we won't be proceeding with your "
            "candidacy at this time, we value the time and effort you invested in applying.\n\n"
            "Rho Talent Acquisition Team"
        ),
        sender="no-reply@ashbyhq.com",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


def test_43_regression_gemini_concluding_search():
    """'found a finalist / concluding our search' (Gemini) must be rejection."""
    result = classify_email(
        subject="Gemini | Application Update",
        body=(
            "Hi Hiral,\n\nThanks for applying to the Product Management Intern (Summer 2025) role "
            "at Gemini. We really appreciate that you took the time to consider us.\n\n"
            "While impressed with your experience, we have recently found a finalist for this "
            "position and are concluding our search at this time.\n\n"
            "Thank you again for your interest in Gemini. We wish you the best of luck.\n\nBest,\n"
            "Grace Reginato\nTalent Acquisition Associate"
        ),
        sender="careers@gemini.com",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


def test_44_regression_spotify_not_selected_next_round():
    """'resume was not selected for the next round' (Spotify) must be rejection."""
    result = classify_email(
        subject="Thanks for your interest in Spotify, Hiral",
        body=(
            "Hi Hiral,\n\nThank you for applying for the Fullstack/Backend Engineer position, "
            "and for considering an opportunity with Spotify.\n\n"
            "At this time and for this specific gig, your resume was not selected for the next round. "
            "We're fortunate to receive a huge number of competitive applications.\n\n"
            "Warm wishes,\nThe Spotify Recruiting Team"
        ),
        sender="no-reply@hire.lever.co",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


def test_45_regression_televisaunivision_no_longer_accepting_candidates():
    """'no longer accepting candidates' (TelevisaUnivision) must be rejection."""
    result = classify_email(
        subject="Thank you for your interest",
        body=(
            "Dear HIRAL Thank you for your interest in employment with TelevisaUnivision. "
            "We appreciate the time that you have invested in your job search for the position of: "
            "Intern, Strategy & Insights. This position is no longer accepting candidates but we "
            "thank you for your interest in TelevisaUnivision. We wish you continued success.\n\n"
            "Sincerely,\nTelevisaUnivision Talent Acquisition Team"
        ),
        sender="univision@myworkday.com",
    )
    assert result.label == "rejection", (
        f"Expected rejection, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


# ---------------------------------------------------------------------------
# Regression: "next steps" in informational context must not trigger interview
# ---------------------------------------------------------------------------

def test_46_regression_zip_next_steps_in_confirmation_not_interview():
    """Confirmation email mentioning 'next steps' informally (Zip/Ashby) must stay confirmation.

    Root cause: 'next steps' weight=5 exceeded MIN_SCORE_THRESHOLD=3.0 alone, causing
    interview to beat confirmation via Rule 3. Fix: lower 'next steps' to weight 2.
    """
    result = classify_email(
        subject="Thanks for applying to Zip",
        body=(
            "Hi Hiral,\n\nThanks for your interest and for applying to our Product Manager, "
            "Intake and Collaboration position! Your application has been received and will be "
            "reviewed by our team.\n\nIf you are a top candidate for the role, you will receive "
            "a message regarding next steps. If you are not selected, please continue to view "
            "our careers page for roles that may be a better match in the future.\n\n"
            "We realize it takes time and effort to apply and we appreciate your time.\n\n"
            "Regards,\nZip"
        ),
        sender="no-reply@ashbyhq.com",
    )
    assert result.label == "confirmation", (
        f"Expected confirmation, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


def test_47_regression_doordash_next_steps_in_confirmation_not_interview():
    """DoorDash confirmation email with 'coordinate next steps' must not classify as interview."""
    result = classify_email(
        subject="Thank you for applying to DoorDash",
        body=(
            "Hi Hiral,\n\nThank you for applying to DoorDash's Associate Manager, QA Strategy "
            "& Operations position! We've received your application and will review it as soon "
            "as possible! If your background looks like a good match, we'll reach out to "
            "coordinate next steps.\n\nIn the meantime, feel free to download the DoorDash app."
        ),
        sender="no-reply@doordash.com",
    )
    assert result.label == "confirmation", (
        f"Expected confirmation, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


def test_48_regression_amazon_next_steps_in_confirmation_not_interview():
    """Amazon confirmation email with 'discuss next steps' must not classify as interview."""
    result = classify_email(
        subject="Thanks for applying to Amazon",
        body=(
            "Hi HIRAL,\n\nThanks for applying to Amazon! We've received your application for "
            "the Sr Product Manager, Tech - Ad Products, Prime Video Advertising (ID: 3204873) "
            "position.\n\nWhat happens next? If we decide to move forward with your application, "
            "the Amazon recruiting team will reach out to discuss next steps. Any updates to "
            "your application status will be reflected on your Application dashboard.\n\n"
            "Best regards,\nAmazon Recruiting Team"
        ),
        sender="amazon@myworkday.com",
    )
    assert result.label == "confirmation", (
        f"Expected confirmation, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )


def test_49_next_steps_still_contributes_to_real_interview():
    """'next steps' at weight 2 still boosts genuine interview emails with other signals."""
    result = classify_email(
        subject="Interview invitation — next steps",
        body=(
            "Hi Hiral,\n\nWe'd love to schedule an interview with you to discuss the role "
            "and next steps. Please use the link below to book a time.\n\n"
            "Looking forward to speaking with you!"
        ),
        sender="recruiting@acme.com",
    )
    assert result.label == "interview", (
        f"Expected interview, got {result.label!r}. score_breakdown={result.score_breakdown}"
    )
