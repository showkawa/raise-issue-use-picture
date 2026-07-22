from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from teams_copilot_proxy.app import _conversation_key, create_app
from teams_copilot_proxy.models import OpenAIMessage
from teams_copilot_proxy.cli import (
    _find_m365_page,
    _is_substrate_token,
    _needs_substrate_token,
    _read_token,
    _seconds_remaining,
    _write_token,
)
from teams_copilot_proxy.config import Settings
from teams_copilot_proxy.session_store import PersistentSessionStore
from teams_copilot_proxy.guards import DISENGAGED_SENTINEL
from teams_copilot_proxy.probe import CapabilityProbeResult, probe_capabilities
from teams_copilot_proxy.substrate_client import (
    _OPTIONS_SETS,
    SubstrateCopilotClient,
    SubstrateCopilotError,
    SubstrateDisengagedError,
    SubstrateThrottledError,
)
from teams_copilot_proxy.tool_protocol import TOOL_FAILURE_SENTINEL


class FakeCopilotClient:
    def __init__(self):
        self.calls: list[tuple[str, list[str]]] = []
        self.sessions: list[object | None] = []

    async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
        self.calls.append((prompt, additional_context))
        self.sessions.append(session)
        return "copilot reply"

    async def chat_stream(
        self,
        prompt: str,
        additional_context: list[str],
        session: object | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append((prompt, additional_context))
        self.sessions.append(session)
        yield "hello"
        yield " world"


class FailingStreamCopilotClient(FakeCopilotClient):
    async def chat_stream(
        self,
        prompt: str,
        additional_context: list[str],
        session: object | None = None,
    ) -> AsyncIterator[str]:
        self.calls.append((prompt, additional_context))
        self.sessions.append(session)
        raise SubstrateCopilotError("upstream broke")
        yield ""


def build_client(fake: FakeCopilotClient) -> TestClient:
    settings = Settings(M365_ACCESS_TOKEN="fake-token")
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    return TestClient(app)


def make_jwt(exp: int, aud: str = "https://substrate.office.com/sydney") -> str:
    def encode(data: dict) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{encode({'alg': 'none'})}.{encode({'aud': aud, 'exp': exp, 'oid': 'oid', 'tid': 'tid'})}.sig"


def test_models_endpoint() -> None:
    client = build_client(FakeCopilotClient())
    client.app.state.capability = CapabilityProbeResult(
        tier="T1",
        tone="Claude_Sonnet",
        accepted_tones=[
            "Claude_Sonnet",
            "Gpt_5_5_Chat",
            "Magic",
            "Gpt_5_5_Reasoning",
            "Gpt_5_6_Reasoning",
        ],
        probed_at=0.0,
    )
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    ids = [m["id"] for m in body["data"]]
    assert ids[0] == "m365-copilot"
    assert "claude-sonnet" in ids
    assert "gpt-5-5-chat" in ids
    assert "magic" in ids
    assert "gpt-5-5-reasoning" in ids
    assert "gpt-5-6-reasoning" in ids


def test_models_endpoint_without_probe() -> None:
    client = build_client(FakeCopilotClient())
    response = client.get("/v1/models")
    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["id"] == "m365-copilot"


def test_app_starts_without_token_for_startup_capture() -> None:
    app = create_app(settings=Settings(M365_ACCESS_TOKEN=""))
    client = TestClient(app)

    response = client.get("/v1/token/status")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False


def test_token_status_reports_expiry() -> None:
    settings = Settings(M365_ACCESS_TOKEN=make_jwt(int(time.time()) + 3600))
    app = create_app(settings=settings, copilot_client_factory=lambda: FakeCopilotClient())
    client = TestClient(app)

    response = client.get("/v1/token/status")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["expires_at"]
    assert body["seconds_remaining"] > 0


def test_healthz_includes_token_remaining_time() -> None:
    settings = Settings(M365_ACCESS_TOKEN=make_jwt(int(time.time()) + 3600))
    app = create_app(settings=settings, copilot_client_factory=lambda: FakeCopilotClient())
    client = TestClient(app)

    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["token"]["valid"] is True
    assert body["token"]["seconds_remaining"] > 0


def test_token_status_rejects_non_substrate_token() -> None:
    settings = Settings(M365_ACCESS_TOKEN=make_jwt(int(time.time()) + 3600, aud="394866fc-eedb"))
    app = create_app(settings=settings, copilot_client_factory=lambda: FakeCopilotClient())
    client = TestClient(app)

    response = client.get("/v1/token/status")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["error"] == "Access token is not a substrate.office.com token."


def test_substrate_client_rejects_non_substrate_token() -> None:
    token = make_jwt(int(time.time()) + 3600, aud="394866fc-eedb")

    try:
        SubstrateCopilotClient(token)
    except SubstrateCopilotError as exc:
        assert "not a substrate.office.com token" in str(exc)
    else:
        raise AssertionError("SubstrateCopilotClient accepted a non-Substrate token")


def test_default_client_factory_reloads_token_from_env(tmp_path, monkeypatch) -> None:
    first_token = make_jwt(int(time.time()) + 3600)
    second_token = make_jwt(int(time.time()) + 7200)
    env_path = tmp_path / ".env"
    env_path.write_text(f"M365_ACCESS_TOKEN={first_token}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    seen_tokens: list[str] = []

    class RecordingCopilotClient(FakeCopilotClient):
        def __init__(self, access_token: str, _time_zone: str, _proxy: str = "", _tone: str = "Claude_Sonnet"):
            super().__init__()
            seen_tokens.append(access_token)

    monkeypatch.setattr(
        "teams_copilot_proxy.app.SubstrateCopilotClient",
        RecordingCopilotClient,
    )
    settings = Settings(M365_ACCESS_TOKEN=first_token)
    app = create_app(settings=settings)
    client = TestClient(app)

    time.sleep(0.01)
    env_path.write_text(f"M365_ACCESS_TOKEN={second_token}\n", encoding="utf-8")
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": [{"role": "user", "content": "Hello"}]},
    )

    assert response.status_code == 200
    assert seen_tokens == [second_token]


def test_cli_reads_current_token_from_env(tmp_path, monkeypatch) -> None:
    token = make_jwt(int(time.time()) + 3600)
    (tmp_path / ".env").write_text(f"M365_ACCESS_TOKEN='{token}'\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert _read_token() == token


def test_cli_write_token_ignores_commented_token_line(tmp_path, monkeypatch) -> None:
    token = make_jwt(int(time.time()) + 3600)
    env_path = tmp_path / ".env"
    env_path.write_text("# M365_ACCESS_TOKEN=old\nOTHER=value\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    _write_token(token)

    assert _read_token() == token
    assert env_path.read_text(encoding="utf-8").count("M365_ACCESS_TOKEN=") == 2


def test_cli_seconds_remaining_uses_jwt_exp() -> None:
    token = make_jwt(int(time.time()) + 3600)

    remaining = _seconds_remaining(token)

    assert 0 < remaining <= 3600


def test_cli_accepts_only_substrate_tokens() -> None:
    assert _is_substrate_token(make_jwt(int(time.time()) + 3600))
    assert not _is_substrate_token(make_jwt(int(time.time()) + 3600, aud="394866fc-eedb"))


def test_cli_knows_when_startup_capture_is_needed() -> None:
    assert _needs_substrate_token(None)
    assert _needs_substrate_token(make_jwt(int(time.time()) + 3600, aud="394866fc-eedb"))
    assert _needs_substrate_token(make_jwt(int(time.time()) - 1))
    assert not _needs_substrate_token(make_jwt(int(time.time()) + 3600))


def test_cli_startup_refresh_can_do_full_fallback(monkeypatch) -> None:
    from teams_copilot_proxy.cli import _startup_capture_loop

    seen_allow_nudge: list[bool] = []
    capture_called = False

    def fake_refresh(_port: int, *, allow_nudge: bool = True) -> bool:
        seen_allow_nudge.append(allow_nudge)
        return allow_nudge

    def fake_capture(_port: int, _timeout: int) -> bool:
        nonlocal capture_called
        capture_called = True
        return False

    monkeypatch.setattr("teams_copilot_proxy.cli._wait_for_m365_page", lambda _port, _timeout: True)
    monkeypatch.setattr("teams_copilot_proxy.cli._try_auto_refresh", fake_refresh)
    monkeypatch.setattr("teams_copilot_proxy.cli._capture_token_to_env", fake_capture)
    monkeypatch.setattr("teams_copilot_proxy.cli.time.sleep", lambda _seconds: None)

    _startup_capture_loop(9222, timeout_seconds=1)

    assert seen_allow_nudge[-1] is True
    assert capture_called is False


def test_cli_startup_refresh_waits_for_m365_page(monkeypatch) -> None:
    from teams_copilot_proxy.cli import _startup_capture_loop

    calls: list[str] = []

    def fake_wait(_port: int, _timeout: int) -> bool:
        calls.append("wait")
        return True

    def fake_refresh(_port: int, *, allow_nudge: bool = True) -> bool:
        calls.append("refresh")
        return True

    monkeypatch.setattr("teams_copilot_proxy.cli._wait_for_m365_page", fake_wait)
    monkeypatch.setattr("teams_copilot_proxy.cli._try_auto_refresh", fake_refresh)

    _startup_capture_loop(9222, timeout_seconds=1)

    assert calls == ["wait", "refresh"]


def test_cli_finds_real_m365_page_not_devtools() -> None:
    tabs = [
        {
            "type": "page",
            "url": "devtools://devtools/bundled/devtools_app.html?remoteBase=https://m365.cloud.microsoft/chat",
        },
        {"type": "page", "url": "https://m365.cloud.microsoft/chat"},
    ]

    assert _find_m365_page(tabs) == tabs[1]


def test_openai_chat_completion_translates_history() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "messages": [
                {"role": "system", "content": "Be concise."},
                {"role": "user", "content": "First question"},
                {"role": "assistant", "content": "First answer"},
                {"role": "user", "content": "Second question"},
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == "copilot reply"
    assert fake.calls == [
        (
            "Second question",
            [
                "System instructions:\nBe concise.",
                "Prior conversation transcript:\nUser: First question\nAssistant: First answer",
            ],
        )
    ]
    assert fake.sessions == [None]


def test_openai_persistent_session_header_reuses_session() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    body = {
        "model": "m365-copilot:persist",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    first = client.post("/v1/chat/completions", headers={"X-M365-Session-Id": "work"}, json=body)
    second = client.post("/v1/chat/completions", headers={"X-M365-Session-Id": "work"}, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert fake.sessions[0] is fake.sessions[1]
    assert fake.sessions[0] is not None


def test_session_header_is_ignored_for_non_persist_models() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    body = {
        "model": "claude-sonnet",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    for _ in range(2):
        response = client.post(
            "/v1/chat/completions", headers={"X-M365-Session-Id": "work"}, json=body
        )
        assert response.status_code == 200

    assert fake.sessions == [None, None]


def test_persist_suffix_with_header_creates_session_per_model_choice() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    headers = {"X-M365-Session-Id": "opencode-main"}

    plain = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "claude-sonnet", "messages": [{"role": "user", "content": "Hi"}]},
    )
    persist = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "claude-sonnet:persist", "messages": [{"role": "user", "content": "Hi"}]},
    )

    assert plain.status_code == 200 and persist.status_code == 200
    assert fake.sessions[0] is None
    assert fake.sessions[1] is not None


def test_openai_persistent_model_suffix_uses_user_as_session_key() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)

    for user in ("alice", "alice", "bob"):
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "m365-copilot:persist",
                "user": user,
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )
        assert response.status_code == 200

    assert fake.sessions[0] is fake.sessions[1]
    assert fake.sessions[0] is not fake.sessions[2]


def test_persist_suffix_without_id_derives_key_from_first_user_message() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)

    def post(first: str, *rest: str) -> None:
        messages = [{"role": "user", "content": first}]
        for text in rest:
            messages.append({"role": "assistant", "content": "ok"})
            messages.append({"role": "user", "content": text})
        response = client.post(
            "/v1/chat/completions",
            json={"model": "m365-copilot:persist", "messages": messages},
        )
        assert response.status_code == 200

    post("Fix the login bug")
    post("Fix the login bug", "now add a test")
    post("Write docs")

    assert fake.sessions[0] is fake.sessions[1]
    assert fake.sessions[0] is not None
    assert fake.sessions[2] is not fake.sessions[0]


