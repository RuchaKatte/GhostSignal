"""
PII and secret redaction - runs locally BEFORE any log text reaches an LLM.
Redacts: IPs, emails, API keys/tokens, credit-card-like numbers, JWTs, UUIDs.
"""
from __future__ import annotations

import re

# ── Regex patterns ─────────────────────────────────────────────────────────────
_PATTERNS: list[tuple[str, str]] = [
    # IPv4 addresses
    (r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP]"),
    # Email addresses
    (r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b", "[EMAIL]"),
    # JWT tokens (header.payload.signature)
    (r"eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", "[JWT]"),
    # Bearer / API key patterns (key=..., token=..., secret=..., password=...)
    (r"(?i)(api[_\-]?key|token|secret|password|bearer|authorization)[=:\s]+['\"]?[A-Za-z0-9_\-\.]{16,}['\"]?", r"\1=[REDACTED]"),
    # Credit-card-like numbers (13-19 digits, optionally space/dash separated)
    (r"\b(?:\d[\s\-]?){13,19}\b", "[CARD]"),
    # AWS-style access keys
    (r"\b(AKIA|AIPA|ASIA)[A-Z0-9]{16}\b", "[AWS_KEY]"),
    # UUIDs (optionally redact; kept here as reference - commented out by default)
    # (r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", "[UUID]"),
]

_COMPILED = [(re.compile(p), r) for p, r in _PATTERNS]


def redact(text: str) -> str:
    """Return a copy of *text* with PII/secrets replaced by placeholders."""
    for pattern, replacement in _COMPILED:
        text = pattern.sub(replacement, text)
    return text


def redact_dict(obj: dict) -> dict:
    """Recursively redact string values in a dict."""
    result = {}
    for k, v in obj.items():
        if isinstance(v, str):
            result[k] = redact(v)
        elif isinstance(v, dict):
            result[k] = redact_dict(v)
        elif isinstance(v, list):
            result[k] = [redact(x) if isinstance(x, str) else x for x in v]
        else:
            result[k] = v
    return result
