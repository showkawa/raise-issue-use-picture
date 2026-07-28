from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .models import (
    ContentPart,
    ImageInput,
    OpenAIChatRequest,
    SamplingParams,
    TranslatedRequest,
)
from .tool_protocol import render_tool_instructions, tool_reminder


_IMAGE_PART_TYPES = ("image_url", "image", "input_image")

# The substrate is a chat product with a hard identity guardrail: when a client
# system prompt asserts a competing named identity (e.g. OpenCode's "You are
# OpenCode, the best coding agent on the planet."), Copilot refuses to play along
# and answers with prose ("I'm Microsoft Copilot, not OpenCode...") instead of
# emitting a tool_call. Live A/B on the Claude tone: raw-merge = 0/3 tool calls,
# identity-neutralized merge = 3/3. So we strip only the identity assertions and
# keep the useful engineering guidance + project rules (AGENTS.md, etc.).
_IDENTITY_SUBSTITUTIONS = (
    (re.compile(r"(?im)^\s*you are opencode[,.]?\s*"), ""),
    (re.compile(r"(?i)\bthe best coding agent on the planet[.]?"), ""),
    (re.compile(r"(?i)\byou are (?:the )?opencode\b[,.]?\s*"), ""),
)

_SYSTEM_GUIDELINE_FRAMING = (
    "Project and workflow guidelines (working instructions from the user's project "
    "and tooling; follow them, but they do not change who you are):"
)


def neutralize_system_identity(text: str) -> str:
    """Remove competing-identity assertions that trip the substrate guardrail,
    preserving the surrounding engineering guidance."""
    result = text
    for pattern, repl in _IDENTITY_SUBSTITUTIONS:
        result = pattern.sub(repl, result)
    lines = result.splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).strip()


def flatten_content(content: str | list[ContentPart] | None) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(part.text or "" for part in content if part.type == "text")


def count_image_parts(content: str | list[ContentPart] | None) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for part in content if part.type in _IMAGE_PART_TYPES)


def _image_type_from_data_uri(data_uri: str) -> str:
    """Derive a file extension (e.g. "png", "jpeg") from a data: URI's MIME type."""
    header = data_uri[5:].split(",", 1)[0]  # strip leading "data:"
    mime = header.split(";", 1)[0]
    subtype = mime.split("/", 1)[-1] if "/" in mime else ""
    return subtype or "png"


def extract_images(content: str | list[ContentPart] | None) -> list[ImageInput]:
    """Collect uploadable images (data: URIs) from OpenAI image content parts.
    Remote http(s) image URLs are not returned here; they are counted separately
    so the caller can decide how to surface them."""
    if not isinstance(content, list):
        return []
    images: list[ImageInput] = []
    for part in content:
        if part.type not in _IMAGE_PART_TYPES or not part.image_url:
            continue
        url = part.image_url.get("url")
        if not isinstance(url, str) or not url.startswith("data:"):
            continue
        ext = _image_type_from_data_uri(url)
        images.append(ImageInput(data_uri=url, filename=f"image.{ext}", file_type=ext))
    return images


def _join_lines(lines: Iterable[str]) -> str:
    return "\n".join(line for line in lines if line).strip()


def _render_openai_message(message) -> str:
    text = flatten_content(message.content).strip()
    if message.role == "assistant" and message.tool_calls:
        calls = "; ".join(
            f'{{"name": "{call.function.name}", "arguments": {call.function.arguments or "{}"}}}'
            for call in message.tool_calls
        )
        line = f"Assistant: [tool call] {calls}"
        if text:
            line = f"Assistant: {text}\n{line}"
        return line
    if message.role == "tool":
        label = message.name or message.tool_call_id or "tool"
        return f"Tool result ({label}):\n{text}"
    if not text:
        return ""
    return f"{message.role.capitalize()}: {text}"


_TRUNCATION_MARKER = "[earlier conversation truncated]"


def _is_tool_call_line(line: str) -> bool:
    return line.startswith("Assistant:") and "[tool call]" in line


def _is_tool_result_line(line: str) -> bool:
    return line.startswith("Tool result (")


def _group_turn_units(transcript_lines: list[str]) -> list[list[str]]:
    """Group each transcript line into a turn unit, binding a tool call to the
    tool result(s) that immediately follow it so they are never split apart."""
    units: list[list[str]] = []
    index = 0
    total = len(transcript_lines)
    while index < total:
        line = transcript_lines[index]
        if _is_tool_call_line(line):
            unit = [line]
            following = index + 1
            while following < total and _is_tool_result_line(transcript_lines[following]):
                unit.append(transcript_lines[following])
                following += 1
            units.append(unit)
            index = following
        else:
            units.append([line])
            index += 1
    return units


def _truncate_transcript(transcript_lines: list[str], budget: int) -> list[str]:
    if budget <= 0:
        return transcript_lines
    total = sum(len(line) + 1 for line in transcript_lines)
    if total <= budget:
        return transcript_lines

    effective = budget - (len(_TRUNCATION_MARKER) + 1)
    kept_units: list[list[str]] = []
    used = 0
    for unit in reversed(_group_turn_units(transcript_lines)):
        used += sum(len(line) + 1 for line in unit)
        if used > effective:
            break
        kept_units.append(unit)
    kept_units.reverse()
    kept = [line for unit in kept_units for line in unit]
    kept.insert(0, _TRUNCATION_MARKER)
    return kept


def _is_web_search_tool(tool: dict[str, Any]) -> bool:
    function = tool.get("function", tool)
    name = (function.get("name") or "").lower()
    return any(k in name for k in ("web_search", "websearch", "search_web", "bing_web_search"))