def test_conversation_key_is_none_without_user_text() -> None:
    assert _conversation_key([OpenAIMessage(role="assistant", content="hi")]) is None
    assert _conversation_key([OpenAIMessage(role="user", content="   ")]) is None
    key = _conversation_key([OpenAIMessage(role="user", content="task")])
    assert key == _conversation_key([OpenAIMessage(role="user", content="task")])


def test_persistent_session_turn_flags_are_reserved_in_order() -> None:
    session = PersistentSessionStore().get("work")

    first_turn = session.reserve_turn()
    second_turn = session.reserve_turn()

    assert first_turn.conversation_id == second_turn.conversation_id
    assert first_turn.client_session_id == second_turn.client_session_id
    assert first_turn.is_start_of_session is True
    assert second_turn.is_start_of_session is False


def test_openai_streaming_returns_sse() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )
    assert response.status_code == 200
    assert '"role": "assistant"' in payload
    assert '"content": "hello"' in payload
    assert '"content": " world"' in payload
    assert "data: [DONE]" in payload


def test_openai_streaming_returns_error_event_on_upstream_failure() -> None:
    client = build_client(FailingStreamCopilotClient())
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert '"type": "upstream_error"' in payload
    assert '"message": "upstream broke"' in payload
    assert "data: [DONE]" in payload


