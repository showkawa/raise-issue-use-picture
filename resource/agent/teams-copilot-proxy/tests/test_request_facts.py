"""OpenCode 请求元数据抽取（project 路径 / turn 类型 / 工具构成）的单元测试。"""

from __future__ import annotations

from teams_copilot_proxy.models import (
    OpenAIMessage,
    OpenAIToolCall,
    OpenAIToolCallFunction,
)
from teams_copilot_proxy.request_facts import (
    TURN_CHAT,
    TURN_TITLE,
    TURN_TOOL,
    message_bytes,
    project_path,
    tool_kinds,
    tools_fingerprint,
    turn_kind,
)

TOOLS = [
    {"type": "function", "function": {"name": "read"}},
    {"type": "function", "function": {"name": "bash"}},
    {"type": "function", "function": {"name": "mcp__github__pr"}},
    {"type": "function", "function": {"name": "skills_run"}},
    {"type": "function", "function": {"name": "task"}},
    {"type": "function", "function": {"name": "webfetch"}},
    {"type": "function", "function": {"name": "weird_thing"}},
]


def test_project_path_prefers_stated_working_directory() -> None:
    messages = [
        OpenAIMessage(
            role="system",
            content="Working directory: C:\\Users\\hh\\Desktop\\brian\\code\\boss-cli",
        ),
        OpenAIMessage(role="user", content="init this project"),
    ]
    assert project_path(messages) == "C:/Users/hh/Desktop/brian/code/boss-cli"


def test_project_path_falls_back_to_common_prefix_of_tool_arguments() -> None:
    messages = [
        OpenAIMessage(role="user", content="read the sources"),
        OpenAIMessage(
            role="assistant",
            content=None,
            tool_calls=[
                OpenAIToolCall(
                    id="c1",
                    function=OpenAIToolCallFunction(
                        name="read",
                        arguments='{"filePath": "/srv/app/src/main.py"}',
                    ),
                ),
                OpenAIToolCall(
                    id="c2",
                    function=OpenAIToolCallFunction(
                        name="read",
                        arguments='{"filePath": "/srv/app/tests/test_main.py"}',
                    ),
                ),
            ],
        ),
    ]
    assert project_path(messages) == "/srv/app"


def test_project_path_is_none_without_any_paths() -> None:
    assert project_path([OpenAIMessage(role="user", content="hello")]) is None


def test_turn_kind_separates_tool_title_and_chat_turns() -> None:
    tool_turn = [OpenAIMessage(role="user", content="do it")]
    assert turn_kind(tool_turn, TOOLS) == TURN_TOOL
    title_turn = [
        OpenAIMessage(role="system", content="Generate a short title for this chat"),
        OpenAIMessage(role="user", content="hi"),
    ]
    assert turn_kind(title_turn, None) == TURN_TITLE
    assert turn_kind([OpenAIMessage(role="user", content="hi")], None) == TURN_CHAT


def test_tool_kinds_counts_each_flavour() -> None:
    assert tool_kinds(TOOLS) == "builtin:2,mcp:1,other:1,skill:1,task:1,web:1"
    assert tool_kinds(None) is None


def test_tools_fingerprint_is_stable_and_order_independent() -> None:
    reversed_tools = list(reversed(TOOLS))
    assert tools_fingerprint(TOOLS) == tools_fingerprint(reversed_tools)
    assert tools_fingerprint([TOOLS[0]]) != tools_fingerprint(TOOLS)
    assert tools_fingerprint([]) is None


def test_message_bytes_splits_system_from_transcript() -> None:
    messages = [
        OpenAIMessage(role="system", content="rules"),
        OpenAIMessage(role="user", content="hello"),
    ]
    transcript, system = message_bytes(messages)
    assert system == len("rules")
    assert transcript == len("hello")
