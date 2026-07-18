# Glossary

**Substrate API** — The browser-facing `substrate.office.com` WebSocket API used by the M365 Copilot web UI. The proxy's only upstream.

**Tool Call Envelope** — The fenced ```tool_call code block containing `{"name", "arguments"}` JSON that Copilot emits when it wants to invoke a client tool.

**Tool Protocol Instructions** — The text block the proxy injects into the upstream prompt describing available tools and the Tool Call Envelope format.

**Tool Calling Emulation** — Translating OpenAI `tools`/`tool_calls` semantics over a plain-text upstream: tool definitions are injected as Tool Protocol Instructions, and Tool Call Envelopes in the reply are parsed back into standard OpenAI `tool_calls`.

**Stateless Replay** — The conversation strategy for tool loops: every request re-sends the full client-provided history (including tool results) as a flattened transcript; the Copilot side keeps no memory.

**Correction Retry** — The single automatic re-ask sent to Copilot when its reply looks like a tool call attempt but cannot be parsed. If the retry also fails, the reply degrades to plain text.

**Transcript Budget** — The character limit (`M365_MAX_TRANSCRIPT_CHARS`) applied to the flattened prior-conversation transcript; oldest lines are dropped first.

**Persistent Session** — A Copilot-side conversation reused across turns via the `:persist` model suffix or `X-M365-Session-Id` header. Orthogonal to (and not used by) Stateless Replay tool loops.