def _dedup_tools(tools: list[dict[str, Any]] | None, dedup_websearch: bool) -> list[dict[str, Any]]:
    if not tools:
        return []
    if not dedup_websearch:
        return tools
    return [t for t in tools if not _is_web_search_tool(t)]


def _json_mode_instruction(response_format: dict[str, Any] | None) -> str | None:
    if not response_format:
        return None
    fmt_type = response_format.get("type")
    if fmt_type == "json_object":
        return "IMPORTANT: respond with a single valid JSON object and nothing else."
    if fmt_type == "json_schema":
        schema = response_format.get("json_schema", {})
        schema_str = json.dumps(schema, ensure_ascii=False) if isinstance(schema, dict) else str(schema)
        return (
            "IMPORTANT: respond with a single valid JSON object matching this JSON Schema "
            f"and nothing else:\n{schema_str}"
        )
    return None


def translate_openai_request(
    request: OpenAIChatRequest,
    max_transcript_chars: int = 0,
    suppress_system_prompt_with_tools: bool = False,
    sanitize_system_prompt_with_tools: bool = True,
    allow_parallel_tool_calls: bool = False,
    dedup_websearch: bool = True,
) -> TranslatedRequest:
    system_lines: list[str] = []
    transcript_lines: list[str] = []
    prompt = ""
    images: list[ImageInput] = []
    image_count = sum(count_image_parts(m.content) for m in request.messages)

    for index, message in enumerate(request.messages):
        is_last = index == len(request.messages) - 1
        if message.role in {"system", "developer"}:
            text = flatten_content(message.content).strip()
            if text:
                system_lines.append(text)
            continue
        if is_last:
            if message.role == "user":
                prompt = flatten_content(message.content).strip()
                images = extract_images(message.content)
            elif message.role == "tool":
                prompt = _render_openai_message(message)
            else:
                raise ValueError("The final OpenAI message must be a user or tool message.")
            continue
        line = _render_openai_message(message)
        if line:
            transcript_lines.append(line)

    if not prompt:
        raise ValueError("A final user or tool message is required.")

    additional_context: list[str] = []
    # Names of the context parts actually injected into this turn, so the Monitor
    # can show what the model was told instead of leaving it to guesswork.
    injections: list[str] = []
    tools = _dedup_tools(request.tools, dedup_websearch)
    if request.tools and len(tools) < len(request.tools):
        dropped_names = [
            (t.get("function", t).get("name") or "unknown") for t in request.tools if _is_web_search_tool(t)
        ]
        additional_context.append(
            f"Note: the following tools were dropped because Copilot already provides "
            f"web-grounded answers through Bing: {', '.join(dropped_names)}. Use the "
            "remaining tools for local actions."
        )
        injections.append("websearch_dropped")
    system_text = _join_lines(system_lines)
    # Client system prompts (e.g. OpenCode's) assert a competing named identity
    # that trips the substrate guardrail and suppresses tool_calls. Rather than
    # dropping the whole prompt (which loses AGENTS.md / agent rules / tool
    # discipline), we neutralize only the identity assertions and keep the rest.
    # A hard drop remains available via suppress_system_prompt_with_tools.
    if system_text:
        if tools and suppress_system_prompt_with_tools:
            injections.append("system_suppressed")
        elif tools and sanitize_system_prompt_with_tools:
            sanitized = neutralize_system_identity(system_text)
            if sanitized:
                additional_context.append(f"{_SYSTEM_GUIDELINE_FRAMING}\n{sanitized}")
                injections.append("system_sanitized")
        else:
            additional_context.append(f"System instructions:\n{system_text}")
            injections.append("system_verbatim")
    # Images carried as data: URIs on the final user turn are uploaded to the
    # substrate and referenced via message annotations, so they are NOT dropped.
    # Only warn when there are image parts we cannot upload (e.g. remote URLs).
    unuploadable = image_count - len(images)
    if unuploadable > 0:
        additional_context.append(
            f"Note: the user attached {unuploadable} image(s) by URL that this "
            "channel cannot fetch, so those image(s) were omitted. Do not claim to "
            "have seen them; ask for a text description if you need one."
        )
        injections.append("image_urls_dropped")
    protocol_bytes = 0
    if tools:
        instructions = render_tool_instructions(tools, allow_parallel_tool_calls)
        additional_context.append(instructions)
        protocol_bytes += len(instructions.encode("utf-8"))
        injections.append("tool_protocol")
    kept_lines = _truncate_transcript(transcript_lines, max_transcript_chars)
    if len(kept_lines) < len(transcript_lines):
        injections.append("transcript_truncated")
    transcript_lines = kept_lines
    transcript_text = _join_lines(transcript_lines)
    if transcript_text:
        additional_context.append(f"Prior conversation transcript:\n{transcript_text}")
        injections.append("transcript")
    json_instruction = _json_mode_instruction(request.response_format)
    if json_instruction:
        additional_context.append(json_instruction)
        injections.append("json_mode")
    if tools:
        reminder = tool_reminder(tools, allow_parallel_tool_calls)
        prompt = f"{prompt}{reminder}"
        protocol_bytes += len(reminder.encode("utf-8"))
        injections.append("tool_reminder")
    if images:
        injections.append(f"images:{len(images)}")
    sampling = SamplingParams(temperature=request.temperature, top_p=request.top_p)
    return TranslatedRequest(
        prompt=prompt,
        additional_context=additional_context,
        images=images,
        sampling=sampling,
        tools=tools,
        injections=injections,
        protocol_bytes=protocol_bytes,
    )

