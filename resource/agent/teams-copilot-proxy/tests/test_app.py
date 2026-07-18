from __future__ import annotations

import base64
import json
import time
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from teams_copilot_proxy.app import create_app
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
from teams_copilot_proxy.substrate_client import SubstrateCopilotClient, SubstrateCopilotError
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
        def __init__(self, access_token: str, _time_zone: str, _proxy: str = ""):
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
        "model": "m365-copilot",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    first = client.post("/v1/chat/completions", headers={"X-M365-Session-Id": "work"}, json=body)
    second = client.post("/v1/chat/completions", headers={"X-M365-Session-Id": "work"}, json=body)

    assert first.status_code == 200
    assert second.status_code == 200
    assert fake.sessions[0] is fake.sessions[1]
    assert fake.sessions[0] is not None


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
