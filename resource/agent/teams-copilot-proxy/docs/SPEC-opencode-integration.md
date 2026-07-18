# Spec: Drive OpenCode with M365 Copilot via teams-copilot-proxy

Status: ready-for-agent
Related: [ADR-0002](./adr/0002-opencode-over-m365-copilot.md), [ADR-0001](./adr/0001-prompt-emulated-tool-calling.md), [CONTEXT.md](../CONTEXT.md)

## Problem Statement

I want to run a real coding agent over Microsoft 365 Copilot as the backend model, so I can
get agentic, tool-using coding help through my enterprise Copilot channel instead of paying
for a separate model API. Copilot's web channel cannot do native function calling, so a
generic OpenAI client that expects reliable tool calls does not "just work" against it, and
whatever I run must not silently leak local secrets to Microsoft or silently stop mid-task
when Copilot flubs a tool call.

## Solution

Use **OpenCode** as the **Agent Loop** owner and point it at **teams-copilot-proxy** as an
OpenAI-compatible **Model Endpoint** backed by Copilot. OpenCode selects and executes tools
locally, then feeds each tool result back to Copilot to iterate until the task is done.
The proxy makes Copilot look like a tool-calling OpenAI model (Tool Calling Emulation from
ADR-0001) and adds the guardrails Copilot's channel needs: bounded correction ending in a
visible **Failure Sentinel**, **Turn-Aware Truncation**, and **Outbound Redaction** of
secrets before anything leaves for Microsoft. Local execution safety stays with OpenCode's
own permission system. The user drops a project-level `opencode.json` into a repo and runs
OpenCode as usual.

## User Stories

1. As a developer, I want to point OpenCode at the proxy with a single project-level `opencode.json`, so that I can start a Copilot-backed coding session in a repo without global setup.
2. As a developer, I want the proxy to advertise a Copilot model that OpenCode recognizes as tool-capable, so that OpenCode actually sends its `tools` and the Agent Loop runs.
3. As a developer, I want Copilot to receive OpenCode's tool definitions as Tool Protocol Instructions, so that it knows which tools exist and how to call them.
4. As a developer, I want Copilot's Tool Call Envelope parsed back into a standard OpenAI `tool_calls` response, so that OpenCode executes the chosen tool natively.
5. As a developer, I want exactly one tool call per turn honored, so that Copilot's unreliable parallel-call behavior never corrupts the loop.
6. As a developer, I want each tool result fed back and the loop to continue, so that multi-step tasks progress turn by turn.
7. As a developer, I want a clean plain-text reply (no tool_call fence) to end the loop as "task complete", so that Copilot can finish naturally.
8. As a developer, when Copilot emits a malformed tool call, I want the proxy to re-ask with an escalating, stricter correction a configurable number of times, so that transient formatting mistakes self-heal.
9. As a developer, when correction is exhausted, I want an explicit Failure Sentinel (with `finish_reason: stop`) instead of the raw malformed text, so that the loop stops visibly and I never mistake garbage for an answer.
10. As a developer, I want the proxy to never blind-retry an identical request on failure, so that Copilot quota is not wasted reproducing the same error.
11. As a developer, I want OpenCode to own conversation history (Stateless Replay via `m365-copilot`, no `:persist`), so that there is a single source of truth and no double-accumulated Copilot-side memory.
12. As a developer, I want OpenCode to own primary context bounding (`limit.context`, compaction, per-tool output caps), so that history stays within Copilot's input ceiling.
13. As a developer, I want the proxy's Transcript Budget to act only as a last-resort safety net, so that OpenCode's bounding is authoritative.
14. As a developer, I want Transcript Budget truncation to be Turn-Aware, so that a Tool Call Envelope is never split from its matching tool result.
15. As a security-conscious developer, I want secrets and sensitive content scrubbed from the transcript before it is sent to Microsoft (Outbound Redaction), so that local credentials never leave my machine in cleartext.
16. As a security-conscious developer, I want local execution safety (bash/edit/write approval, dangerous-pattern deny) handled by OpenCode's permission config, so that guarding my machine is not duplicated in the proxy.
17. As a developer, I want the proxy to keep working with a dummy `apiKey`, so that I do not have to fabricate credentials the proxy does not use.
18. As a developer, I want streaming to remain functional (buffered when tools are present), so that non-tool replies still stream and tool turns still return a valid response.
19. As an operator, I want the redaction and correction behavior to be configurable (including off), so that I can tune reliability vs. cost and audit exactly what is scrubbed.
20. As a new contributor, I want the integration documented (opencode.json example + how the loop maps onto the proxy), so that I can reproduce a session without reverse-engineering the code.

## Implementation Decisions