def test_responses_streaming_returns_error_event_on_upstream_failure() -> None:
    client = build_client(FailingStreamCopilotClient())
    with client.stream(
        "POST",
        "/v1/responses",
        json={"model": "ignored", "stream": True, "input": "Hello"},
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert '"type": "error"' in payload
    assert '"message": "upstream broke"' in payload


def test_anthropic_messages_endpoint() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/messages",
        json={
            "model": "ignored",
            "system": "Be concise.",
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "message"
    assert body["content"][0]["text"] == "copilot reply"


def test_anthropic_streaming_returns_error_event_on_upstream_failure() -> None:
    client = build_client(FailingStreamCopilotClient())
    with client.stream(
        "POST",
        "/v1/messages",
        json={
            "model": "ignored",
            "stream": True,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert "event: error" in payload
    assert '"message": "upstream broke"' in payload


SAMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


class ToolCallingCopilotClient(FakeCopilotClient):
    def __init__(self, replies: list[str]):
        super().__init__()
        self.replies = list(replies)

    async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
        self.calls.append((prompt, additional_context))
        self.sessions.append(session)
        return self.replies.pop(0)


def test_chat_completion_returns_tool_calls_when_model_emits_tool_call_block() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```']
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read main.py"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"]) == {"path": "main.py"}
    tools_context = fake.calls[0][1]
    assert any("Tool calling protocol" in part for part in tools_context)
    assert any("read_file" in part for part in tools_context)


def test_chat_completion_plain_text_with_tools_returns_stop() -> None:
    fake = ToolCallingCopilotClient(["Just a normal answer."])
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "Just a normal answer."


def test_chat_completion_retries_once_on_malformed_tool_call() -> None:
    fake = ToolCallingCopilotClient(
        [
            '```tool_call\n{"name": "read_file", "arguments": {broken\n```',
            '```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        ]
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read a.py"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "read_file"
    assert len(fake.calls) == 2
    assert "could not be parsed" in fake.calls[1][0]


def test_chat_completion_accepts_final_tool_message() -> None:
    fake = ToolCallingCopilotClient(["Done, the file contains X."])
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [
                {"role": "user", "content": "Read a.py"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "print('hi')"},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Done, the file contains X."
    prompt, context = fake.calls[0]
    assert prompt.startswith("Tool result (call_1):")
    assert "print('hi')" in prompt
    assert any("[tool call]" in part for part in context)


def test_streaming_with_tools_emits_tool_call_chunks() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```']
    )
    client = build_client(fake)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read main.py"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert '"tool_calls"' in payload
    assert '"name": "read_file"' in payload
    assert '"finish_reason": "tool_calls"' in payload
    assert "data: [DONE]" in payload


def test_streaming_with_tools_plain_text_falls_back_to_content() -> None:
    fake = ToolCallingCopilotClient(["A plain streamed answer."])
    client = build_client(fake)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert '"content": "A plain streamed answer."' in payload
    assert '"finish_reason": "stop"' in payload


def test_tool_protocol_rejects_unknown_tool_then_falls_back_to_text() -> None:
    fake = ToolCallingCopilotClient(
        [
            '```tool_call\n{"name": "delete_everything", "arguments": {}}\n```',
            "I cannot do that with the available tools.",
        ]
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Do something"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "I cannot do that with the available tools."


MALFORMED_TOOL_CALL = '```tool_call\n{"name": "read_file", "arguments": {broken\n```'


def test_chat_completion_returns_failure_sentinel_when_all_corrections_fail() -> None:
    fake = ToolCallingCopilotClient([MALFORMED_TOOL_CALL, MALFORMED_TOOL_CALL])
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read a.py"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == TOOL_FAILURE_SENTINEL
    assert len(fake.calls) == 2


def test_streaming_returns_failure_sentinel_when_all_corrections_fail() -> None:
    fake = ToolCallingCopilotClient([MALFORMED_TOOL_CALL, MALFORMED_TOOL_CALL])
    client = build_client(fake)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read a.py"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert TOOL_FAILURE_SENTINEL in payload
    assert '"finish_reason": "stop"' in payload
    assert len(fake.calls) == 2


class DisengagingCopilotClient(FakeCopilotClient):
    async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
        self.calls.append((prompt, additional_context))
        self.sessions.append(session)
        raise SubstrateDisengagedError("disengaged")


class ThrottledCopilotClient(FakeCopilotClient):
    async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
        raise SubstrateThrottledError("substrate throttled", retry_after=30)


def test_confabulation_guard_retries_then_tool_call() -> None:
    fake = ToolCallingCopilotClient(
        [
            "I cannot access your local files. Please paste the file content.",
            '```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```',
        ]
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read main.py"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert len(fake.calls) == 2
    assert "MUST reply with ONLY one fenced tool_call block" in fake.calls[1][0]


def test_hallucinated_completion_guard_retries_then_tool_call() -> None:
    fake = ToolCallingCopilotClient(
        [
            "I have created the file for you.",
            '```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```',
        ]
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Create the file"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["finish_reason"] == "tool_calls"
    assert len(fake.calls) == 2
    assert "did not emit any tool call" in fake.calls[1][0]


def test_guards_share_retry_budget_and_report_honestly() -> None:
    confab = "I cannot access your local files. Please paste the file content."
    fake = ToolCallingCopilotClient([confab, confab])
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read main.py"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["choices"][0]["message"]["content"] == confab
    assert body["x_m365_guard"] == {"guard": "confabulation", "retries_exhausted": True}
    assert len(fake.calls) == 2


def test_disengaged_retries_with_fresh_session_then_reports() -> None:
    fake = DisengagingCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Do something"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["choices"][0]["message"]["content"] == DISENGAGED_SENTINEL
    assert body["x_m365_guard"]["guard"] == "disengaged"
    assert len(fake.calls) == 2
    assert fake.sessions[1] is None


def test_throttled_upstream_maps_to_429_with_retry_after() -> None:
    fake = ThrottledCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


class ProbingFakeClient(FakeCopilotClient):
    def __init__(self, fenced_ok: bool = True, reject_claude: bool = False):
        super().__init__()
        self.tone = "Claude_Sonnet"
        self.fenced_ok = fenced_ok
        self.reject_claude = reject_claude
        self.probe_calls: list[tuple[str, str]] = []

    async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
        self.probe_calls.append((self.tone, prompt))
        self.calls.append((prompt, additional_context))
        self.sessions.append(session)
        if self.reject_claude and self.tone.lower().startswith("claude"):
            raise SubstrateCopilotError("Failed to invoke 'Chat'")
        if "probe_echo" in prompt:
            if self.fenced_ok:
                return '```tool_call\n{"name": "probe_echo", "arguments": {"value": "ping"}}\n```'
            return "I cannot call tools."
        return "ok"


def _probe_app(fake: ProbingFakeClient, tmp_path) -> TestClient:
    settings = Settings(
        M365_ACCESS_TOKEN="fake-token",
        M365_STARTUP_PROBE=True,
        M365_PROBE_CACHE_PATH=str(tmp_path / "probe_cache.json"),
    )
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    return TestClient(app)


def test_startup_probe_selects_t1_when_claude_passes_fenced_probe(tmp_path) -> None:
    fake = ProbingFakeClient(fenced_ok=True)
    with _probe_app(fake, tmp_path) as client:
        health = client.get("/healthz").json()
        assert health["capability"]["tier"] == "T1"
        assert health["capability"]["tone"] == "Claude_Sonnet"


def test_startup_probe_falls_back_to_t3_without_claude(tmp_path) -> None:
    fake = ProbingFakeClient(reject_claude=True)
    with _probe_app(fake, tmp_path) as client:
        health = client.get("/healthz").json()
        assert health["capability"]["tier"] == "T3"
        assert health["capability"]["tone"] == "Gpt_5_5_Chat"

        client.post(
            "/v1/chat/completions",
            json={"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
        )
        assert fake.tone == "Gpt_5_5_Chat"


def test_probe_result_cache_respects_ttl(tmp_path) -> None:
    path = tmp_path / "cache.json"
    fresh = {
        "tier": "T1",
        "tone": "Claude_Sonnet",
        "accepted_tones": ["Claude_Sonnet"],
        "probed_at": time.time(),
    }
    path.write_text(json.dumps(fresh), encoding="utf-8")

    def exploding_factory():
        raise AssertionError("probe should use the cache")

    result = asyncio.run(probe_capabilities(exploding_factory, path, 3600))
    assert result.tier == "T1"

    stale = dict(fresh, probed_at=time.time() - 7200)
    path.write_text(json.dumps(stale), encoding="utf-8")
    fake = ProbingFakeClient(fenced_ok=True)
    result = asyncio.run(probe_capabilities(lambda: fake, path, 3600))
    assert result.tier == "T1"
    assert fake.probe_calls
    assert json.loads(path.read_text(encoding="utf-8"))["tier"] == "T1"


def test_probe_rejects_empty_and_refusal_replies(tmp_path) -> None:
    class SelectiveFakeClient(ProbingFakeClient):
        async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
            if "probe_echo" not in prompt:
                if self.tone == "Magic":
                    return (
                        "Sorry, I wasn't able to respond to that. "
                        "Is there something else I can help with?"
                    )
                if self.tone == "Gpt_5_6_Reasoning":
                    return "   "
            return await super().chat(prompt, additional_context, session)

    fake = SelectiveFakeClient(fenced_ok=True)
    result = asyncio.run(probe_capabilities(lambda: fake, tmp_path / "cache.json", 3600))
    assert "Claude_Sonnet" in result.accepted_tones
    assert "Gpt_5_5_Chat" in result.accepted_tones
    assert "Gpt_5_5_Reasoning" in result.accepted_tones
    assert "Magic" not in result.accepted_tones
    assert "Gpt_5_6_Reasoning" not in result.accepted_tones


def test_probe_retries_transient_refusal_once(tmp_path) -> None:
    class FlakyFakeClient(ProbingFakeClient):
        def __init__(self):
            super().__init__(fenced_ok=True)
            self.flaked = False

        async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
            if "probe_echo" not in prompt and self.tone == "Magic" and not self.flaked:
                self.flaked = True
                return "Sorry, I wasn't able to respond to that."
            return await super().chat(prompt, additional_context, session)

    fake = FlakyFakeClient()
    result = asyncio.run(probe_capabilities(lambda: fake, tmp_path / "cache.json", 3600))
    assert "Magic" in result.accepted_tones


def test_model_name_maps_to_tone() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert fake.tone == "Gpt_5_5_Chat"
    client.post(
        "/v1/chat/completions",
        json={"model": "claude-3-sonnet", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert fake.tone == "Claude_Sonnet"
    client.post(
        "/v1/chat/completions",
        json={
            "model": "gpt-4o:persist",
            "user": "u1",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert fake.tone == "Gpt_5_5_Chat"


def test_reasoning_model_names_map_to_exact_tone() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5-6-reasoning", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert fake.tone == "Gpt_5_6_Reasoning"
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5-5-reasoning", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert fake.tone == "Gpt_5_5_Reasoning"
    client.post(
        "/v1/chat/completions",
        json={"model": "gpt-5-5-chat", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert fake.tone == "Gpt_5_5_Chat"


def test_unknown_model_uses_default_claude_tone() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert fake.tone == "Claude_Sonnet"


def test_default_tone_is_configurable() -> None:
    fake = FakeCopilotClient()
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_DEFAULT_TONE="Magic")
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert fake.tone == "Magic"


def test_substrate_chat_invoke_uses_selected_tone() -> None:
    token = make_jwt(int(time.time()) + 3600)
    substrate = SubstrateCopilotClient(token, tone="Gpt_5_5_Chat")
    frame = substrate._chat_invoke("hi", "conv", "sess", "req", True)
    assert '"tone": "Gpt_5_5_Chat"' in frame


class SlowToolCallingCopilotClient(ToolCallingCopilotClient):
    def __init__(self, replies: list[str], delay: float):
        super().__init__(replies)
        self.delay = delay

    async def chat(self, prompt: str, additional_context: list[str], session: object | None = None) -> str:
        await asyncio.sleep(self.delay)
        return await super().chat(prompt, additional_context, session)


def _sse_frames(payload: str) -> list[str]:
    return [frame for frame in payload.split("\n\n") if frame]


def _sse_content_deltas(payload: str) -> list[str]:
    deltas: list[str] = []
    for line in payload.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        event = json.loads(line[len("data: "):])
        choices = event.get("choices")
        if not choices:
            continue
        content = choices[0].get("delta", {}).get("content")
        if content:
            deltas.append(content)
    return deltas


def test_streaming_with_tools_sends_keepalive_while_resolving() -> None:
    fake = SlowToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```'],
        delay=0.2,
    )
    settings = Settings(
        M365_ACCESS_TOKEN="fake-token",
        M365_STREAM_KEEPALIVE_INTERVAL_S=0.02,
    )
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read main.py"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    frames = _sse_frames(payload)
    assert frames[0].startswith("data: ")
    assert '"role": "assistant"' in frames[0]
    keepalive_frames = [frame for frame in frames if frame.startswith(": keepalive")]
    assert keepalive_frames
    first_keepalive = frames.index(keepalive_frames[0])
    first_tool_call = next(i for i, frame in enumerate(frames) if '"tool_calls"' in frame)
    assert first_keepalive < first_tool_call
    assert '"name": "read_file"' in payload
    assert '"finish_reason": "tool_calls"' in payload
    assert "data: [DONE]" in payload


def test_streaming_with_tools_chunks_plain_text_typewriter() -> None:
    fake = ToolCallingCopilotClient(["A plain streamed answer."])
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_STREAM_CHUNK_CHARS=5)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    deltas = _sse_content_deltas(payload)
    assert len(deltas) == 5
    assert "".join(deltas) == "A plain streamed answer."
    assert all(len(piece) <= 5 for piece in deltas)
    assert '"finish_reason": "stop"' in payload


def test_streaming_chunking_disabled_emits_single_content_chunk() -> None:
    fake = ToolCallingCopilotClient(["A plain streamed answer."])
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_STREAM_CHUNK_CHARS=0)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Hello"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    deltas = _sse_content_deltas(payload)
    assert deltas == ["A plain streamed answer."]


def test_streaming_failure_sentinel_is_emitted_atomically() -> None:
    fake = ToolCallingCopilotClient([MALFORMED_TOOL_CALL, MALFORMED_TOOL_CALL])
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_STREAM_CHUNK_CHARS=5)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read a.py"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert _sse_content_deltas(payload) == [TOOL_FAILURE_SENTINEL]
    assert '"finish_reason": "stop"' in payload


def test_streaming_with_tools_tool_call_stays_atomic_with_chunking() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```']
    )
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_STREAM_CHUNK_CHARS=5)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "stream": True,
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read main.py"}],
        },
    ) as response:
        payload = "".join(
            chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
            for chunk in response.iter_text()
        )

    assert response.status_code == 200
    assert payload.count('"tool_calls": [{"index": 0') == 1
    assert '"name": "read_file"' in payload
    assert '"finish_reason": "tool_calls"' in payload


def test_correction_count_is_configurable_and_final_attempt_is_strict() -> None:
    fake = ToolCallingCopilotClient([MALFORMED_TOOL_CALL] * 3)
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_TOOL_CORRECTION_RETRIES=2)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Read a.py"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["message"]["content"] == TOOL_FAILURE_SENTINEL
    assert len(fake.calls) == 3
    assert "final attempt" in fake.calls[2][0]
    assert "final attempt" not in fake.calls[1][0]


def test_tool_reminder_is_appended_after_prompt_when_tools_present() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```']
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "看下当前项目下的README.md"}],
        },
    )

    assert response.status_code == 200
    prompt = fake.calls[0][0]
    assert prompt.startswith("看下当前项目下的README.md")
    assert "tool-calling reminder" in prompt
    assert "read_file" in prompt
    assert prompt.rstrip().endswith(
        "Reply with plain text only when the task is fully complete and no tool is needed."
    )


