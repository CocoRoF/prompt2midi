"""Masking of credentials in text that may leave the process (errors, logs, API responses)."""
from __future__ import annotations

import re

REDACTED = "[REDACTED]"
MIN_FRAGMENT = 6  # any run of this many characters of a secret is removed from messages

_PATTERNS = [
    re.compile(r"(?i)\b(?:x-api-key|api-key|authorization)\s*[:=]\s*(?:bearer\s+)?[^\s,;'\"}]+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{6,}"),
]


def mask_secrets(text: str, secrets: list[str | None] | tuple[str | None, ...] = ()) -> str:
    """Remove every secret, and every fragment of >= 6 characters of a secret, plus key-looking tokens.

    Fragment removal handles SDK/provider messages that echo a truncated key ("Incorrect API key
    provided: sk-ant-...XYZ1").
    """
    out = str(text)
    for secret in secrets:
        if not secret or len(secret) < MIN_FRAGMENT:
            continue
        if secret in out:
            out = out.replace(secret, REDACTED)
        # longest fragments first so that a long echoed piece is replaced as one block
        for n in range(len(secret) - 1, MIN_FRAGMENT - 1, -1):
            for i in range(0, len(secret) - n + 1):
                frag = secret[i:i + n]
                if frag in out:
                    out = out.replace(frag, REDACTED)
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    return out