- **Model Endpoint capability advertisement** — The proxy's model listing and the shipped
  `opencode.json` example declare the model as tool-capable so OpenCode sends `tools`. The
  `opencode.json` uses the `@ai-sdk/openai-compatible` provider, `baseURL`
  `http://127.0.0.1:8000/v1`, dummy `apiKey`, model `m365-copilot`, and `"tool_call": true`
  plus a `limit.context` placeholder to be calibrated. Example shape (from the design):

  ```json
  {
    "provider": {
      "teams-copilot": {
        "npm": "@ai-sdk/openai-compatible",
        "options": { "baseURL": "http://127.0.0.1:8000/v1", "apiKey": "unused" },
        "models": { "m365-copilot": { "tool_call": true, "reasoning": false,
          "attachment": false, "limit": { "context": 128000, "output": 8192 } } }
      }
    }
  }
  ```

- **Correction Retry becomes configurable** — The tool-resolution path (currently
  `_chat_resolving_tools` in the app, using `correction_prompt` from the tool protocol)
  changes from a single hardcoded retry to a configurable attempt count with escalating
  strictness on the final attempt. A new setting (following the existing `M365_*` config
  convention) controls the count; default preserves today's effective behavior (one
  correction) unless raised.

- **Failure Sentinel** — When all correction attempts fail, the completion returned has
  `finish_reason: "stop"` and content set to an explicit, recognizable sentinel string
  (not the raw model text). This replaces today's degrade-to-raw-text. The sentinel string
  is a stable, documented marker so tooling/users can detect it.

- **Turn-Aware Truncation** — The transcript flattening/budgeting in the translator changes
  so that when the Transcript Budget forces dropping content, it drops whole turn units and
  never separates a Tool Call Envelope from its corresponding tool result. The budget stays
  a safety net; OpenCode remains the primary bounder.

- **Outbound Redaction** — A new redaction transform is applied to the outbound prompt and
  additional context (the flattened transcript, tool definitions, and tool results) before
  they reach the Substrate client. It scrubs secret-like tokens (API keys, bearer tokens,
  private keys, `.env`-style values) using pattern-based detection. It is configurable and
  can be disabled. It only affects what is sent upstream; it does not alter what OpenCode
  stores locally. Redaction sits between request translation and the Substrate client so all
  three OpenAI/Responses/Anthropic entry points can reuse it, though only
  `/v1/chat/completions` carries tools.

- **Session mode** — The integration uses the stateless `m365-copilot` alias. `:persist` and
  `X-M365-Session-Id` remain supported for other clients but are explicitly not part of this
  path.

- **No parallel tool calls / buffered tool streaming retained** — These ADR-0002 constraints
  are contract, not bugs; no change beyond ensuring the buffered-with-tools streaming path
  emits the Failure Sentinel consistently with the non-streaming path.

## Testing Decisions

- **What makes a good test here** — Assert only externally observable proxy behavior at the
  HTTP boundary: the JSON/SSE the proxy returns for a given request, and what transcript the
  proxy hands to the (inspectable) Copilot client. Do not assert internal function calls or
  private helper shapes.
- **Single seam** — All four behaviors are tested through the FastAPI app boundary
  (`/v1/chat/completions`, non-stream and stream) using an inspectable fake
  `SubstrateCopilotClient` that (a) records the `prompt`/`additional_context` it received and
  (b) returns scripted replies per turn. This is the existing seam.
- **Prior art** — `tests/test_app.py` already drives `create_app` with a fake client via
  `fastapi.testclient.TestClient`; new tests extend the same pattern.
- **Coverage** —
  - Outbound Redaction: feed a request whose transcript/tool result contains secret-like
    strings; assert the fake client received a scrubbed transcript and the secrets are absent.
  - Turn-Aware Truncation: feed a history that exceeds the Transcript Budget with interleaved
    tool calls/results; assert the transcript handed upstream is within budget and every
    retained Tool Call Envelope still has its paired result.
  - Correction + Sentinel: script the fake client to return malformed tool calls for N+1
    turns; assert the number of upstream attempts equals the configured count and the final
    response is the Failure Sentinel with `finish_reason: stop` — in both non-stream and
    stream paths. Also assert a clean plain-text reply still ends as `finish_reason: stop`
    without triggering correction.
  - opencode.json scaffold: validate the shipped example parses and declares
    `tool_call: true` for the model (schema/load check, not the HTTP seam).
- **Out of scope for automated tests** — Real-tenant behavior (see below); calibration of
  `limit.context` against Copilot's true input ceiling.

## Out of Scope

- Real Microsoft 365 Copilot end-to-end validation against a live tenant (requires the user's
  login/MFA) and the resulting `limit.context` calibration.
- Supporting parallel tool calls or true incremental tool streaming (ADR-0002 rejects these).
- Adding a persistent-session mode to the OpenCode path.
- Any change to OpenCode itself; only its config and the proxy are in scope.
- Local execution permission logic (owned by OpenCode's `permission` config, not the proxy).

## Further Notes

- The redaction layer is the only safeguard on the wire to Microsoft; treat gaps in it as
  security issues, not cosmetic ones.
- The Failure Sentinel string and the correction-count setting are user-visible contracts —
  document them alongside the `opencode.json` example.
- Calibrating `limit.context` will likely require a short real-tenant smoke session; until
  then 128k is a placeholder and may be wrong in either direction.
