"""Secret redaction for evidence text.

Standard library only: the Jenkins evidence packager imports this module with the
system interpreter, before (or without) the project virtualenv.
"""

import re

REDACTED = "[REDACTED]"

_PATTERNS = [
    # PEM private key blocks
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    # AWS access key ids
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # GitHub / OpenAI style tokens
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    # JSON Web Tokens
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # Authorization headers
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    # Credentials embedded in URLs: scheme://user:password@host
    re.compile(r"(?<=://)[^/\s:@]+:[^/\s:@]+(?=@)"),
]

# key = value / key: value assignments whose key names a secret
_ASSIGNMENT = re.compile(
    r"(?i)\b([\w.-]*(?:password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key)"
    r"[\w.-]*)(\"?\s*[:=]\s*)(\"[^\"\n]*\"|'[^'\n]*'|[^\s,;\"']+)"
)


def redact(text: str) -> str:
    """Replace secret-looking substrings with a fixed marker."""
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)


def redact_value(value):
    """Redact every string inside a JSON-like structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value