def test_system_prompt_is_suppressed_when_tools_present() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```']
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [
                {"role": "system", "content": "You are opencode. You cannot access files."},
                {"role": "user", "content": "看下当前项目下的README.md"},
            ],
        },
    )

    assert response.status_code == 200
    context = fake.calls[0][1]
    assert not any(part.startswith("System instructions:") for part in context)
    assert not any("cannot access files" in part for part in context)
    assert any("Tool calling protocol" in part for part in context)


def test_system_prompt_kept_when_no_tools() -> None:
    fake = ToolCallingCopilotClient(["plain answer"])
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "messages": [
                {"role": "system", "content": "You are opencode."},
                {"role": "user", "content": "hi"},
            ],
        },
    )

    assert response.status_code == 200
    context = fake.calls[0][1]
    assert any(part.startswith("System instructions:") for part in context)


def test_system_prompt_kept_with_tools_when_suppression_disabled() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```']
    )
    settings = Settings(
        M365_ACCESS_TOKEN="fake-token",
        M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS=False,
    )
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [
                {"role": "system", "content": "You are opencode."},
                {"role": "user", "content": "read a.py"},
            ],
        },
    )

    assert response.status_code == 200
    context = fake.calls[0][1]
    assert any(part.startswith("System instructions:") for part in context)


