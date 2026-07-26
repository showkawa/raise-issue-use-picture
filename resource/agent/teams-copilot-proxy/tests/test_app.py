from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi.testclient import TestClient

from teams_copilot_proxy.app import _conversation_key, _tone_for_model, create_app
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
        self.images: list[object] = []

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
        def __init__(
            self,
            access_token: str,
            _time_zone: str,
            _proxy: str = "",
            _tone: str = "Claude_Sonnet",
            throttle_retries: int = 2,
        ):
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


def test_neutralize_system_identity_strips_persona_keeps_guidance() -> None:
    from teams_copilot_proxy.translator import neutralize_system_identity

    text = (
        "You are OpenCode, the best coding agent on the planet.\n\n"
        "You must run tests after edits.\n- Never edit vendor/."
    )
    out = neutralize_system_identity(text)
    assert "OpenCode" not in out
    assert "best coding agent on the planet" not in out
    assert "You must run tests after edits." in out
    assert "Never edit vendor/." in out


def test_neutralize_system_identity_handles_lowercase_and_inline() -> None:
    from teams_copilot_proxy.translator import neutralize_system_identity

    text = "You are opencode, an interactive CLI tool that helps users."
    out = neutralize_system_identity(text)
    assert out.lower().startswith("an interactive cli tool")
    assert "opencode" not in out.lower()


def test_translate_sanitizes_system_prompt_when_tools_present() -> None:
    from teams_copilot_proxy.models import OpenAIChatRequest
    from teams_copilot_proxy.translator import (
        _SYSTEM_GUIDELINE_FRAMING,
        translate_openai_request,
    )

    request = OpenAIChatRequest(
        model="claude-sonnet",
        messages=[
            {"role": "system", "content": "You are OpenCode, the best coding agent on the planet.\nRun tests."},
            {"role": "user", "content": "Do the thing"},
        ],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {}}}],
    )
    translated = translate_openai_request(request, 200_000)
    guideline_blocks = [c for c in translated.additional_context if c.startswith(_SYSTEM_GUIDELINE_FRAMING)]
    assert len(guideline_blocks) == 1
    block = guideline_blocks[0]
    assert "OpenCode" not in block
    assert "best coding agent on the planet" not in block
    assert "Run tests." in block


def test_translate_can_hard_drop_system_prompt_with_tools() -> None:
    from teams_copilot_proxy.models import OpenAIChatRequest
    from teams_copilot_proxy.translator import (
        _SYSTEM_GUIDELINE_FRAMING,
        translate_openai_request,
    )

    request = OpenAIChatRequest(
        model="claude-sonnet",
        messages=[
            {"role": "system", "content": "You are OpenCode. Run tests."},
            {"role": "user", "content": "Do the thing"},
        ],
        tools=[{"type": "function", "function": {"name": "read", "parameters": {}}}],
    )
    translated = translate_openai_request(
        request, 200_000, suppress_system_prompt_with_tools=True
    )
    assert not any(c.startswith(_SYSTEM_GUIDELINE_FRAMING) for c in translated.additional_context)
    assert not any(c.startswith("System instructions:") for c in translated.additional_context)


def test_translate_keeps_raw_system_prompt_without_tools() -> None:
    from teams_copilot_proxy.models import OpenAIChatRequest
    from teams_copilot_proxy.translator import translate_openai_request

    request = OpenAIChatRequest(
        model="claude-sonnet",
        messages=[
            {"role": "system", "content": "You are OpenCode. Be concise."},
            {"role": "user", "content": "Hi"},
        ],
    )
    translated = translate_openai_request(request, 200_000)
    assert any(c == "System instructions:\nYou are OpenCode. Be concise." for c in translated.additional_context)


def test_estimate_tokens_monotonic() -> None:
    from teams_copilot_proxy.usage import estimate_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0
    assert estimate_tokens("a") == 1
    assert estimate_tokens("a" * 100) > estimate_tokens("a" * 10)


