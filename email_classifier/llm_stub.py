"""LLM classifier stub — always returns None, ready for future wiring."""
from __future__ import annotations

from typing import Optional


class LlmClassifier:
    CONFIDENCE_THRESHOLD = 0.40  # future: invoke LLM when confidence < this

    def classify(self, subject: str, body: str, sender: str) -> Optional[object]:
        """Stub: always falls back to rules result."""
        return None
