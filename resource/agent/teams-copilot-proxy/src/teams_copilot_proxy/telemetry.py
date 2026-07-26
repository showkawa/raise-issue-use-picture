"""Per-turn facts about one substrate round trip.

The substrate client fills this in while it consumes the WebSocket; the app
layer copies it onto the Monitor attempt record. It answers the questions the
rendered reply text cannot: what conversation the turn ran on, when the first
frame arrived, which message types the upstream sent, and — the reason it
exists — whether the stream actually terminated instead of being cut off.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Caps for captured content; only kept under the Monitor capture tiers.
_FRAME_LIMIT = 2048
_HEAD_LIMIT = 1024
_TAIL_LIMIT = 512


@dataclass
class TurnTelemetry:
    conversation_id: str = ""
    client_request_id: str = ""
    substrate_session_id: str = ""
    start_of_session: bool = False
    images: int = 0
    option_sets: int = 0
    sent_bytes: int = 0
    first_frame_ms: int | None = None
    frames: int = 0
    message_types: list[str] = field(default_factory=list)
    reply_bytes: int = 0
    citations: int = 0
    terminated_cleanly: bool = False
    upstream_status: int | None = None
    close_reason: str | None = None
    final_frame: str | None = None
    sent_head: str | None = None
    sent_tail: str | None = None

    def mark_sent(self, text: str) -> None:
        """Remember the head and tail of what was actually sent upstream.

        Enough to tell an intact prompt from a truncated or overwritten one
        without keeping the whole transcript.
        """
        self.sent_bytes = len(text.encode("utf-8"))
        self.sent_head = text[:_HEAD_LIMIT]
        self.sent_tail = text[-_TAIL_LIMIT:] if len(text) > _HEAD_LIMIT else None

    def mark_frame(self, elapsed_ms: int) -> None:
        self.frames += 1
        if self.first_frame_ms is None:
            self.first_frame_ms = elapsed_ms

    def mark_message(self, entry: dict) -> None:
        """Record one non-user substrate message: its type, citations and body.

        Keeps the latest body as the final frame so a truncated turn still shows
        what the upstream actually sent, not just the text the proxy rendered.
        """
        message_type = entry.get("messageType") or "Chat"
        if message_type not in self.message_types:
            self.message_types.append(message_type)
        attributions = entry.get("attributions")
        if isinstance(attributions, list):
            self.citations += len(attributions)
        try:
            body = json.dumps(entry, ensure_ascii=False)
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return
        self.final_frame = body[:_FRAME_LIMIT]

    def types_csv(self) -> str | None:
        return ",".join(self.message_types) if self.message_types else None


@runtime_checkable
class SupportsTurnTelemetry(Protocol):
    """A Copilot client that publishes facts about its last round trip."""

    last_turn: TurnTelemetry | None