def test_chat_completion_includes_usage() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "messages": [{"role": "user", "content": "Hello there"}]},
    )
    assert response.status_code == 200
    usage = response.json()["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_streaming_includes_usage_in_final_chunk() -> None:
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
    final = None
    for line in payload.splitlines():
        if not line.startswith("data: ") or line == "data: [DONE]":
            continue
        event = json.loads(line[len("data: "):])
        choices = event.get("choices") or [{}]
        if choices[0].get("finish_reason") == "stop":
            final = event
    assert final is not None
    usage = final["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_tool_call_completion_includes_usage() -> None:
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
    usage = response.json()["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_chat_completion_forwards_data_uri_images_instead_of_dropping() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "What is in this screenshot?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,AAAA"},
                        },
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200
    prompt, context = fake.calls[0]
    assert prompt == "What is in this screenshot?"
    # The image is uploaded and referenced, not dropped, so there is no omission note.
    assert not any("were omitted" in part for part in context)
    assert len(fake.images) == 1
    assert fake.images[0].data_uri == "data:image/png;base64,AAAA"
    assert fake.images[0].file_type == "png"


def test_chat_completion_warns_only_for_unfetchable_remote_image_urls() -> None:
    fake = FakeCopilotClient()
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Look at this"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "https://example.com/pic.png"},
                        },
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200
    _, context = fake.calls[0]
    assert any("were omitted" in part for part in context)
    assert fake.images == []


def _bare_substrate_client() -> SubstrateCopilotClient:
    """Build a client without running token validation, for offline unit tests."""
    client = SubstrateCopilotClient.__new__(SubstrateCopilotClient)
    client._token = "tok"
    client._oid = "oid-1"
    client._tid = "tid-1"
    client._proxy = ""
    client._time_zone = "Asia/Tokyo"
    client.tone = "Magic"
    client.images = []
    client.options = {}
    return client


def test_encode_multipart_repeats_field_names_and_closes_boundary() -> None:
    from teams_copilot_proxy.substrate_client import _encode_multipart

    body, boundary = _encode_multipart(
        [("scenario", "UploadImage"), ("optionsSets", "x"), ("optionsSets", "y")]
    )
    text = body.decode("utf-8")
    assert text.count('name="optionsSets"') == 2
    assert 'name="scenario"' in text
    assert text.rstrip().endswith(f"--{boundary}--")


def test_chat_invoke_embeds_image_annotations_and_gptv_option() -> None:
    client = _bare_substrate_client()
    annotations = [{
        "id": "0-ea-d7-abc",
        "messageAnnotationMetadata": {"@type": "File", "fileType": "png"},
        "messageAnnotationType": "ImageFile",
    }]
    frame = client._chat_invoke("hi", "conv", "sess", "req", True, annotations)
    arg = json.loads(frame.rstrip("\x1e"))["arguments"][0]
    assert arg["message"]["messageAnnotations"] == annotations
    assert "gptvnorm2048" in arg["optionsSets"]


def test_chat_invoke_without_images_has_no_annotations() -> None:
    client = _bare_substrate_client()
    frame = client._chat_invoke("hi", "conv", "sess", "req", True)
    arg = json.loads(frame.rstrip("\x1e"))["arguments"][0]
    assert arg["message"]["messageAnnotations"] == []
    assert "gptvnorm2048" not in arg["optionsSets"]


def test_chat_invoke_options_default_empty() -> None:
    client = _bare_substrate_client()
    frame = client._chat_invoke("hi", "conv", "sess", "req", True)
    arg = json.loads(frame.rstrip("\x1e"))["arguments"][0]
    assert arg["options"] == {}


def test_chat_invoke_forwards_sampling_options_when_set() -> None:
    client = _bare_substrate_client()
    client.options = {"temperature": 0.0, "topP": 0.1}
    frame = client._chat_invoke("hi", "conv", "sess", "req", True)
    arg = json.loads(frame.rstrip("\x1e"))["arguments"][0]
    assert arg["options"] == {"temperature": 0.0, "topP": 0.1}


def test_translate_carries_sampling_params() -> None:
    from teams_copilot_proxy.models import OpenAIChatRequest
    from teams_copilot_proxy.translator import translate_openai_request

    request = OpenAIChatRequest(
        model="claude-sonnet",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.0,
        top_p=0.5,
    )
    translated = translate_openai_request(request, 200_000)
    assert translated.sampling.as_options() == {"temperature": 0.0, "topP": 0.5}


def test_translate_omits_unset_sampling_params() -> None:
    from teams_copilot_proxy.models import OpenAIChatRequest
    from teams_copilot_proxy.translator import translate_openai_request

    request = OpenAIChatRequest(
        model="claude-sonnet",
        messages=[{"role": "user", "content": "hi"}],
    )
    translated = translate_openai_request(request, 200_000)
    assert translated.sampling.as_options() == {}


def test_parse_multi_collects_every_tool_call_block() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output_multi

    text = (
        '```tool_call\n{"name": "read", "arguments": {"path": "a"}}\n```\n'
        '```tool_call\n{"name": "read", "arguments": {"path": "b"}}\n```'
    )
    outcome = parse_model_output_multi(text, {"read"})
    assert outcome.error is None
    assert [tc.name for tc in outcome.tool_calls] == ["read", "read"]
    assert [tc.arguments["path"] for tc in outcome.tool_calls] == ["a", "b"]


def test_parse_multi_falls_back_to_single_block() -> None:
    from teams_copilot_proxy.tool_protocol import (
        parse_model_output,
        parse_model_output_multi,
    )

    text = '```tool_call\n{"name": "read", "arguments": {}}\n```'
    multi = parse_model_output_multi(text, {"read"})
    single = parse_model_output(text, {"read"})
    assert len(multi.tool_calls) == 1
    assert multi.tool_call.name == single.tool_call.name


def test_parse_multi_rejects_unknown_tool_in_any_block() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output_multi

    text = (
        '```tool_call\n{"name": "read", "arguments": {}}\n```\n'
        '```tool_call\n{"name": "nope", "arguments": {}}\n```'
    )
    outcome = parse_model_output_multi(text, {"read"})
    assert outcome.error is not None
    assert not outcome.tool_calls


def test_parallel_header_permits_multiple_tools() -> None:
    from teams_copilot_proxy.tool_protocol import render_tool_instructions

    tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    single = render_tool_instructions(tools, allow_parallel=False)
    parallel = render_tool_instructions(tools, allow_parallel=True)
    assert "at most ONE tool per reply" in single
    assert "at most ONE tool per reply" not in parallel
    assert "several independent tools at once" in parallel


def test_tool_outcome_completion_emits_all_tool_calls() -> None:
    from teams_copilot_proxy.app import _tool_outcome_completion
    from teams_copilot_proxy.tool_protocol import ParsedToolCall, ToolParseOutcome

    outcome = ToolParseOutcome(
        text="",
        tool_calls=[
            ParsedToolCall(name="read", arguments={"path": "a"}),
            ParsedToolCall(name="grep", arguments={"q": "x"}),
        ],
    )
    body = _tool_outcome_completion("m365-copilot", outcome)
    calls = body["choices"][0]["message"]["tool_calls"]
    assert [c["function"]["name"] for c in calls] == ["read", "grep"]
    assert body["choices"][0]["finish_reason"] == "tool_calls"


def test_parse_recovers_tool_call_from_mislabelled_json_fence() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output

    text = (
        "Let me think. I need to inspect the entry point first.\n\n"
        '```json\n{"name": "read", "arguments": {"path": "main.py"}}\n```'
    )
    outcome = parse_model_output(text, {"read"})
    assert outcome.error is None
    assert outcome.tool_call is not None
    assert outcome.tool_call.name == "read"
    assert outcome.tool_call.arguments == {"path": "main.py"}


def test_fenced_json_fallback_ignores_unknown_tool_names() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output

    # A JSON sample that is not one of the tools must stay plain text.
    text = 'Here is an example config:\n```json\n{"name": "not_a_tool", "arguments": {}}\n```'
    outcome = parse_model_output(text, {"read"})
    assert outcome.tool_call is None
    assert outcome.error is None


def test_fenced_json_fallback_skipped_when_ambiguous() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output

    # Two candidate blocks -> ambiguous, so no tool call is recovered.
    text = (
        '```json\n{"name": "read", "arguments": {"path": "a"}}\n```\n'
        '```json\n{"name": "read", "arguments": {"path": "b"}}\n```'
    )
    outcome = parse_model_output(text, {"read"})
    assert outcome.tool_call is None


def test_confabulation_detects_sandbox_and_mount_hallucination() -> None:
    from teams_copilot_proxy.guards import detect_confabulation

    sandbox_reply = (
        "I can't access the repository from this execution environment. The available "
        "workspace is /mnt/data, which is empty. Please attach or mount the repository "
        "files into the available workspace."
    )
    assert detect_confabulation(sandbox_reply)
    # A reasoning tone that refuses by claiming the file is not reachable.
    not_accessible = (
        "I can summarize it once `app.py` is available, but no `app.py` file is "
        "currently accessible in the project workspace."
    )
    assert detect_confabulation(not_accessible)
    # Real Gpt_5_6_Reasoning refusals captured live during tuning.
    live_refusals = [
        "I couldn't locate a repository or project files in the accessible workspace, "
        "so I can't inspect the layout. Please make the repository available through "
        "the project tools, then rerun this initialization request.",
        "I couldn't locate the project repository or its main entry point in the "
        "accessible workspace. Please provide the repository through the project tooling.",
        "I checked the available project directory, but it contains no repository "
        "files, so there is no main entry point to inspect or explain.",
    ]
    for reply in live_refusals:
        assert detect_confabulation(reply), reply
    # A normal reply that merely mentions files must not trip the guard.
    assert not detect_confabulation("I will attach the generated report to the PR.")
    assert not detect_confabulation("The program prints 'hello' and then exits.")
    assert not detect_confabulation("I read main.py; it defines a CLI entry point.")


def test_tool_protocol_header_forbids_server_sandbox() -> None:
    from teams_copilot_proxy.tool_protocol import render_tool_instructions

    tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    for header in (
        render_tool_instructions(tools, allow_parallel=False),
        render_tool_instructions(tools, allow_parallel=True),
    ):
        assert "/mnt/data" in header
        assert "no separate" in header


def test_upload_images_chains_conversation_and_builds_annotations() -> None:
    from teams_copilot_proxy.models import ImageInput

    client = _bare_substrate_client()
    calls: list[tuple[str, str]] = []

    async def fake_upload(conv_id: str, image: ImageInput) -> tuple[str, str]:
        calls.append((conv_id, image.filename))
        return f"doc-{len(calls)}", "server-conv"

    client._upload_image = fake_upload  # type: ignore[method-assign]
    images = [
        ImageInput(data_uri="data:image/png;base64,AAAA", filename="a.png", file_type="png"),
        ImageInput(data_uri="data:image/jpeg;base64,BBBB", filename="b.jpg", file_type="jpeg"),
    ]
    conv, annotations = asyncio.run(client._upload_images("start", images))
    assert conv == "server-conv"
    assert calls[0][0] == "start"
    assert calls[1][0] == "server-conv"  # second upload reuses the assigned conversation
    assert [a["id"] for a in annotations] == ["doc-1", "doc-2"]
    assert annotations[1]["messageAnnotationMetadata"]["fileType"] == "jpeg"


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


def test_hosted_file_link_guard_retries_with_targeted_prompt() -> None:
    fake = ToolCallingCopilotClient(
        [
            (
                "Created [`AGENTS.md`](https://jp-prod.asyncgw.teams.microsoft.com"
                "/v1/objects/0-ea-d2-abc/views/original/AGENTS.md) covering the CI commands."
            ),
            '```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```',
        ]
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Create AGENTS.md"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["finish_reason"] == "tool_calls"
    assert len(fake.calls) == 2
    assert "hosted/server-side file link" in fake.calls[1][0]


def test_truncated_tool_call_retries_with_split_write_prompt() -> None:
    truncated = (
        '```tool_call\n{"name": "read_file", "arguments": {"path": "docs/AGENTS'
    )
    fake = ToolCallingCopilotClient(
        [
            truncated,
            '```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        ]
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Write AGENTS.md"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["finish_reason"] == "tool_calls"
    assert len(fake.calls) == 2
    retry_prompt = fake.calls[1][0]
    assert "cut off" in retry_prompt
    assert "Do NOT resend the same call" in retry_prompt


def test_redirect_guard_retry_does_not_consume_parse_failure_budget() -> None:
    """A hosted-link redirect and a later truncated call each get their own retry."""
    fake = ToolCallingCopilotClient(
        [
            (
                "Created [`AGENTS.md`](https://jp-prod.asyncgw.teams.microsoft.com"
                "/v1/objects/0-ea-d2-abc/views/original/AGENTS.md)."
            ),
            '```tool_call\n{"name": "read_file", "arguments": {"path": "docs/AGE',
            '```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        ]
    )
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Create AGENTS.md"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["choices"][0]["finish_reason"] == "tool_calls"
    assert len(fake.calls) == 3
    assert "hosted/server-side file link" in fake.calls[1][0]
    assert "cut off" in fake.calls[2][0]


def test_exhausted_truncation_budget_reports_truncated_guard() -> None:
    truncated = '```tool_call\n{"name": "read_file", "arguments": {"path": "AGE'
    fake = ToolCallingCopilotClient([truncated, truncated])
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "Write AGENTS.md"}],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["x_m365_guard"]["guard"] == "tool_output_truncated"
    assert body["choices"][0]["message"]["content"] == TOOL_FAILURE_SENTINEL


def test_substrate_client_retries_throttled_turn_before_first_chunk() -> None:
    token = make_jwt(int(time.time()) + 3600)
    substrate = SubstrateCopilotClient(token, throttle_retries=2)
    attempts = {"n": 0}

    async def fake_stream(prompt, additional_context, session=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise SubstrateThrottledError("throttled", retry_after=0)
        yield "recovered"

    substrate.chat_stream = fake_stream
    assert asyncio.run(substrate.chat("hi", [])) == "recovered"
    assert attempts["n"] == 3

    attempts["n"] = 0
    strict = SubstrateCopilotClient(token, throttle_retries=0)
    strict.chat_stream = fake_stream
    try:
        asyncio.run(strict.chat("hi", []))
    except SubstrateThrottledError:
        pass
    else:
        raise AssertionError("throttle_retries=0 must surface the 429")


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
            "messages": [{"role": "user", "content": "çœ‹ä¸‹å½“å‰é¡¹ç›®ä¸‹çš„README.md"}],
        },
    )

    assert response.status_code == 200
    prompt = fake.calls[0][0]
    assert prompt.startswith("çœ‹ä¸‹å½“å‰é¡¹ç›®ä¸‹çš„README.md")
    assert "tool-calling reminder" in prompt
    assert "read_file" in prompt
    assert prompt.rstrip().endswith(
        "Reply with plain text only when the task is fully complete and no tool is needed."
    )


def test_system_prompt_is_sanitized_when_tools_present() -> None:
    from teams_copilot_proxy.translator import _SYSTEM_GUIDELINE_FRAMING

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
                {
                    "role": "system",
                    "content": "You are opencode, the best coding agent on the planet.\nAlways run tests after edits.",
                },
                {"role": "user", "content": "çœ‹ä¸‹å½“å‰é¡¹ç›®ä¸‹çš„README.md"},
            ],
        },
    )

    assert response.status_code == 200
    context = fake.calls[0][1]
    # No raw "System instructions:" block, and the competing identity is stripped,
    # but the engineering guidance is preserved under the neutral guideline framing.
    assert not any(part.startswith("System instructions:") for part in context)
    guideline_blocks = [p for p in context if p.startswith(_SYSTEM_GUIDELINE_FRAMING)]
    assert len(guideline_blocks) == 1
    assert "opencode" not in guideline_blocks[0].lower()
    assert "best coding agent on the planet" not in guideline_blocks[0]
    assert "Always run tests after edits." in guideline_blocks[0]
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


