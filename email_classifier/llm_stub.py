"""LLM classifier — uses xAI Grok when rule-based confidence is low."""
from __future__ import annotations

import json
import os
import pathlib
from typing import Optional

from .models import ClassificationResult

_VALID_LABELS = {"confirmation", "rejection", "interview", "offer", "update", "unknown"}
_API_URL = "https://api.groq.com/openai/v1/chat/completions"
_MODEL = "llama-3.1-8b-instant"

_SYSTEM_PROMPT = """You classify job-application emails into exactly one of these labels:
- confirmation  : application received / submitted successfully
- rejection     : application declined / not moving forward
- interview     : invited to interview / phone screen / assessment
- offer         : job offer extended
- update        : general status update (not one of the above)
- unknown       : cannot determine

Reply with a JSON object only, no markdown, no extra text:
{"label": "<label>", "reason": "<one sentence>"}"""


def _load_api_key() -> str:
    """Read XAI_API_KEY from environment or .env file next to this package."""
    key = os.environ.get("GROQ_API_KEY", "")
    if key:
        return key
    # Walk up to find a .env file alongside app.py
    for parent in [pathlib.Path(__file__).parent, pathlib.Path(__file__).parent.parent]:
        env_file = parent / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GROQ_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return ""


class LlmClassifier:
    CONFIDENCE_THRESHOLD = 0.40  # invoke LLM when rule-based confidence < this

    def classify(self, subject: str, body: str, sender: str) -> Optional[ClassificationResult]:
        api_key = _load_api_key()
        if not api_key:
            return None

        # Truncate body to keep token cost low — first 1500 chars is plenty
        body_snippet = body[:1500].strip()
        user_msg = f"Subject: {subject}\nSender: {sender}\n\n{body_snippet}"

        try:
            import httpx
            resp = httpx.post(
                _API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": _MODEL,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user",   "content": user_msg},
                    ],
                    "temperature": 0,
                    "max_tokens": 80,
                },
                timeout=15,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"].strip()
            # Strip markdown code fences if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            data = json.loads(content)
            label = data.get("label", "unknown")
            if label not in _VALID_LABELS:
                label = "unknown"
            reason = data.get("reason", "")
            return ClassificationResult(
                label=label,
                confidence=0.85,
                evidence=[f"[grok] {reason}"] if reason else ["[grok]"],
                score_breakdown={},
                explanation=f"Groq/{_MODEL} fallback: {reason}",
            )
        except Exception:
            return None
