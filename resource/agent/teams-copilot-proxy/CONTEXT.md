# Glossary

**Substrate API** — The browser-facing `substrate.office.com` WebSocket API used by the M365 Copilot web UI. The proxy's only upstream.

**Tool Call Envelope** — The fenced ```tool_call code block containing `{"name", "arguments"}` JSON that Copilot emits when it wants to invoke a client tool.

**Tool Protocol Instructions** — The text block the proxy injects into the upstream prompt describing available tools and the Tool Call Envelope format.

**Tool Calling Emulation** — Translating OpenAI `tools`/`tool_calls` semantics over a plain-text upstream: tool definitions are injected as Tool Protocol Instructions, and Tool Call Envelopes in the reply are parsed back into standard OpenAI `tool_calls`.

**Stateless Replay** — The conversation strategy for tool loops: every request re-sends the full client-provided history (including tool results) as a flattened transcript; the Copilot side keeps no memory.

**Correction Retry** — The automatic re-ask(s) sent to Copilot when its reply looks like a tool-call attempt but cannot be parsed. Configurable count with escalating strictness; if all attempts fail, the reply becomes a Failure Sentinel rather than being passed through as a real answer.

**Failure Sentinel** — The explicit "Copilot could not produce a valid tool call" message the proxy returns (with `finish_reason: stop`) after Correction Retry is exhausted, so the loop stops visibly instead of surfacing malformed text as if it were the final answer.
_Avoid_: degrade-to-text

**Transcript Budget** — The character limit (`M365_MAX_TRANSCRIPT_CHARS`) applied to the flattened prior-conversation transcript. A last-resort safety net only: OpenCode owns primary context bounding. Truncation is Turn-Aware.

**Turn-Aware Truncation** — Transcript Budget trimming that never splits a Tool Call Envelope from its matching tool result, preserving protocol pairing.

**Persistent Session** — A Copilot-side conversation reused across turns via the `:persist` model suffix or `X-M365-Session-Id` header. Orthogonal to (and deliberately not used by) the OpenCode integration, which is Stateless.

## OpenCode integration

**OpenCode** — The terminal coding agent (`@ai-sdk/openai-compatible` provider) that owns the Agent Loop, executes tools locally, and treats the proxy as its model. Copilot never executes anything.

**Agent Loop** — The tool-execution iteration. Owned by OpenCode, not the proxy: OpenCode selects a tool, runs it, appends the result to the history, and re-asks. Copilot only reasons and picks the next tool (one per turn).
_Avoid_: loop (unqualified), orchestrator

**Model Endpoint** — The proxy's role in this integration: an OpenAI-compatible `/v1/chat/completions` surface that makes Copilot look like a tool-calling model to OpenCode.
_Avoid_: gateway, orchestrator

**Local Execution Permission** — OpenCode's own permission gate over local tool execution (bash/edit/write approval, dangerous-pattern deny). Guards the user's machine. Distinct from Outbound Redaction.

**Outbound Redaction** — The proxy-side scrubbing of secrets/sensitive content from the transcript before it is sent to Microsoft's Substrate API. The only layer on the wire to Microsoft; complements, and does not overlap with, Local Execution Permission.