def test_system_prompt_kept_raw_with_tools_when_sanitize_and_suppress_disabled() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```']
    )
    settings = Settings(
        M365_ACCESS_TOKEN="fake-token",
        M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS=False,
        M365_SANITIZE_SYSTEM_PROMPT_WITH_TOOLS=False,
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
    assert any(part == "System instructions:\nYou are opencode." for part in context)


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


def test_reasoning_effort_routes_chat_tone_to_reasoning_sibling() -> None:
    assert _tone_for_model("gpt-5-5-chat", "Claude_Sonnet", "high") == "Gpt_5_5_Reasoning"
    assert _tone_for_model("gpt-5-5-chat", "Claude_Sonnet", "medium") == "Gpt_5_5_Reasoning"
    assert _tone_for_model("gpt-5-5-chat", "Claude_Sonnet", "low") == "Gpt_5_5_Chat"
    assert _tone_for_model("claude-sonnet", "Claude_Sonnet", "xhigh") == "Claude_Sonnet_Reasoning"
    assert _tone_for_model("gpt-quick", "Claude_Sonnet", "high") == "Gpt_Reasoning"
    # Explicit reasoning ids are never downgraded by a low/none effort.
    assert _tone_for_model("gpt-5-6-reasoning", "Claude_Sonnet", "low") == "Gpt_5_6_Reasoning"


def test_effort_suffix_on_model_id_selects_reasoning_tone() -> None:
    assert _tone_for_model("gpt-5-5-chat-high", "Claude_Sonnet") == "Gpt_5_5_Reasoning"
    assert _tone_for_model("gpt-5-6-reasoning-high", "Claude_Sonnet") == "Gpt_5_6_Reasoning"
    assert _tone_for_model("gpt-5-6-reasoning-low", "Claude_Sonnet") == "Gpt_5_6_Reasoning"
    # Explicit request field wins over the suffix.
    assert _tone_for_model("gpt-5-5-chat-high", "Claude_Sonnet", "low") == "Gpt_5_5_Chat"


def test_extended_tone_catalog_routes_by_model_id() -> None:
    assert _tone_for_model("gpt-5-2-chat", "Claude_Sonnet") == "Gpt_5_2_Chat"
    assert _tone_for_model("gpt-5-4-reasoning", "Claude_Sonnet") == "Gpt_5_4_Reasoning"
    assert _tone_for_model("gpt-quick", "Claude_Sonnet") == "Gpt_Quick"
    assert _tone_for_model("claude-sonnet-reasoning", "Claude_Sonnet") == "Claude_Sonnet_Reasoning"


def test_dotted_model_ids_route_like_the_gateway_catalog() -> None:
    assert _tone_for_model("gpt-5.5", "Claude_Sonnet") == "Gpt_5_5_Chat"
    assert _tone_for_model("gpt-5.5-reasoning", "Claude_Sonnet") == "Gpt_5_5_Reasoning"
    assert _tone_for_model("gpt-5.6-reasoning", "Claude_Sonnet") == "Gpt_5_6_Reasoning"
    assert _tone_for_model("gpt-5.2", "Claude_Sonnet") == "Gpt_5_2_Chat"
    assert _tone_for_model("gpt-5.4-reasoning", "Claude_Sonnet") == "Gpt_5_4_Reasoning"
    assert _tone_for_model("claude-sonnet-reasoning", "Claude_Sonnet") == "Claude_Sonnet_Reasoning"


def test_bare_model_aliases_route_to_chat_or_reasoning_sibling() -> None:
    assert _tone_for_model("gpt-5-5", "Claude_Sonnet") == "Gpt_5_5_Chat"
    assert _tone_for_model("gpt-5-2", "Claude_Sonnet") == "Gpt_5_2_Chat"
    assert _tone_for_model("gpt-5-4", "Claude_Sonnet") == "Gpt_5_4_Chat"
    assert _tone_for_model("gpt-5-6", "Claude_Sonnet") == "Gpt_5_6_Reasoning"
    assert _tone_for_model("claude", "Claude_Sonnet") == "Claude_Sonnet"
    assert _tone_for_model("quick", "Claude_Sonnet") == "Gpt_Quick"
    assert _tone_for_model("think-deeper", "Claude_Sonnet") == "Gpt_Reasoning"
    # Effort still upgrades a bare chat alias to its reasoning sibling.
    assert _tone_for_model("gpt-5.5", "Claude_Sonnet", "high") == "Gpt_5_5_Reasoning"
    assert _tone_for_model("claude", "Claude_Sonnet", "xhigh") == "Claude_Sonnet_Reasoning"


def test_models_endpoint_lists_full_routable_catalog() -> None:
    client = build_client(FakeCopilotClient())
    ids = [m["id"] for m in client.get("/v1/models").json()["data"]]
    for expected in (
        "gpt-5-5-chat",
        "gpt-5-5-reasoning",
        "gpt-5-6-reasoning",
        "claude-sonnet",
        "claude-sonnet-reasoning",
    ):
        assert expected in ids


def test_schema_validation_rejects_missing_required_then_retries() -> None:
    fake = ToolCallingCopilotClient([
        '```tool_call\n{"name": "read_file", "arguments": {}}\n```',
        '```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```',
    ])
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
    call = response.json()["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"path": "main.py"}
    assert len(fake.calls) == 2
    correction_prompt = fake.calls[1][0]
    assert "missing required" in correction_prompt


def test_schema_validation_rejects_wrong_argument_type() -> None:
    fake = ToolCallingCopilotClient([
        '```tool_call\n{"name": "read_file", "arguments": {"path": 123}}\n```',
        '```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
    ])
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
    call = response.json()["choices"][0]["message"]["tool_calls"][0]
    assert json.loads(call["function"]["arguments"]) == {"path": "a.py"}
    assert "must be of type" in fake.calls[1][0]