def test_code_interpreter_option_sets_are_disabled() -> None:
    assert not any("code_interpreter" in option for option in _OPTIONS_SETS)


def test_no_tool_reminder_when_no_tools() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "messages": [{"role": "user", "content": "hello"}],
        },
    )

    assert response.status_code == 200
    prompt = fake.calls[0][0]
    assert "tool-calling reminder" not in prompt
    assert prompt == "hello"


def _transcript_sent(fake: FakeCopilotClient) -> str:
    for _prompt, context in fake.calls:
        for part in context:
            if part.startswith("Prior conversation transcript:"):
                return part[len("Prior conversation transcript:\n"):]
    return ""


def _history_with_tool_turn() -> list[dict]:
    messages: list[dict] = []
    for _ in range(6):
        messages.append({"role": "user", "content": "F" * 100})
        messages.append({"role": "assistant", "content": "A" * 100})
    messages.append(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                }
            ],
        }
    )
    messages.append({"role": "tool", "tool_call_id": "call_1", "content": "R" * 200})
    messages.append({"role": "user", "content": "continue"})
    return messages


def _post_with_budget(budget: int) -> FakeCopilotClient:
    fake = FakeCopilotClient()
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_MAX_TRANSCRIPT_CHARS=budget)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": _history_with_tool_turn()},
    )
    assert response.status_code == 200
    return fake


