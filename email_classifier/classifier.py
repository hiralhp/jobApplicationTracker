"""Scoring, precedence rules, confidence calculation, and main classify_email()."""
from __future__ import annotations

from .config import MIN_SCORE_THRESHOLD, MIN_REJECTION_SCORE
from .extractor import extract_signals
from .llm_stub import LlmClassifier
from .models import ClassificationResult, EmailSignals


def _build_candidates(signals: EmailSignals) -> dict[str, tuple[float, list[str]]]:
    """Return {label: (raw_score, hits)} for labels meeting threshold."""
    all_scores = {
        "offer":        (signals.offer_score,        signals.offer_hits),
        "interview":    (signals.interview_score,     signals.interview_hits),
        "rejection":    (signals.rejection_score,     signals.rejection_hits),
        "confirmation": (signals.confirmation_score,  signals.confirmation_hits),
        "update":       (signals.update_score,        signals.update_hits),
    }
    candidates: dict[str, tuple[float, list[str]]] = {}
    for label, (score, hits) in all_scores.items():
        if score < MIN_SCORE_THRESHOLD:
            continue
        if label == "rejection" and score < MIN_REJECTION_SCORE:
            continue
        candidates[label] = (score, hits)
    return candidates


def _apply_precedence(
    candidates: dict[str, tuple[float, list[str]]],
    signals: EmailSignals,
) -> tuple[str, list[str]]:
    """Apply precedence rules; return (winning_label, evidence_hits)."""
    if not candidates:
        return "unknown", []

    def has(label: str) -> bool:
        return label in candidates

    def score(label: str) -> float:
        return candidates[label][0] if label in candidates else 0.0

    def hits(label: str) -> list[str]:
        return candidates[label][1] if label in candidates else []

    # Rule 1 — OFFER beats everything
    if has("offer"):
        return "offer", hits("offer")

    # Rule 0 — STRONG REJECTION override (after offer so offer still wins)
    # Unambiguous negative-outcome regex patterns bypass the ratio check in Rule 2.
    # Evidence = strong hits first, then any supporting exact-phrase hits.
    if signals.strong_rejection_hits:
        evidence = list(dict.fromkeys(signals.strong_rejection_hits + signals.rejection_hits))
        return "rejection", evidence

    # Rule 2 — REJECTION beats confirmation/interview/update
    #           but only if rejection_score >= confirmation_score * 0.75
    if has("rejection"):
        if score("rejection") >= score("confirmation") * 0.75:
            return "rejection", hits("rejection")

    # Rule 3 — INTERVIEW beats confirmation and update (not rejection/offer)
    # Guard: requires interview_score >= confirmation_score * 0.45 when confirmation is present.
    # This lets genuine interview invitations win (ratio ~0.5) while blocking boilerplate
    # confirmation phrases that mention "next steps" or "move forward" conditionally (ratio ~0.05–0.30).
    if has("interview"):
        if not has("confirmation") or score("interview") >= score("confirmation") * 0.45:
            return "interview", hits("interview")

    # Rule 4 — CONFIRMATION beats update only
    if has("confirmation"):
        return "confirmation", hits("confirmation")

    # Rule 5 — UPDATE
    if has("update"):
        return "update", hits("update")

    # Rule 6 — FALLBACK: max raw score among candidates
    best_label = max(candidates, key=lambda l: candidates[l][0])
    return best_label, hits(best_label)


def _compute_confidence(
    winner: str,
    winner_raw: float,
    candidates: dict[str, tuple[float, list[str]]],
) -> float:
    if winner == "unknown":
        return 0.0

    all_raw = {label: score for label, (score, _) in candidates.items()}
    total = sum(all_raw.values())
    normalized = winner_raw / total if total > 0 else 0.0

    raw_boost = min(0.20, winner_raw / 60.0)

    other_scores = [s for label, s in all_raw.items() if label != winner]
    second_best = max(other_scores) if other_scores else 0.0
    competition_pen = 0.10 if (winner_raw > 0 and second_best / winner_raw > 0.30) else 0.0

    confidence = normalized + raw_boost - competition_pen
    return max(0.0, min(1.0, confidence))


def _build_score_breakdown(signals: EmailSignals) -> dict[str, float]:
    return {
        "offer":        signals.offer_score,
        "interview":    signals.interview_score,
        "rejection":    signals.rejection_score,
        "confirmation": signals.confirmation_score,
        "update":       signals.update_score,
    }


def _build_explanation(
    label: str,
    confidence: float,
    evidence: list[str],
    score_breakdown: dict[str, float],
) -> str:
    if label == "unknown":
        best = max(score_breakdown, key=score_breakdown.get)
        best_score = score_breakdown[best]
        return (
            f"No label reached threshold. Best raw score: {best}={best_score:.1f}. "
            "No classification possible."
        )
    runner_up_label = max(
        (l for l in score_breakdown if l != label),
        key=lambda l: score_breakdown[l],
        default=None,
    )
    runner_up = (
        f" vs runner-up {runner_up_label}={score_breakdown[runner_up_label]:.1f}"
        if runner_up_label else ""
    )
    phrases_str = ", ".join(f'"{p}"' for p in evidence[:5])
    if len(evidence) > 5:
        phrases_str += f" (+{len(evidence)-5} more)"
    return (
        f"Classified as {label} (confidence={confidence:.2f}). "
        f"Winner score: {label}={score_breakdown[label]:.1f}{runner_up}. "
        f"Matched: {phrases_str or 'none'}."
    )


def classify_email(subject: str, body: str, sender: str = "") -> ClassificationResult:
    """Classify an email and return a ClassificationResult."""
    signals = extract_signals(subject, body, sender)
    candidates = _build_candidates(signals)
    label, evidence = _apply_precedence(candidates, signals)
    winner_raw = candidates[label][0] if label in candidates else 0.0
    confidence = _compute_confidence(label, winner_raw, candidates)
    score_breakdown = _build_score_breakdown(signals)
    explanation = _build_explanation(label, confidence, evidence, score_breakdown)

    result = ClassificationResult(
        label=label,
        confidence=confidence,
        evidence=evidence,
        score_breakdown=score_breakdown,
        explanation=explanation,
    )

    # Optionally invoke LLM when confidence is low (stub always returns None)
    if confidence < LlmClassifier.CONFIDENCE_THRESHOLD:
        llm_result = LlmClassifier().classify(subject, body, sender)
        if llm_result is not None:
            return llm_result

    return result