def _repeated_failure_transcript() -> list[dict]:
    failing_call = {
        "id": "call_1",
        "type": "function",
        "function": {"name": "read_file", "arguments": '{"path": "gone.py"}'},
    }
    second_call = dict(failing_call, id="call_2")
    return [
        {"role": "user", "content": "Read gone.py"},
        {"role": "assistant", "content": None, "tool_calls": [failing_call]},
        {"role": "tool", "tool_call_id": "call_1", "content": "Error: file not found"},
        {"role": "assistant", "content": None, "tool_calls": [second_call]},
        {"role": "tool", "tool_call_id": "call_2", "content": "Error: file not found"},
        {"role": "user", "content": "continue"},
    ]


def test_repeated_identical_failure_injects_strategy_hint() -> None:
    fake = ToolCallingCopilotClient(["I will stop retrying that file."])
    client = build_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": _repeated_failure_transcript(),
        },
    )

    assert response.status_code == 200
    _prompt, context = fake.calls[0]
    ledger = next(p for p in context if "EVIDENCE_LEDGER" in p)
    assert "failed repeatedly" in ledger


def test_single_failure_does_not_inject_repeat_strategy_hint() -> None:
    fake = ToolCallingCopilotClient(["Trying another path."])
    client = build_client(fake)
    transcript = _repeated_failure_transcript()[:3] + [{"role": "user", "content": "continue"}]
    response = client.post(
        "/v1/chat/completions",
        json={"model": "ignored", "tools": SAMPLE_TOOLS, "messages": transcript},
    )

    assert response.status_code == 200
    _prompt, context = fake.calls[0]
    # A single completed call still yields an evidence ledger, but without the
    # "failed repeatedly" / repeat strategy nudge.
    ledger = next(p for p in context if "EVIDENCE_LEDGER" in p)
    assert "failed repeatedly" not in ledger
    assert "already been issued more than once" not in ledger


