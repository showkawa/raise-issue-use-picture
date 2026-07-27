"""Unit tests for tool call parsing edge cases (embedded code fences, truncation)."""

from __future__ import annotations

import json

from teams_copilot_proxy.tool_protocol import (
    parse_model_output,
    parse_model_output_multi,
)


def _build_apply_patch_tool_call(patch_text: str, complete: bool = True) -> str:
    body = json.dumps(
        {"name": "apply_patch", "arguments": {"patchText": patch_text}},
        ensure_ascii=False,
    )
    if not complete:
        # Simulate an upstream output-limit cut: drop the closing quote and braces so the
        # JSON object is left mid-string (no closing fence either).
        body = body[:-3]
    text = f"```tool_call\n{body}"
    if complete:
        text += "\n```"
    return text


def test_parse_tool_call_with_embedded_code_fences() -> None:
    """Code fences inside a string argument must not terminate the tool_call block."""
    patch_text = (
        "*** Begin Patch\n"
        "*** Add File: AGENTS.md\n"
        "+# AGENTS.md\n"
        "+\n"
        "+## Setup\n"
        "+\n"
        "+```bash\n"
        "+echo hi\n"
        "+```\n"
        "+\n"
        "+end"
    )
    text = _build_apply_patch_tool_call(patch_text)
    outcome = parse_model_output(text, {"apply_patch", "write", "read", "list"})
    assert outcome.error is None
    assert len(outcome.tool_calls) == 1
    assert outcome.tool_calls[0].name == "apply_patch"
    assert outcome.tool_calls[0].arguments == {"patchText": patch_text}


def test_parse_truncated_tool_call_with_embedded_fences_is_truncated() -> None:
    """A truncated apply_patch containing embedded fences must be reported as truncated,
    not as JSON parse failure, so the higher-level retry logic uses the split-write
    correction prompt instead of eating the parse-failure budget."""
    patch_text = (
        "*** Begin Patch\n"
        "*** Add File: AGENTS.md\n"
        "+# AGENTS.md\n"
        "+\n"
        "+## Setup\n"
        "+\n"
        "+```bash\n"
        "+echo hi\n"
        "+```\n"
        "+\n"
        "+Preserve request jitter, cooldowns, some long text that got cut off before close"
    )
    text = _build_apply_patch_tool_call(patch_text, complete=False)
    outcome = parse_model_output(text, {"apply_patch", "write", "read", "list"})
    assert outcome.error is not None
    assert outcome.error.startswith("tool_call block appears truncated")
    assert outcome.tool_calls == []


def test_parse_multiple_tool_calls_ignores_fences_inside_arguments() -> None:
    """Multi-block parsing should also ignore embedded fences in argument strings."""
    patch_text = "+```bash\n+echo one\n+```\n+\n+Done"
    call1 = (
        '{"name": "apply_patch", "arguments": {"patchText": '
        + json.dumps(patch_text, ensure_ascii=False)
        + "}}"
    )
    call2 = '{"name": "list", "arguments": {"path": "."}}'
    text = f"```tool_call\n{call1}\n```\n```tool_call\n{call2}\n```"
    outcome = parse_model_output_multi(text, {"apply_patch", "list"})
    assert outcome.error is None
    assert [c.name for c in outcome.tool_calls] == ["apply_patch", "list"]


def test_parse_mislabeled_json_fence_with_embedded_code_fences() -> None:
    """Mislabelled fences (```json) that contain embedded triple-backticks should still parse."""
    content = "+```python\n+x = 1\n+```\n"
    body = json.dumps(
        {"name": "write", "arguments": {"filePath": "AGENTS.md", "content": content}},
        ensure_ascii=False,
    )
    text = f"```json\n{body}\n```"
    outcome = parse_model_output(text, {"write"})
    assert outcome.error is None
    assert outcome.tool_calls[0].arguments == {"filePath": "AGENTS.md", "content": content}


def test_parse_tool_call_requires_closing_fence_on_own_line() -> None:
    """A bare ``` inside the middle of a line must not be treated as the block terminator."""
    body = '{"name": "write", "arguments": {"filePath": "a.md", "content": "x = ```foo```"}}'
    text = f"```tool_call\n{body}\n```"
    outcome = parse_model_output(text, {"write"})
    assert outcome.error is None
    assert outcome.tool_calls[0].arguments == {
        "filePath": "a.md",
        "content": "x = ```foo```",
    }


def test_parse_still_accepts_standard_tool_call() -> None:
    text = '```tool_call\n{"name": "read", "arguments": {"path": "main.py"}}\n```'
    outcome = parse_model_output(text, {"read"})
    assert outcome.error is None
    assert outcome.tool_calls[0].name == "read"
