# SPEC — Refactor teams-copilot-cli onto teams-copilot-proxy

Status: proposed (grilled 2026-07). Supersedes the embedded `copilot-web` browser
channel. Companion: `teams-copilot-proxy` OpenAI-compatible API.

## Goal

teams-copilot-cli stops driving Microsoft 365 Copilot itself and instead consumes
`teams-copilot-proxy` as its model backend over the OpenAI-compatible API. The proxy
owns the Copilot channel (token capture, Substrate WebSocket), the tool-call
emulation, and outbound redaction. The CLI keeps its user-facing commands and its
local execution responsibilities (tool implementations, permission gate, workspace
context) but delegates all model access to the proxy.

This removes the long-standing duplication where both projects independently solved
"make the Copilot browser channel usable for agentic tool calling."

## Decisions (from grill)

- **Q1 — Target shape: B.** The CLI adopts the proxy's OpenAI tool-calling. The CLI's
  own text `<<<TOOL>>>` / `<<<DONE>>>` / `<<<RESULT>>>` ToolProtocol is removed; tool
  calls now travel as OpenAI `tools` / `tool_calls`.
- **Q2 — System prompt: B1.** Live probes proved that a rich system prompt sent
  alongside `tools` makes Copilot refuse / go prose / hallucinate `/mnt/data`. The
  proxy's default `M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS=true` stays on. Therefore
  the CLI must **not** rely on the `system` role for behavior: working directory,
  rules/AGENTS, repo context, and permission framing move into the per-turn `user`
  message (or the proxy's tool-protocol reminder). The `system` role is left minimal
  or empty.
- **Q3 — Deletion scope: C1.** Delete the entire `src/provider/copilot-web/*` package,
  the session-resilience machinery built for the fragile browser channel (rotate,
  handshake, protocol-amnesia detection, char-budget ledger), and the `<<<TOOL>>>`
  ToolProtocol. `createProvider` keeps only `proxy` (OpenAI HTTP) and `mock`.
- **Q4a — Tool schema source.** OpenAI `tools` JSON is generated automatically from
  `ToolRegistry.schemas()` so the registry stays the single source of truth.
- **Q4b — Failure handling.** The loop recognizes the proxy's `TOOL_FAILURE_SENTINEL`
  as a tool-failure signal and feeds the error back for self-correction, terminating
  only after N consecutive failures. Tools return structured errors (`ToolResult.ok
  = false`) — e.g. for paths that do not resolve inside `projectRoot` — and the model
  is expected to retry with a corrected (absolute) path on the next turn. Path policy
  stays in the CLI; the proxy is not asked to rewrite paths.
- **Q5 — Redaction: proxy-owned.** Outbound redaction lives only in the proxy
  (`M365_REDACT_OUTBOUND`, default on). The CLI's `src/agent/redaction.ts` is removed
  to keep a single egress boundary.
- **Q6 — History: stateless.** The CLI is the single source of truth for history and
  replays the full message list every turn against the stateless `m365-copilot` model.
  `:persist` / `X-M365-Session-Id` are not used (they would double-count history).

## Target architecture

```
tcc ask/code/implement/...        (CLI commands, unchanged UX)
        |
   AgentLoop (serial: send -> tool_calls -> execute -> feed result -> repeat)
        |
   ProxyProvider  --OpenAI /v1/chat/completions (model=m365-copilot, tools=[...])-->  teams-copilot-proxy
        |                                                                                   |
   ToolRegistry + PermissionGate (local execution)                              Copilot channel + tool emulation + redaction
```

- **Provider seam unchanged.** `ProxyProvider` implements the existing
  `Provider` / `ChatSession` interfaces (`src/provider/types.ts`). `createSession()`
  returns a session whose `send()` performs one OpenAI request; tool orchestration is
  the loop's job, not the provider's.
- **Config.** `config.provider` defaults to `proxy`. New settings: proxy base URL
  (default `http://127.0.0.1:8000/v1`), model name (default `m365-copilot`), API key
  placeholder (`unused`), request timeout. `browser.*` / `copilot.*` settings are
  removed or ignored (legacy-tolerated).

## Loop protocol (OpenAI tool-calling)

1. Build request: `messages` = [minimal system?] + prior turns + current user turn
   (with workspace/rules/context inlined per Q2), `tools` = generated from registry,
   `model` = `m365-copilot`.
2. Send to proxy `/v1/chat/completions`.
3. If response `finish_reason == "tool_calls"`: for the single returned call, validate
   args (`validateArgs`), run through `PermissionGate`, execute via `ToolRegistry`,
   append an OpenAI `role:"tool"` result message, go to 1.
4. If response is `TOOL_FAILURE_SENTINEL`: count a failure, feed the error text back;
   after N consecutive failures, stop with a clear message.
5. If plain assistant text (no tool call): treat as the final answer.

Single tool call per turn (proxy constraint); no parallel tool calling.

## Out of scope / unchanged

- Local tool implementations (read/write/edit/run-command/grep/glob/git) and their
  path-safety and risk levels.
- `PermissionGate` (allowlist / yolo / ask) and YOLO opt-in.
- Command surface: `ask`, `review`, `prd`, `arch`, `tasks`, `repl`, `code`,
  `implement` — behavior preserved; only the model channel underneath changes.
- The proxy itself (no proxy code changes required by this refactor; B1 relies on the
  already-shipped suppression behavior).

## Acceptance criteria

- `createProvider` returns a `proxy` provider that talks OpenAI Chat Completions to the
  proxy; `copilot-web` and the browser/session machinery are deleted; build and unit
  tests are green.
- Local tools are advertised as OpenAI `tools` generated from `ToolRegistry`; a
  returned `tool_calls` is validated, permission-checked, executed, and fed back as a
  `role:"tool"` message.
- `TOOL_FAILURE_SENTINEL` is handled as a failure (not a final answer).
- No CLI-side outbound redaction remains; `<<<TOOL>>>` ToolProtocol is removed.
- Workspace/rules/context reach Copilot via the user turn, not the system role.

## Open items (need live runs / user)

- Real end-to-end run against a signed-in proxy (multi-turn `code`/`implement`) — not
  yet performed.
- `limit`/context budget calibration against a live tenant (placeholder today).
- Exact N for consecutive-failure termination — pick a small default (e.g. 3) and
  confirm during live runs.