def test_shell_fence_recovered_as_bash_call_when_available() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output

    text = "Let me list files.\n```bash\nls -la /tmp\n```"
    outcome = parse_model_output(text, {"bash", "read"})
    assert outcome.error is None
    assert outcome.tool_call is not None
    assert outcome.tool_call.name == "bash"
    assert outcome.tool_call.arguments == {"command": "ls -la /tmp"}


def test_shell_fence_ignored_without_shell_tool() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output

    text = "Here's how:\n```bash\nls -la\n```"
    outcome = parse_model_output(text, {"read", "grep"})
    assert outcome.tool_call is None
    assert outcome.error is None


def test_shell_fence_skipped_when_ambiguous_multiple_blocks() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output

    text = "```bash\nls\n```\nand also\n```bash\npwd\n```"
    outcome = parse_model_output(text, {"bash"})
    assert outcome.tool_call is None


def test_bare_command_json_recovered_as_bash() -> None:
    from teams_copilot_proxy.tool_protocol import parse_model_output

    outcome = parse_model_output('{"command": "echo hi", "timeout": 5}', {"bash"})
    assert outcome.tool_call is not None
    assert outcome.tool_call.name == "bash"
    assert outcome.tool_call.arguments == {"command": "echo hi", "timeout": 5}


def test_dedupe_tool_calls_drops_identical_within_reply() -> None:
    from teams_copilot_proxy.tool_protocol import ParsedToolCall, dedupe_tool_calls

    calls = [
        ParsedToolCall(name="read", arguments={"path": "a", "n": 1}),
        ParsedToolCall(name="read", arguments={"n": 1, "path": "a"}),  # key order swap
        ParsedToolCall(name="read", arguments={"path": "b"}),
    ]
    deduped = dedupe_tool_calls(calls)
    assert [(c.name, c.arguments) for c in deduped] == [
        ("read", {"path": "a", "n": 1}),
        ("read", {"path": "b"}),
    ]


