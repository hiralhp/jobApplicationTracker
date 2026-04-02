"""All phrase lists, weights, and configuration constants for email classification."""
import re as _re

ATS_DOMAINS = [
    "lever.co", "greenhouse.io", "greenhouse-mail.io",
    "ashbyhq.com", "workday.com", "smartrecruiters.com",
    "icims.com", "jobvite.com", "taleo.net", "successfactors.com",
    "bamboohr.com", "recruitee.com", "breezy.hr", "workable.com",
    "myworkday.com", "dayforce.com", "adp.com",
]

OFFER_PHRASES = [
    ("offer letter", 10), ("pleased to offer", 10), ("extend an offer", 10),
    ("offer of employment", 10), ("employment offer", 10), ("job offer", 9),
    ("formal offer", 9), ("we are offering you", 9), ("sign your offer", 9),
    ("compensation package", 6), ("start date", 4),
]

INTERVIEW_PHRASES = [
    ("invite you to interview", 9), ("schedule an interview", 9),
    ("we'd like to move forward", 8), ("move forward with your application", 8),
    ("phone screen", 8), ("technical assessment", 8), ("take-home assignment", 8),
    ("coding challenge", 8), ("virtual interview", 8), ("video interview", 8),
    ("on-site interview", 9), ("interview loop", 9), ("final round", 7),
    ("schedule a call", 7), ("next steps", 2), ("take-home", 5),
    ("speak with you", 4), ("hiring manager", 4), ("interview", 4),
]

REJECTION_PHRASES = [
    ("regret to inform you", 10), ("regret to let you know", 10),
    ("decided not to move forward", 10), ("decision not to move forward", 10),
    ("decision to not move forward", 10),  # split-infinitive variant (e.g. Veeva)
    ("not moving forward with your", 10), ("will not be moving forward", 10),
    ("decided to proceed with other candidates", 10),
    ("decided to move forward with other", 10),
    ("chosen to move forward with other", 10),
    ("moving forward with other candidates", 10),
    ("move forward with other applicants", 10),
    # "another candidate" variants — distinct from "other candidates" above
    ("move forward with another candidate", 10),
    ("decided to move forward with another candidate", 10),
    ("moving forward with another candidate", 10),
    ("chosen to move forward with another", 10),
    ("decided to proceed with another candidate", 10),
    # "unable to move forward" variants
    ("we are unable to move forward", 9),
    ("unable to move forward with your", 9),
    ("we're unable to move forward", 9),
    ("not able to move forward", 9),
    ("pursue other candidates", 10), ("pursuing other candidates", 10),
    ("not selected for this position", 10), ("not selected for this role", 10),
    ("not selected for an interview", 10), ("not a fit for this role", 10),
    ("not a fit for this position", 10), ("have decided not to", 9),
    ("chosen not to move forward", 10), ("not be moving forward with your candidacy", 10),
    ("not moving forward", 8), ("will not be moving", 9), ("won't be moving", 9),
    ("decided not to proceed", 9), ("position has been filled", 8),
    ("we have filled this position", 8), ("no longer accepting applications", 7),
    ("going in a different direction", 7), ("at this time we are unable", 7),
    ("not selected", 7), ("other candidates", 5), ("unfortunately", 3),
    ("not selected for the next round", 10),  # Spotify: "your resume was not selected for the next round"
    ("concluding our search", 9),             # Gemini: "are concluding our search at this time"
    ("found a finalist", 8),                  # Gemini: "found a finalist for this position"
]

CONFIRMATION_PHRASES = [
    ("thank you for applying", 9), ("thanks for applying", 9),
    ("thank you for your application", 9), ("thanks for your application", 9),
    ("application received", 9), ("application confirmation", 9),
    ("we received your application", 9), ("application submitted", 8),
    ("successfully applied", 9), ("you've applied", 8),
    ("received your application", 8), ("have received your application", 9),
    ("we have received your application", 9), ("application has been received", 9),
    ("we've received your application", 9),
    ("successfully submitted your application", 9),
    ("your application is under review", 7), ("we will review your application", 6),
    ("one of our recruiters will be in touch", 6),
    # deliberately weak — also appears in rejections (weight 2: below MIN_SCORE_THRESHOLD=3.0 alone):
    ("thank you for your interest", 2), ("thanks for your interest", 2),
]

