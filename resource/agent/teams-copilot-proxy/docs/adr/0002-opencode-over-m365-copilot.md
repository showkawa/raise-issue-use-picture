---
status: accepted
---

# 2. Drive OpenCode's agent loop with M365 Copilot as the backend model

Date: 2026-07-18

## Decision

Use **OpenCode** (the terminal coding agent) as the agent-loop owner and point it at
`teams-copilot-proxy` as an OpenAI-compatible **Model Endpoint** backed by Microsoft 365
Copilot. OpenCode selects and executes tools locally and feeds results back to Copilot each
turn (`/loop`); Copilot only reasons and picks the next tool. The proxy stays a thin
translation layer — it never owns the loop, history, or execution.

This records the seven decisions taken during the design grilling.

## Considered options

The main alternative was building/keeping a bespoke agent loop (`teams-copilot-cli`) driving
Copilot directly over CDP. Rejected: OpenCode already provides a mature loop, tool set,
permission gating, history compaction, and TUI. Reusing it removes a whole codebase to
maintain; Copilot only needs to look like a tool-calling OpenAI model, which the proxy
already does.

## The seven decisions

1. **Loop ownership** — OpenCode owns the Agent Loop; proxy = Model Endpoint; Copilot = model only.
2. **Compatibility contract** — Accept the proxy's constraints as the contract rather than
   removing them: at most **one tool call per turn**, **buffered streaming when tools are
   present**, and a bounded correction path. Copilot's web channel cannot reliably emit
   parallel structured calls, so sequential single-tool turns are both simpler and more
   reliable. Parallelism is explicitly out of scope.
3. **OpenCode config** — A **project-level `opencode.json`** with a custom
   `@ai-sdk/openai-compatible` provider, `baseURL` `http://127.0.0.1:8000/v1`, and a **dummy
   `apiKey`** (the proxy authenticates upstream with its own M365 token and ignores
   `Authorization`). The model **must** declare `"tool_call": true`, or OpenCode will not send
   the `tools` array and the loop cannot work. `limit.context` bounds what OpenCode sends and
   must be calibrated against Copilot's real input ceiling (currently unverified; placeholder
   128k).
4. **History ownership** — **Stateless**. OpenCode is the single source of truth and re-sends
   the full history each turn (Stateless Replay); the `m365-copilot` alias (no `:persist`) is
   used. `:persist` / `X-M365-Session-Id` are deliberately avoided here — a Copilot-side
   session would double-accumulate context against OpenCode's resend.
5. **Context bounding** — OpenCode owns primary bounding via `limit.context`, its
   compaction/`compress`, and per-tool output caps. The proxy's Transcript Budget is only a
   last-resort safety net and must be **Turn-Aware** (never split a Tool Call Envelope from
   its result).
6. **Tool-call failure** — On a malformed tool-call attempt, run **Correction Retry**
   (configurable count, escalating strictness). If all attempts fail, return an explicit
   **Failure Sentinel** with `finish_reason: stop` — never pass malformed text through as if
   it were the answer, and never blind-retry (an identical resend reproduces the same
   failure). Note: clean prose with no tool-call fence already means "task complete" and is
   passed through unchanged.
7. **Safety split by concern** — **Local execution safety** is OpenCode's `permission` config
   (bash/edit/write approval, dangerous-pattern deny). **Outbound data exposure** is the
   proxy's job via Outbound Redaction of secrets/sensitive content before the transcript
   leaves for Microsoft's Substrate API — the proxy is the only layer on that wire.

## Consequences

- Reading N files or other batch work is serialized into N turns; TUI first-token latency is
  high because tool turns are buffered.
- The proxy gains two small pieces of work not yet implemented: Turn-Aware Truncation and an
  Outbound Redaction layer; Correction Retry becomes configurable and ends in a Failure
  Sentinel.
- Copilot's real input ceiling and end-to-end behavior remain **unverified against a live
  tenant** — decisions 3 and 5 need real-account smoke testing to calibrate `limit.context`
  and confirm OpenCode drives the loop over the proxy as designed.
