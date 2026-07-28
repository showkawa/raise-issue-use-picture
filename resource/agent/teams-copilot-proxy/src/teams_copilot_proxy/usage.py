from __future__ import annotations

import math
from typing import Any

# The browser/substrate channel does not report token counts, but OpenCode relies
# on the ``usage`` field to track context consumption and decide when to
# auto-compact a session. A missing/zero usage makes OpenCode believe the context
# is always empty and it can silently blow past the real backend limit (the M365
# Copilot conversation cap is ~265k tokens). We therefore return an approximate
# count.
#
# ~4 characters per token is the widely used rough average for English text and
# code; exactness is unnecessary here because the client only needs a monotonic,
# roughly-proportional signal for context management.
_CHARS_PER_TOKEN = 4


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / _CHARS_PER_TOKEN))


def openai_usage(input_text: str, output_text: str) -> dict[str, Any]:
    prompt = estimate_tokens(input_text)
    completion = estimate_tokens(output_text)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        # The substrate does not expose cache or modal token details; report zero
        # so clients that expect these fields keep working, without fabricating data.
        "prompt_tokens_details": {"cached_tokens": 0},
        "completion_tokens_details": {"reasoning_tokens": 0},
    }

