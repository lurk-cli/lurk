"""Title sanitization — strip sensitive patterns before storage."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Email addresses
    (re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"), "[email]"),
    # Phone numbers (US formats)
    (re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"), "[phone]"),
    # Credit card numbers
    (re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b"), "[card]"),
    # Auth tokens / API keys (long hex or base64 strings, 32+ chars)
    (re.compile(r"\b[A-Za-z0-9+/\-_]{32,}={0,2}\b"), "[token]"),
    # Known sensitive URL segments
    (re.compile(r"(?:/account|/billing|/password|/settings/security)\b", re.I), "[sensitive]"),
]


def sanitize_title(title: str) -> str:
    """Strip sensitive patterns from a window title."""
    result = title
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result