# ---------------------------------------------------------------------------
# Regex-based rejection patterns — semantic variants not catchable by exact
# string matching. Each entry: (compiled_regex, weight).
# The actual matched text (m.group(0)) is used as evidence.
# ---------------------------------------------------------------------------
REJECTION_PATTERNS: list = [
    # "no longer moving forward" variants (covers Adobe)
    (_re.compile(r"\bno longer moving forward\b"),                                        10),
    (_re.compile(r"\bno longer (?:hiring|considering)(?: for)? this\b"),                   9),
    # "progress / proceed / continue with other candidates" (covers Amazon)
    (_re.compile(r"\bprogress(?:ing)? with other candidates\b"),                          10),
    (_re.compile(r"\bproceeding with other candidates\b"),                                10),
    (_re.compile(r"\bcontinuing? with other candidates\b"),                               10),
    # "decided to progress/proceed/continue with other/another candidate(s)"
    (_re.compile(r"\bdecided to (?:progress|proceed|continue) with (?:other|another) candidates?\b"), 10),
    # "will not / not be proceeding"
    (_re.compile(r"\bwill not be proceeding\b"),                                           9),
    (_re.compile(r"\bnot be proceeding with your\b"),                                      9),
    # "unable to move forward / proceed / extend an offer"
    (_re.compile(r"\bunable to (?:move forward|proceed|extend an offer)\b"),               9),
    # "move forward with another/other candidate(s)" — regex covers singular+plural
    (_re.compile(r"\bmove forward with (?:another|other) candidates?\b"),                 10),
    # "other candidates for this role/position"
    (_re.compile(r"\bother candidates? for this (?:role|position)\b"),                    8),
    # "unfortunately … not/unable/won't/will not" (within 80 chars)
    (_re.compile(r"\bunfortunately\b.{0,80}(?:\bnot\b|\bunable\b|\bwon.t\b|\bwill not\b)"), 7),
    # "decided to move forward with candidates [whose/who/that]…" (Rho, Klaviyo)
    (_re.compile(r"\bdecided to move forward with candidates\b"),                            10),
    # "won't be proceeding with your" (Rho)
    (_re.compile(r"\bwon't be proceeding with your\b"),                                       9),
    # "no longer accepting candidates/applications/resumes" (TelevisaUnivision)
    (_re.compile(r"\bno longer accepting (?:candidates|applications|resumes)\b"),             7),
]

# Patterns unambiguous enough to override confirmation score entirely.
# If any of these fire in body_clean, rejection wins regardless of the
# Rule-2 ratio check (but offer still beats rejection).
STRONG_REJECTION_PATTERNS: list = [
    _re.compile(r"\bno longer moving forward\b"),
    _re.compile(r"\bno longer (?:hiring|considering)(?: for)? this\b"),
    _re.compile(r"\bprogress(?:ing)? with other candidates\b"),
    _re.compile(r"\bproceeding with other candidates\b"),
    _re.compile(r"\bcontinuing? with other candidates\b"),
    _re.compile(r"\bwill not be proceeding\b"),
    _re.compile(r"\bnot be proceeding with your\b"),
    _re.compile(r"\bdecided to (?:progress|proceed|continue) with (?:other|another) candidates?\b"),
    _re.compile(r"\bdecision to not move forward\b"),         # split-infinitive variant (Veeva, Lime)
    _re.compile(r"\bdecided to move forward with candidates\b"),  # (Rho, Klaviyo)
    _re.compile(r"\bwon't be proceeding with your\b"),        # (Rho)
    _re.compile(r"\bnot selected for the next round\b"),      # (Spotify)
    _re.compile(r"\bconcluding (?:our|the) search\b"),        # (Gemini)
    _re.compile(r"\bfound a finalist\b"),                     # (Gemini)
]

UPDATE_PHRASES = [
    ("an update on your application", 8), ("update regarding your application", 8),
    ("we wanted to update you", 7), ("status of your application", 7),
    ("your application status", 6), ("checking in on your application", 6),
]

CONDITIONAL_STRIP_PATTERN = (
    r'\bif\b[^.!?\n]*'
    r'\b(?:not selected|not a match|not moving|no longer consider|do not hear)\b'
    r'[^.!?\n]*'
)

MIN_SCORE_THRESHOLD = 3.0
MIN_REJECTION_SCORE = 5.0
SUBJECT_MULTIPLIER = 1.5
ATS_CONFIRMATION_MULT = 0.85
