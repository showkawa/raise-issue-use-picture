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

CANDIDATE_TONES = ("Claude_Sonnet", "Gpt_5_5_Chat", "Magic")

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
        client = client_factory()
        client.tone = tone
        try:
            await client.chat("Reply with the single word: ok", [])
        except SubstrateCopilotError:
            continue
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