def test_turn_aware_truncation_never_orphans_a_tool_result() -> None:
    transcript = _transcript_sent(_post_with_budget(250))

    if "Tool result (call_1)" in transcript:
        assert "[tool call]" in transcript


def test_turn_aware_truncation_keeps_tool_call_with_result_within_budget() -> None:
    budget = 400
    transcript = _transcript_sent(_post_with_budget(budget))

    assert "[tool call]" in transcript
    assert "Tool result (call_1)" in transcript
    assert "FFFFFFFFFF" not in transcript
    assert len(transcript) <= budget


SECRET_BEARER = "Bearer AbC123dEf456GhI789jklMNO"
SECRET_OPENAI_KEY = "sk-ABCDEFGHIJKLMNOP1234567890"
SECRET_ENV_LINE = "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMIexample123"


def test_outbound_redaction_scrubs_secrets_in_prompt() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "messages": [
                {"role": "user", "content": f"token {SECRET_BEARER} key {SECRET_OPENAI_KEY}"}
            ],
        },
    )

    assert response.status_code == 200
    prompt, _context = fake.calls[0]
    assert "AbC123dEf456GhI789jklMNO" not in prompt
    assert SECRET_OPENAI_KEY not in prompt
    assert "[REDACTED]" in prompt


def test_outbound_redaction_scrubs_secrets_in_transcript() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "messages": [
                {"role": "user", "content": "set up creds"},
                {"role": "assistant", "content": SECRET_ENV_LINE},
                {"role": "user", "content": "continue"},
            ],
        },
    )

    assert response.status_code == 200
    transcript = _transcript_sent(fake)
    assert "wJalrXUtnFEMIexample123" not in transcript
    assert "AWS_SECRET_ACCESS_KEY=[REDACTED]" in transcript


def test_outbound_redaction_can_be_disabled() -> None:
    fake = FakeCopilotClient()
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_REDACT_OUTBOUND=False)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": [{"role": "user", "content": f"key {SECRET_OPENAI_KEY}"}]},
    )

    assert response.status_code == 200
    prompt, _context = fake.calls[0]
    assert SECRET_OPENAI_KEY in prompt


def test_outbound_redaction_does_not_change_response() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": [{"role": "user", "content": f"key {SECRET_OPENAI_KEY}"}]},
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "copilot reply"


def test_example_opencode_config_parses_and_declares_tool_call() -> None:
    config_path = Path(__file__).resolve().parent.parent / "examples" / "opencode.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    model = config["provider"]["teams-copilot"]["models"]["claude-sonnet"]
    assert model["tool_call"] is True
    options = config["provider"]["teams-copilot"]["options"]
    assert options["baseURL"] == "http://127.0.0.1:8000/v1"


def test_responses_requires_final_user_message() -> None:
    client = build_client(FakeCopilotClient())
    response = client.post(
        "/v1/responses",
        json={
            "model": "ignored",
            "input": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi"},
            ],
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "The final Responses input message must be a user message."
