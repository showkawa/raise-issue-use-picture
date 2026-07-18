from __future__ import annotations

import re

PLACEHOLDER = "[REDACTED]"

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
    re.DOTALL,
)
_BEARER_RE = re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+")
_OPENAI_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")
_AWS_ACCESS_KEY_RE = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_ENV_SECRET_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_]*"
    r"(?:TOKEN|SECRET|PASSWORD|PASSWD|APIKEY|API_KEY|ACCESS_KEY|PRIVATE_KEY)"
    r"[A-Za-z0-9_]*)[ \t]*=[ \t]*\S+"
)


def redact_text(text: str) -> str:
    """Replace secret-like substrings with a non-reversible placeholder."""
    if not text:
        return text
    text = _PRIVATE_KEY_RE.sub(PLACEHOLDER, text)
    text = _BEARER_RE.sub(PLACEHOLDER, text)
    text = _JWT_RE.sub(PLACEHOLDER, text)
    text = _OPENAI_KEY_RE.sub(PLACEHOLDER, text)
    text = _AWS_ACCESS_KEY_RE.sub(PLACEHOLDER, text)
    text = _ENV_SECRET_RE.sub(rf"\1={PLACEHOLDER}", text)
    return text


def redact_outbound(
    prompt: str, additional_context: list[str]
) -> tuple[str, list[str]]:
    """Scrub secrets from everything about to be sent upstream to Copilot."""
    return redact_text(prompt), [redact_text(part) for part in additional_context]
