"""substrate 往返事实（帧数/类型/首帧延迟/是否干净终止）的采集测试。

用假 WebSocket 喂 SignalR 帧，断言 TurnTelemetry 记录的事实——尤其是被上游
中途掐断时 terminated_cleanly 为 False，这是 /init 截断故障唯一的直接证据。
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Self

import pytest

from teams_copilot_proxy import substrate_client as sc
from teams_copilot_proxy.substrate_client import (
    SIGNALR_SEP,
    SubstrateCopilotClient,
    SubstrateCopilotError,
)


class FakeWebSocket:
    def __init__(self, frames: list[dict], fail_after: bool = False) -> None:
        self._frames = frames
        self._fail_after = fail_after
        self.sent: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info) -> bool:
        return False

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        return json.dumps({}) + SIGNALR_SEP

    async def __aiter__(self):
        for frame in self._frames:
            yield json.dumps(frame) + SIGNALR_SEP
        if self._fail_after:
            raise ConnectionResetError("upstream closed the socket")


def connect_returning(ws: FakeWebSocket):
    def _connect(*args, **kwargs):
        return ws

    return _connect


def fake_substrate_token() -> str:
    claims = {
        "aud": "https://substrate.office.com/",
        "oid": "oid-1",
        "tid": "tid-1",
        "exp": int(time.time()) + 3600,
    }
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{payload}.signature"


def build_client() -> SubstrateCopilotClient:
    return SubstrateCopilotClient(fake_substrate_token())


CHAT_FRAMES = [
    {"type": 6},
    {
        "type": 1,
        "target": "update",
        "arguments": [
            {
                "messages": [
                    {
                        "author": "bot",
                        "messageType": "Progress",
                        "text": "thinking",
                    }
                ]
            }
        ],
    },
    {
        "type": 2,
        "item": {
            "messages": [
                {
                    "author": "bot",
                    "text": "final answer",
                    "attributions": [{"seeMoreUrl": "https://example.com"}],
                }
            ]
        },
    },
    {"type": 3},
]


def test_turn_telemetry_records_frames_types_and_clean_termination(monkeypatch) -> None:
    ws = FakeWebSocket(CHAT_FRAMES)
    monkeypatch.setattr(sc.websockets, "connect", connect_returning(ws))
    client = build_client()

    text = asyncio.run(client.chat("hello", []))

    assert text == "final answer"
    turn = client.last_turn
    assert turn is not None
    assert turn.frames == len(CHAT_FRAMES) - 1
    assert turn.heartbeats == 1
    assert turn.message_types == ["Progress", "Chat"]
    assert turn.citations == 1
    assert turn.terminated_cleanly is True
    assert turn.connect_ms is not None
    assert turn.first_frame_ms is not None
    assert turn.first_text_ms is not None
    assert turn.last_text_ms is not None
    assert turn.tone == client.tone
    assert turn.sent_bytes == len("hello")
    assert turn.reply_bytes == len("final answer")
    assert turn.close_reason is None
    assert "final answer" in (turn.final_frame or "")


def test_heartbeat_frames_are_excluded_from_first_frame_timing(monkeypatch) -> None:
    """A SignalR ping ahead of the first content frame used to be timed as the
    upstream's first output, which made first_frame_ms measure the handshake."""
    ws = FakeWebSocket([{"type": 6}, {"type": 6}] + CHAT_FRAMES[1:])
    monkeypatch.setattr(sc.websockets, "connect", connect_returning(ws))
    client = build_client()

    asyncio.run(client.chat("hello", []))

    turn = client.last_turn
    assert turn is not None
    assert turn.heartbeats == 2
    assert turn.frames == len(CHAT_FRAMES) - 1


def test_turn_telemetry_marks_unclean_close_when_upstream_cuts_the_stream(
    monkeypatch,
) -> None:
    ws = FakeWebSocket(CHAT_FRAMES[:-1], fail_after=True)
    monkeypatch.setattr(sc.websockets, "connect", connect_returning(ws))
    client = build_client()

    with pytest.raises(SubstrateCopilotError):
        asyncio.run(client.chat("hello", []))

    turn = client.last_turn
    assert turn is not None
    assert turn.terminated_cleanly is False
    assert "ConnectionResetError" in (turn.close_reason or "")
