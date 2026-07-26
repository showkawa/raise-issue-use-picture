"""Monitor（阶段二·工单 01）的端到端测试。

Seam：FastAPI app + fake substrate client + TestClient——发真实的
/v1/chat/completions 请求，然后断言 /monitor/api/* 的对外行为。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from teams_copilot_proxy.app import create_app
from teams_copilot_proxy.config import Settings
from teams_copilot_proxy.guards import TOOL_PARSE_FAILURE
from teams_copilot_proxy.monitor import RequestRecord, SQLiteSink
from teams_copilot_proxy.substrate_client import SubstrateCopilotError

AUTH = {"Authorization": "Bearer fake-token"}

SAMPLE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": "Read a file",
            "parameters": {
                "type": "object",
                "properties": {"filePath": {"type": "string"}},
            },
        },
    }
]


class FakeCopilotClient:
    async def chat(
        self, prompt: str, additional_context: list[str], session: object | None = None
    ) -> str:
        return "copilot reply"

    async def chat_stream(
        self, prompt: str, additional_context: list[str], session: object | None = None
    ) -> AsyncIterator[str]:
        yield "hello"
        yield " world"


class ScriptedCopilotClient(FakeCopilotClient):
    def __init__(self, replies: list[str]):
        self.replies = list(replies)

    async def chat(
        self, prompt: str, additional_context: list[str], session: object | None = None
    ) -> str:
        return self.replies.pop(0)


class ErrorCopilotClient(FakeCopilotClient):
    async def chat(
        self, prompt: str, additional_context: list[str], session: object | None = None
    ) -> str:
        raise SubstrateCopilotError("substrate exploded")


def build_monitor_client(
    fake: FakeCopilotClient, tmp_path, **overrides
) -> TestClient:
    kwargs = {
        "M365_ACCESS_TOKEN": "fake-token",
        "M365_MONITOR_DB_PATH": str(tmp_path / "monitor.db"),
    }
    kwargs.update(overrides)
    settings = Settings(**kwargs)
    app = create_app(settings=settings, copilot_client_factory=lambda: fake)
    return TestClient(app)


def chat(client: TestClient, headers: dict | None = None, **extra) -> dict:
    body = {
        "model": "claude-sonnet",
        "messages": [{"role": "user", "content": "Read main.py"}],
    }
    body.update(extra)
    response = client.post("/v1/chat/completions", json=body, headers=headers or {})
    assert response.status_code == 200
    return response.json()


BAD_TOOL_REPLY = "```tool_call\nthis is not json\n```"


def test_monitor_api_requires_bearer_token(tmp_path) -> None:
    client = build_monitor_client(FakeCopilotClient(), tmp_path)
    for path in (
        "/monitor/api/summary",
        "/monitor/api/requests",
        "/monitor/api/requests/some-id",
    ):
        assert client.get(path).status_code == 401
        assert (
            client.get(path, headers={"Authorization": "Bearer wrong"}).status_code
            == 401
        )
    assert client.get("/monitor/api/summary", headers=AUTH).status_code == 200


def test_normal_request_records_metadata_without_content(tmp_path) -> None:
    client = build_monitor_client(FakeCopilotClient(), tmp_path)
    chat(client)

    summary = client.get("/monitor/api/summary", headers=AUTH).json()
    assert summary["requests"] == 1
    assert summary["total_tokens"] > 0
    assert summary["errors"] == 0

    requests = client.get("/monitor/api/requests", headers=AUTH).json()["requests"]
    assert len(requests) == 1
    entry = requests[0]
    assert entry["status"] == "ok"
    assert entry["model"] == "claude-sonnet"
    assert entry["tone"] == "Claude_Sonnet"
    assert entry["stream"] == 0
    assert entry["session_key"]
    assert entry["prompt_tokens"] > 0
    assert entry["completion_tokens"] > 0
    assert entry["duration_ms"] >= 0
    # capture=failures（默认）下，正常请求不留任何内容现场
    assert entry["prompt_summary"] is None
    assert entry["reply_snippet"] is None

    detail = client.get(
        f"/monitor/api/requests/{entry['id']}", headers=AUTH
    ).json()
    assert len(detail["attempts"]) == 1
    assert detail["attempts"][0]["status"] == "ok"
    assert detail["attempts"][0]["text"] is None


def test_guard_retries_produce_attempt_chain_with_excerpts(tmp_path) -> None:
    fake = ScriptedCopilotClient([BAD_TOOL_REPLY, BAD_TOOL_REPLY])
    client = build_monitor_client(fake, tmp_path)
    body = chat(client, tools=SAMPLE_TOOLS)
    assert body["x_m365_guard"]["guard"] == TOOL_PARSE_FAILURE

    requests = client.get("/monitor/api/requests", headers=AUTH).json()["requests"]
    entry = requests[0]
    assert entry["status"] == "guard"
    assert entry["guard"] == TOOL_PARSE_FAILURE
    # failures 档位：守卫触发的请求保留脱敏现场
    assert entry["prompt_summary"]

    detail = client.get(f"/monitor/api/requests/{entry['id']}", headers=AUTH).json()
    attempts = detail["attempts"]
    assert [a["seq"] for a in attempts] == [1, 2]
    assert attempts[0]["retried"] == 1
    assert attempts[0]["guard"] == TOOL_PARSE_FAILURE
    assert attempts[1]["retried"] == 0
    assert all("not json" in a["text"] for a in attempts)

    summary = client.get("/monitor/api/summary", headers=AUTH).json()
    assert summary["guarded"] == 1
    assert summary["errors"] == 1


def test_capture_failures_truncates_excerpts_to_2kb(tmp_path) -> None:
    huge = "```tool_call\n" + "x" * 5000 + "\n```"
    fake = ScriptedCopilotClient([huge, huge])
    client = build_monitor_client(fake, tmp_path)
    chat(client, tools=SAMPLE_TOOLS)

    entry = client.get("/monitor/api/requests", headers=AUTH).json()["requests"][0]
    detail = client.get(f"/monitor/api/requests/{entry['id']}", headers=AUTH).json()
    for attempt in detail["attempts"]:
        assert len(attempt["text"]) < 2200


def test_capture_off_stores_no_content_even_on_failure(tmp_path) -> None:
    fake = ScriptedCopilotClient([BAD_TOOL_REPLY, BAD_TOOL_REPLY])
    client = build_monitor_client(fake, tmp_path, M365_MONITOR_CAPTURE="off")
    chat(client, tools=SAMPLE_TOOLS)

    entry = client.get("/monitor/api/requests", headers=AUTH).json()["requests"][0]
    assert entry["status"] == "guard"
    assert entry["prompt_summary"] is None
    assert entry["reply_snippet"] is None
    detail = client.get(f"/monitor/api/requests/{entry['id']}", headers=AUTH).json()
    assert all(a["text"] is None for a in detail["attempts"])


def test_capture_all_keeps_content_for_ok_requests(tmp_path) -> None:
    client = build_monitor_client(
        FakeCopilotClient(), tmp_path, M365_MONITOR_CAPTURE="all"
    )
    chat(client)
    entry = client.get("/monitor/api/requests", headers=AUTH).json()["requests"][0]
    assert entry["status"] == "ok"
    assert "Read main.py" in entry["prompt_summary"]
    assert entry["reply_snippet"] == "copilot reply"


def test_upstream_error_recorded_with_error_status(tmp_path) -> None:
    client = build_monitor_client(ErrorCopilotClient(), tmp_path)
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "claude-sonnet",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert response.status_code == 502

    entry = client.get("/monitor/api/requests", headers=AUTH).json()["requests"][0]
    assert entry["status"] == "error"
    assert "substrate exploded" in entry["error"]


def test_streaming_request_recorded(tmp_path) -> None:
    client = build_monitor_client(FakeCopilotClient(), tmp_path)
    with client.stream(
        "POST",
        "/v1/chat/completions",
        json={
            "model": "claude-sonnet",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    ) as response:
        assert response.status_code == 200
        for _ in response.iter_text():
            pass

    entry = client.get("/monitor/api/requests", headers=AUTH).json()["requests"][0]
    assert entry["stream"] == 1
    assert entry["status"] == "ok"
    assert entry["completion_tokens"] > 0


def test_session_header_takes_priority_over_conversation_key(tmp_path) -> None:
    client = build_monitor_client(FakeCopilotClient(), tmp_path)
    chat(client, headers={"x-session-id": "opencode-thread-1"})
    chat(client)  # 无 header：回退到 conversation key

    filtered = client.get(
        "/monitor/api/requests", headers=AUTH, params={"session": "opencode-thread-1"}
    ).json()["requests"]
    assert len(filtered) == 1
    assert filtered[0]["session_key"] == "opencode-thread-1"

    everything = client.get("/monitor/api/requests", headers=AUTH).json()["requests"]
    fallback = [r for r in everything if r["session_key"] != "opencode-thread-1"]
    assert len(fallback) == 1
    assert len(fallback[0]["session_key"]) == 16  # 首条 user 消息哈希


def test_monitor_disabled_leaves_main_path_unchanged(tmp_path) -> None:
    client = build_monitor_client(
        FakeCopilotClient(), tmp_path, M365_MONITOR_ENABLED=False
    )
    body = chat(client)
    assert body["choices"][0]["message"]["content"] == "copilot reply"
    assert body["usage"]["total_tokens"] > 0
    assert client.get("/monitor/api/summary", headers=AUTH).status_code == 404
    assert not (tmp_path / "monitor.db").exists()


def test_monitor_init_failure_does_not_break_chat(tmp_path) -> None:
    # 指向不存在的目录使 SQLite 初始化失败：Monitor 必须静默降级，主链路不受影响
    client = build_monitor_client(
        FakeCopilotClient(),
        tmp_path,
        M365_MONITOR_DB_PATH=str(tmp_path / "missing-dir" / "monitor.db"),
    )
    body = chat(client)
    assert body["choices"][0]["message"]["content"] == "copilot reply"
    assert client.get("/monitor/api/summary", headers=AUTH).status_code == 404


def test_retention_cleanup_deletes_expired_requests(tmp_path) -> None:
    sink = SQLiteSink(str(tmp_path / "monitor.db"), retention_days=30)
    old = RequestRecord(
        id="req-old",
        ts=time.time() - 40 * 86400,
        session_key="s",
        model="m",
        tone="t",
        stream=False,
    )
    fresh = RequestRecord(
        id="req-fresh",
        ts=time.time(),
        session_key="s",
        model="m",
        tone="t",
        stream=False,
    )
    sink.write(old)
    sink.write(fresh)
    sink.cleanup()
    ids = [r["id"] for r in sink.requests(limit=10)]
    assert ids == ["req-fresh"]
    sink.close()
