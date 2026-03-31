"""Email classifier package. Public API: classify_email()."""
from .classifier import classify_email
from .models import ClassificationResult, EmailSignals

__all__ = ["classify_email", "ClassificationResult", "EmailSignals"]
