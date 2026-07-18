# 1. Prompt-emulated tool calling over the Substrate API

Date: 2026-07-18

## Status

Accepted

## Context

OpenCode (and other coding agents) require OpenAI-style tool calling to do agentic work: they send `tools` definitions and expect `tool_calls` in responses. The proxy's upstream is the browser-facing Substrate WebSocket API, which has no native function-calling, no `response_format`/JSON mode, and returns only plain text. Without tool calling, OpenCode can only chat.

## Decision

Emulate tool calling in the proxy for `/v1/chat/completions` (non-stream and stream):

- Inject tool definitions into the prompt as Tool Protocol Instructions; require Copilot to reply with a single fenced ```tool_call JSON block (one tool per turn).
- Parse the reply; valid envelopes become standard OpenAI `tool_calls` with `finish_reason: "tool_calls"`.
- On a malformed attempt, perform one Correction Retry; if that fails, degrade to a plain-text reply.
- Use Stateless Replay for the tool loop: the full client history (assistant tool calls, tool results) is flattened into the transcript each turn, bounded by the Transcript Budget.
- When tools are present, streaming responses are buffered (the whole upstream reply is collected before deciding between `tool_calls` and text), trading first-token latency for parse reliability.

Out of scope for this stage: tools on `/v1/responses` and `/v1/messages` (Codex CLI can use `wire_api = "chat"`), and parallel tool calls.

## Consequences

- OpenCode's built-in tools (read/edit/bash/etc.) work against M365 Copilot without client changes.
- Format compliance is prompt-enforced only; occasional degraded turns are expected and handled by retry/fallback.
- Buffered streaming means no incremental tokens when `tools` is present.
- Single-tool-per-turn serialises multi-file operations into extra round trips.
