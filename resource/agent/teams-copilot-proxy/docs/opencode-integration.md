# OpenCode + Teams Copilot Proxy integration

This guide explains how to drive [OpenCode](https://opencode.ai) with Microsoft 365 Copilot
as the model, using `teams-copilot-proxy` as an OpenAI-compatible Model Endpoint. See
[ADR-0002](adr/0002-opencode-over-m365-copilot.md) for the architectural decisions behind it.

## Quick start

1. Start the proxy and sign in once (see the [README](../README.md)):

   ```bat
   uv run teams-copilot-proxy serve
   ```

2. Drop the example [`opencode.json`](../examples/opencode.json) into your project root. It
   declares a custom provider backed by the proxy, with `tool_call: true` so OpenCode will
   actually send tool definitions and enter its local tool loop.

3. Run `opencode` in that project and pick the `m365-copilot` model under the
   **Teams Copilot Proxy** provider.

## How the loop maps onto the proxy

- **OpenCode owns the Agent Loop.** It plans, executes every tool locally (bash, read, write,
  edit, glob, grep, task, todowrite, webfetch, ...), enforces local execution permission, and
  decides when the task is done.
- **The proxy is the Model Endpoint.** It only translates requests, talks to Copilot, and maps
  Copilot's reply back to OpenAI shapes. It never executes tools.
- **Copilot is the model / reasoner.** It chooses the next tool call or writes the final answer;
  it never touches your files.
- **One tool call per turn.** Copilot emits at most one fenced ` ```tool_call ` JSON block per
  turn. OpenCode executes it, appends the result, and asks again — a serial loop. There is no
  parallel tool calling.
- **Stateless Replay.** Use the plain `m365-copilot` model (not `m365-copilot:persist`). OpenCode
  is the single source of truth for history and replays the full message list every turn; the
  proxy flattens it into a transcript. Persistent Copilot-side sessions would double-count history
  and are intentionally not used here.
- **Buffered streaming with tools.** When `tools` are present the streamed response is buffered
  and delivered at once; this only affects the typewriter feel, not correctness.

## Failure Sentinel and correction retries

When Copilot returns a malformed Tool Call Envelope, the proxy re-asks with a correction prompt
instead of passing the broken text through. This is controlled by `M365_TOOL_CORRECTION_RETRIES`
(default `1`, preserving the original single-correction behaviour). When more than one retry is
configured, the final attempt uses a stricter, minimal-schema reminder.

If every correction attempt fails, the proxy returns a stable Failure Sentinel as the assistant
message with `finish_reason: "stop"`, so OpenCode stops cleanly instead of treating malformed
model output as a final answer. The sentinel string is:

```text
[teams-copilot-proxy] Copilot could not produce a valid tool call after repeated attempts. Please rephrase the request or continue manually.
```

A clean plain-text reply (no `tool_call` fence) is always treated as a successful final answer and
never triggers a correction attempt.

## System prompt suppression when tools are present

OpenCode's built-in system prompt is written for models with native function calling and tells
the model to "output text to communicate" and answer concisely in prose. Fed to the Copilot
browser channel verbatim, it reliably pushes Copilot into a conversational refusal ("I can't
access your local files, please upload them") — and even a hallucinated `/mnt/data` sandbox —
instead of emitting a `tool_call`. Piling on extra override instructions does not beat it; the
only reliable fix is to stop forwarding that framing.

So when a request carries `tools`, the proxy drops the client system prompt and lets the tool
protocol be the only authoritative system instruction Copilot sees. This is controlled by
`M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS` (default on). Requests without tools keep the system
prompt unchanged. A short high-recency tool reminder is also appended after the user prompt, and
Copilot's own code-interpreter option sets are disabled, to further discourage the sandbox
persona.

Trade-off: Copilot no longer sees OpenCode's environment framing (working directory, `AGENTS.md`
rules, conventions) on tool turns, so it may probe with `bash`/`glob` to locate paths before
acting. Set the flag to `false` to forward the full system prompt if you prefer.

## Context budget (safety net)

OpenCode is the primary context bounder via `limit.context` and its own compaction. The proxy's
`M365_MAX_TRANSCRIPT_CHARS` is only a last-resort safety net. Its truncation is **Turn-Aware**:
it drops whole turn units oldest-first and never splits a Tool Call Envelope from its paired tool
result, so a retained tool result always keeps the call that produced it.

## Outbound redaction

Because Stateless Replay sends the whole transcript (including tool output) to Microsoft, the
proxy scrubs secret-like content (bearer/JWT tokens, `sk-` API keys, AWS access keys, PEM
private-key blocks, and `KEY=value` env secrets) before sending, replacing them with a
non-reversible `[REDACTED]` placeholder. This is controlled by `M365_REDACT_OUTBOUND`
(default on) and only affects what is sent upstream — never what OpenCode stores locally.

Local execution safety is OpenCode's job: configure `permission` in `opencode.json` (e.g. bash
`ask`, dangerous edits `deny`). The proxy does not duplicate local execution permission.

## Calibrating `limit.context`

The `limit.context` value of `128000` in the example is a placeholder. Copilot's real effective
input limit depends on the tenant and must be measured against a live signed-in session, then
backfilled into `opencode.json`.
