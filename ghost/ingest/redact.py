"""PII and secret redaction for incident evidence."""

from __future__ import annotations

import re
from typing import Any

_IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_AWS_KEY_RE = re.compile(r"\b(?:AKIA|AIPA|ASIA)[A-Z0-9]{16}\b")
_CARD_RE = re.compile(r"\b(?:\d[\s\-]?){13,19}\b")
_AUTH_RE = re.compile(
    r"(?i)(authorization|auth|x-api-key|api[_-]?key|token|secret|password|bearer)\s*[:=]\s*['\"]?([A-Za-z0-9._~+/=\-:]{8,})['\"]?",
    re.IGNORECASE,
)
_KEY_VALUE_RE = re.compile(
    r"(?i)(\b(?:api[_-]?key|token|secret|password|session|cookie|refresh[_-]?token)\b)\s*[:=]\s*['\"]?([A-Za-z0-9._~+/=\-:]{8,})['\"]?",
    re.IGNORECASE,
)
_IDENTIFIER_FIELDS = {
    "cluster_id",
    "evidence_id",
    "evidence_ids",
    "log_id",
    "log_ids",
    "metric_id",
    "metric_ids",
}


def _redact_auth_header(match: re.Match[str]) -> str:
    label = match.group(1)
    return f"{label}=[REDACTED]"


def redact(text: str) -> str:
    """Return a copy of *text* with sensitive values replaced by placeholders."""
    if not isinstance(text, str) or text == "":
        return text

    redacted = text
    redacted = _AUTH_RE.sub(_redact_auth_header, redacted)
    redacted = _KEY_VALUE_RE.sub(r"\1=[REDACTED]", redacted)
    redacted = _JWT_RE.sub("[JWT]", redacted)
    redacted = _EMAIL_RE.sub("[EMAIL]", redacted)
    redacted = _IP_RE.sub("[IP]", redacted)
    redacted = _AWS_KEY_RE.sub("[AWS_KEY]", redacted)
    redacted = _CARD_RE.sub("[CARD]", redacted)
    return redacted


def redact_dict(obj: Any) -> Any:
    """Recursively redact sensitive values in nested dict/list structures."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {
            key: value if key in _IDENTIFIER_FIELDS else redact_dict(value)
            for key, value in obj.items()
        }
    if isinstance(obj, list):
        return [redact_dict(value) for value in obj]
    if isinstance(obj, tuple):
        return tuple(redact_dict(value) for value in obj)
    return obj
