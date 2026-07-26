"""Metadata derived from an incoming OpenCode request, for the Monitor.

Everything here is deliberately shape-only — counts, categories, fingerprints
and the project directory — so a request can be identified and compared later
without keeping the transcript or any file content in the database.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from posixpath import commonprefix

from .models import OpenAIMessage
from .translator import flatten_content

TURN_TOOL = "tool"
TURN_TITLE = "title"
TURN_SUMMARY = "summary"
TURN_CHAT = "chat"

# Absolute paths as they appear in OpenCode prompts and tool arguments; the
# Windows branch also matches the JSON-escaped form ("C:\\Users\\...").
_PATH_RE = re.compile(
    r"(?:[A-Za-z]:(?:\\\\|\\|/)[^\s\"'<>|?*\n]+|/(?:[\w.\-+@]+/)+[\w.\-+@]*)"
)
# OpenCode states the working directory in its system prompt; when present it is
# authoritative and beats guessing from the paths mentioned in the transcript.
_CWD_RE = re.compile(
    r"(?:current\s+working\s+directory|working\s+directory|cwd|project\s+root)"
    r"\s*(?:is)?\s*[:=]?\s*[\"'`<]?"
    r"([A-Za-z]:(?:\\\\|\\|/)[^\s\"'`<>\n]+|/[^\s\"'`<>\n]+)",
    re.IGNORECASE,
)

_BUILTIN_TOOLS = frozenset(
    {
        "read", "write", "edit", "multiedit", "patch", "apply_patch", "bash",
        "shell", "ls", "list", "glob", "grep", "todoread", "todowrite",
        "question", "invalid",
    }
)
_WEB_TOOLS = frozenset({"webfetch", "web_fetch", "websearch", "web_search"})


def tool_names_of(tools: Sequence[dict] | None) -> list[str]:
    names = []
    for tool in tools or []:
        name = (tool.get("function", tool) or {}).get("name")
        if name:
            names.append(str(name))
    return names


def classify_tool(name: str) -> str:
    lowered = name.lower()
    if lowered.startswith("mcp") or "mcp__" in lowered:
        return "mcp"
    if lowered.startswith("skill") or "skills_" in lowered:
        return "skill"
    if lowered in {"task", "subagent", "agent"} or lowered.startswith("task_"):
        return "task"
    if lowered in _WEB_TOOLS:
        return "web"
    if lowered in _BUILTIN_TOOLS:
        return "builtin"
    return "other"


def tool_kinds(tools: Sequence[dict] | None) -> str | None:
    """``builtin:8,mcp:3`` — which flavours of tools this client exposed."""
    counts: dict[str, int] = {}
    for name in tool_names_of(tools):
        kind = classify_tool(name)
        counts[kind] = counts.get(kind, 0) + 1
    if not counts:
        return None
    return ",".join(f"{k}:{counts[k]}" for k in sorted(counts))


def tools_fingerprint(tools: Sequence[dict] | None) -> str | None:
    """Stable id for a tool set, so tool-list changes can be correlated with
    behaviour changes across sessions."""
    names = sorted(set(tool_names_of(tools)))
    if not names:
        return None
    joined = "\x00".join(names)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]


def _normalize_path(path: str) -> str:
    return path.replace("\\\\", "\\").replace("\\", "/").rstrip("/")


def _candidate_paths(text: str) -> list[str]:
    return [_normalize_path(m) for m in _PATH_RE.findall(text)]


def project_path(messages: Sequence[OpenAIMessage]) -> str | None:
    """Best-effort project directory for the turn.

    Prefers an explicit working directory from the system prompt; otherwise takes
    the longest common directory prefix of the absolute paths mentioned in the
    transcript and tool arguments. Only the directory is kept — never contents.
    """
    texts: list[str] = []
    for message in messages:
        texts.append(flatten_content(message.content))
        for call in message.tool_calls or []:
            texts.append(call.function.arguments or "")
    blob = "\n".join(t for t in texts if t)
    if not blob:
        return None
    stated = _CWD_RE.search(blob)
    if stated:
        return _normalize_path(stated.group(1))
    dirs = []
    for path in _candidate_paths(blob):
        head = path.rsplit("/", 1)[0] if "/" in path else path
        if head:
            dirs.append(head + "/")
    if not dirs:
        return None
    shared = commonprefix(dirs)
    shared = shared[: shared.rfind("/")] if "/" in shared else ""
    return shared or None


def turn_kind(
    messages: Sequence[OpenAIMessage], tools: Sequence[dict] | None
) -> str:
    """Which kind of turn this is, so agent turns are not averaged together with
    OpenCode's title/summary side requests."""
    if tools:
        return TURN_TOOL
    blob = " ".join(
        flatten_content(m.content)
        for m in messages
        if m.role in {"system", "developer", "user"}
    ).lower()
    if "title" in blob and len(messages) <= 4:
        return TURN_TITLE
    if any(word in blob for word in ("summariz", "summary", "compact")):
        return TURN_SUMMARY
    return TURN_CHAT


def message_bytes(messages: Sequence[OpenAIMessage]) -> tuple[int, int]:
    """``(transcript_bytes, system_bytes)`` for the incoming request."""
    transcript = 0
    system = 0
    for message in messages:
        size = len(flatten_content(message.content).encode("utf-8"))
        for call in message.tool_calls or []:
            size += len((call.function.arguments or "").encode("utf-8"))
        if message.role in {"system", "developer"}:
            system += size
        else:
            transcript += size
    return transcript, system
