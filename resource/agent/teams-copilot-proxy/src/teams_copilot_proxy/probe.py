from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from .substrate_client import SubstrateCopilotClient, SubstrateCopilotError
from .tool_protocol import parse_model_output, render_tool_instructions, tool_reminder

logger = logging.getLogger(__name__)

# Tones verified against the substrate backend; the probe keeps whichever
# ones respond. Each maps to a Copilot model family:
#   Claude_Sonnet     -> Claude Sonnet 4.6 (Anthropic)
#   Gpt_5_5_Chat      -> GPT-5 chat model
#   Magic             -> GPT-5 chat model
#   Gpt_5_5_Reasoning -> GPT-5 reasoning model
#   Gpt_5_6_Reasoning -> GPT-5 reasoning model
CANDIDATE_TONES = (
    "Claude_Sonnet",
    "Gpt_5_5_Chat",
    "Magic",
    "Gpt_5_5_Reasoning",
    "Gpt_5_6_Reasoning",
)

_PROBE_PROMPT = "Reply with the single word: ok"
_PROBE_ATTEMPTS = 2

# A tone is only usable when the backend actually produces content instead
# of an empty turn or the generic canned refusal. These markers match that
# refusal so such tones are dropped from the accepted list.
_REFUSAL_MARKERS = (
    "sorry, i wasn't able to respond",
    "sorry, i wasn’t able to respond",
    "is there something else i can help with",
)


def _reply_is_usable(reply: str) -> bool:
    text = " ".join(reply.split())
    if not text:
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in _REFUSAL_MARKERS)


_PROBE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "probe_echo",
            "description": "Echo back the given value.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    }
]


@dataclass
class CapabilityProbeResult:
    tier: str
    tone: str
    accepted_tones: list[str]
    probed_at: float

    def as_dict(self) -> dict:
        return asdict(self)


def _load_cache(path: Path, ttl_seconds: float) -> CapabilityProbeResult | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        result = CapabilityProbeResult(**raw)
    except (OSError, ValueError, TypeError):
        return None
    if time.time() - result.probed_at > ttl_seconds:
        return None
    return result


def _store_cache(path: Path, result: CapabilityProbeResult) -> None:
    try:
        path.write_text(json.dumps(result.as_dict()), encoding="utf-8")
    except OSError:
        logger.warning("Could not write probe cache to %s", path)


async def probe_capabilities(
    client_factory: Callable[[], SubstrateCopilotClient],
    cache_path: Path,
    ttl_seconds: float,
    candidates: tuple[str, ...] = CANDIDATE_TONES,
) -> CapabilityProbeResult:
    cached = _load_cache(cache_path, ttl_seconds)
    if cached is not None:
        return cached

    accepted: list[str] = []
    for tone in candidates:
        if await _tone_probe_passes(client_factory, tone):
            accepted.append(tone)

    claude = next((t for t in accepted if t.lower().startswith("claude")), None)
    tier = "T3"
    tone = accepted[0] if accepted else candidates[0]
    if claude is not None and await _fenced_probe(client_factory, claude):
        tier = "T1"
        tone = claude
    if tier == "T3":
        logger.warning(
            "Capability probe: no Claude tone passed the fenced tool probe; running as T3 "
            "(tool calling is best-effort and unreliable)."
        )
    result = CapabilityProbeResult(
        tier=tier, tone=tone, accepted_tones=accepted, probed_at=time.time()
    )
    _store_cache(cache_path, result)
    return result


async def _tone_probe_passes(
    client_factory: Callable[[], SubstrateCopilotClient], tone: str
) -> bool:
    for attempt in range(1, _PROBE_ATTEMPTS + 1):
        client = client_factory()
        client.tone = tone
        try:
            reply = await client.chat(_PROBE_PROMPT, [])
        except SubstrateCopilotError:
            return False
        if _reply_is_usable(reply):
            return True
        logger.info(
            "Capability probe: tone %s returned an empty or refusal reply "
            "(attempt %d/%d).",
            tone,
            attempt,
            _PROBE_ATTEMPTS,
        )
    return False


async def _fenced_probe(
    client_factory: Callable[[], SubstrateCopilotClient], tone: str
) -> bool:
    client = client_factory()
    client.tone = tone
    prompt = 'Call the probe_echo tool with value "ping".' + tool_reminder(_PROBE_TOOLS)
    try:
        text = await client.chat(prompt, [render_tool_instructions(_PROBE_TOOLS)])
    except SubstrateCopilotError:
        return False
    outcome = parse_model_output(text, {"probe_echo"})
    return outcome.tool_call is not None