def test_parallel_reply_with_duplicate_blocks_is_deduped_end_to_end() -> None:
    dup = '{"name": "read_file", "arguments": {"path": "a.py"}}'
    fake = ToolCallingCopilotClient(
        [f"```tool_call\n{dup}\n```\n```tool_call\n{dup}\n```"]
    )
    settings = Settings(
        M365_ACCESS_TOKEN="fake-token", M365_ALLOW_PARALLEL_TOOL_CALLS=True
    )
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    client = TestClient(app)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "read a.py twice"}],
        },
    )
    assert response.status_code == 200
    calls = response.json()["choices"][0]["message"]["tool_calls"]
    assert len(calls) == 1


def test_agent_ledger_hint_lists_completed_calls() -> None:
    from teams_copilot_proxy.app import _agent_ledger_hint
    from teams_copilot_proxy.models import (
        OpenAIMessage,
        OpenAIToolCall,
        OpenAIToolCallFunction,
    )

    messages = [
        OpenAIMessage(role="user", content="read a.py"),
        OpenAIMessage(
            role="assistant",
            content=None,
            tool_calls=[
                OpenAIToolCall(
                    id="c1",
                    function=OpenAIToolCallFunction(
                        name="read_file", arguments='{"path": "a.py"}'
                    ),
                )
            ],
        ),
        OpenAIMessage(role="tool", tool_call_id="c1", content="print('ok')"),
        OpenAIMessage(role="user", content="continue"),
    ]
    hint = _agent_ledger_hint(messages)
    assert hint is not None
    assert "EVIDENCE_LEDGER" in hint
    assert "read_file" in hint
    assert "failed repeatedly" not in hint


