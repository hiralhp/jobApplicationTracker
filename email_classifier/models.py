"""Data models for email classification."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class EmailSignals:
    subject_lower: str
    body_clean: str
    sender_domain: str
    is_ats_sender: bool
    offer_hits: list[str] = field(default_factory=list)
    interview_hits: list[str] = field(default_factory=list)
    rejection_hits: list[str] = field(default_factory=list)
    confirmation_hits: list[str] = field(default_factory=list)
    update_hits: list[str] = field(default_factory=list)
    offer_score: float = 0.0
    interview_score: float = 0.0
    rejection_score: float = 0.0
    confirmation_score: float = 0.0
    update_score: float = 0.0
    strong_rejection_hits: list[str] = field(default_factory=list)


@dataclass
class ClassificationResult:
    label: str           # "confirmation"|"rejection"|"interview"|"offer"|"update"|"unknown"
    confidence: float    # 0.0–1.0
    evidence: list[str] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)   # {label: raw_score} for all labels
    explanation: str = ""

    @property
    def matched_phrases(self) -> list[str]:
        """Alias for evidence — the matched phrases that drove the winning label."""
        return self.evidence

    @property
    def legacy_status(self) -> Optional[str]:
        return {
            "confirmation": "Applied",
            "rejection": "Rejected",
            "interview": "Interviewing",
            "offer": "Offer",
        }.get(self.label)
