# Teams Copilot Proxy

An OpenAI-compatible proxy that lets OpenCode drive Microsoft 365 Copilot (substrate) models, translating chat-completions requests into substrate chats and model output back into tool calls. All tools execute client-side in OpenCode; the proxy never executes anything itself.

## Language

**Tone**:
A substrate-side model variant (e.g. `Gpt_5_6_Reasoning`, `Claude_Sonnet`) selected by mapping the OpenAI `model` alias.
_Avoid_: model id, engine

**Session**:
One OpenCode conversation thread, identified by `x-session-id` header when present, otherwise by the conversation key (hash of the first user message).
_Avoid_: substrate session, WebSocket session

**Attempt**:
A single substrate round-trip inside one chat-completions request. A request may chain multiple Attempts when guards or parse corrections trigger retries.
_Avoid_: retry (use only for the act of adding another Attempt)

**Guard**:
A proxy-side detector that rejects dishonest model text instead of returning it: `confabulation`, `hallucinated_completion`, `disengaged`, `tool_parse_failure`.
_Avoid_: filter, validator

**Confabulation**:
Model text falsely claiming it cannot access the workspace/tools, or referencing a server-side sandbox (e.g. `/mnt`, Teams object links).

**Hallucinated completion**:
Model text claiming a write/edit already happened when no tool has run in the Session.

**Capture**:
The monitor's payload-retention level: `off` (metadata only), `failures` (keep prompt/response excerpts only for failed or guard-triggered requests; default), `all`.

**Monitor**:
The in-process observability subsystem: event bus → SQLite sink → read-only `/monitor` API and dashboard.

**Tool call closure**:
Pairing a Tool call emitted in one request with its `Tool result (...)` observed in the next request of the same Session, recording only an error flag and result size.