def test_agent_ledger_hint_none_without_tool_history() -> None:
    from teams_copilot_proxy.app import _agent_ledger_hint
    from teams_copilot_proxy.models import OpenAIMessage

    messages = [OpenAIMessage(role="user", content="hi")]
    assert _agent_ledger_hint(messages) is None


def _router_client(fake: FakeCopilotClient) -> TestClient:
    settings = Settings(M365_ACCESS_TOKEN="fake-token", M365_TOOL_PLANNING_MODE="router")
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    return TestClient(app)


def test_router_mode_selects_tool_in_a_single_turn() -> None:
    fake = ToolCallingCopilotClient(
        ['```tool_call\n{"name": "read_file", "arguments": {"path": "main.py"}}\n```']
    )
    client = _router_client(fake)
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
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "read_file"
    # Only the selection turn runs when a tool is chosen (no wasted answer turn).
    assert len(fake.calls) == 1
    select_context = fake.calls[0][1]
    assert any("TOOL-SELECTION TURN" in part for part in select_context)


def test_router_mode_makes_a_second_turn_for_the_answer() -> None:
    fake = ToolCallingCopilotClient(["NO_TOOL_NEEDED", "The capital is Paris."])
    client = _router_client(fake)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "ignored",
            "tools": SAMPLE_TOOLS,
            "messages": [{"role": "user", "content": "What is the capital of France?"}],
        },
    )

    assert response.status_code == 200
    choice = response.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "The capital is Paris."
    # Phase 1 selection turn + phase 2 answer turn.
    assert len(fake.calls) == 2
    assert any("TOOL-SELECTION TURN" in part for part in fake.calls[0][1])
    assert not any("TOOL-SELECTION TURN" in part for part in fake.calls[1][1])


def test_router_mode_repairs_malformed_selection() -> None:
    fake = ToolCallingCopilotClient(
        [
            '```tool_call\n{"name": "read_file", "arguments": {broken\n```',
            '```tool_call\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```',
        ]
    )
    client = _router_client(fake)
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
