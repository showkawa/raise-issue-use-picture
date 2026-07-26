# Teams Copilot Proxy

Give [OpenCode](https://opencode.ai) full coding capabilities backed by Microsoft 365 Copilot.

This project runs a local FastAPI proxy that talks to the same `substrate.office.com` WebSocket API used by the M365 Copilot web UI, then exposes it to OpenCode as an OpenAI-compatible `/v1/chat/completions` endpoint.

**Scope:** this proxy is built and tuned specifically for OpenCode 1.18.4. It is not intended to support other clients (e.g. Codex or Claude Code); the OpenAI Responses (`/v1/responses`) and Anthropic Messages (`/v1/messages`) endpoints have been removed.

No Azure app registration. No admin consent. Sign in with your normal M365 Copilot browser session.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Connect OpenCode](#connect-opencode)
- [Persistent Sessions](#persistent-sessions)
- [Examples](#examples)
- [Token Management](#token-management)
  - [Refresh](#refresh)
  - [Manual Fallback](#manual-fallback)
  - [Health](#health)
- [API Endpoints](#api-endpoints)
- [Monitor](#monitor)
- [Environment Variables](#environment-variables)
- [Security Notes](#security-notes)
- [Limitations](#limitations)
- [Token Automation Details](#token-automation-details)
- [License](#license)

## Features

- Drives OpenCode's agentic coding loop from M365 Copilot
- Works with your existing signed-in Copilot web session
- Runs locally on `127.0.0.1` by default
- Auto-captures and refreshes the short-lived browser token
- Supports persistent Copilot sessions across turns
- Emulated tool calling on `/v1/chat/completions`, so OpenCode can read files, run commands, and edit code
- Image/vision input: OpenCode image attachments are uploaded to the substrate and described by the model (GPT-5 / reasoning tones)
- Maps OpenAI model names to Copilot tones (`claude*` -> `Claude_Sonnet`, `gpt*` -> `Gpt_5_5_Chat`, `magic*` -> `Magic`); unknown names use the default tone
- Startup capability probe: tests candidate tones and a fenced tool probe, tiers the deployment T1 (Claude + reliable tools) or T3 (best-effort tools), cached for 24h and reported on `/healthz`
- Guard layer for tool turns: detects confabulation ("I can't access your files"), hallucinated completion, safety-filter disengagement, and upstream throttling; shares a per-request retry budget and reports honestly via an `x_m365_guard` field instead of faking tool success
- Streaming with tools: immediate HTTP 200, `: keepalive` comments while Copilot thinks, then typewriter-style chunked delivery of plain-text answers (tool calls stay atomic)
- Built-in read-only Monitor (`/monitor`): request/attempt tracing, token usage, tool-call closure stats, guard/substrate error timeline, and per-session aggregation, backed by a local SQLite file

## Quick Start

```bat
uv sync
uv run teams-copilot-proxy serve
```

The server starts at `http://127.0.0.1:8000`.

On first run, the proxy opens a dedicated Chrome window. Sign in to M365 Copilot there once. The proxy will capture the required Substrate token and write it to `.env`.

The dedicated Chrome profile is stored at:

```text
%USERPROFILE%\.teams-copilot-proxy\chrome-profile
```

If startup says it is waiting for a token, click the Copilot message box and type one character. You do not need to send the message.

Verify it works:

```bat
curl -X POST http://127.0.0.1:8000/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"m365-copilot\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in one short sentence.\"}]}"
```

## Connect OpenCode

Proxy connection settings:

| Setting | Value |
|---|---|
| Base URL | `http://127.0.0.1:8000/v1` |
| API Key | `unused` |

The model id selects the Copilot tone: ids starting with `claude`/`gpt`/`magic` map to `Claude_Sonnet`/`Gpt_5_5_Chat`/`Magic`; anything else uses `M365_DEFAULT_TONE` (or the tone chosen by the startup probe).

Recommended: drop a project-level `opencode.json` in your repo root. It declares the proxy as a custom provider with `tool_call: true`, which is **required** â€” without it OpenCode will not send tool definitions and the agent loop cannot run:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "teams-copilot": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Teams Copilot Proxy",
      "options": {
        "baseURL": "http://127.0.0.1:8000/v1",
        "apiKey": "unused"
      },
      "models": {
        "m365-copilot": {
          "name": "M365 Copilot",
          "tool_call": true,
          "reasoning": false,
          "attachment": false,
          "limit": {
            "context": 265000,
            "output": 8192
          }
        }
      }
    }
  }
}
```

Then run `opencode` in that project and pick **M365 Copilot** under the **Teams Copilot Proxy** provider. The same file lives at [examples/opencode.json](examples/opencode.json).

To apply the same provider to every project instead of per-repo, put the identical JSON in the global config file at `C:\Users\{username}\.config\opencode\opencode.json` (on macOS/Linux: `~/.config/opencode/opencode.json`). A project-level `opencode.json` in the repo root overrides the global one when both exist.

`limit.context` is set to `265000`, matching M365 Copilot's single-conversation token cap (~265k). OpenCode uses this to track context consumption and trigger auto-compaction before the conversation is truncated upstream.

The full multi-model config (Claude Sonnet plus GPT-5 chat/reasoning tones, with vision-capable models declaring `attachment: true`) lives at [examples/opencode.json](examples/opencode.json). Vision input works on the GPT-5 / reasoning tones; the Claude tone does not return an image description on this channel, so keep `attachment: false` for `claude-sonnet` and route image tasks to a GPT/reasoning model.

For persistent Copilot-side conversation memory, use a `:persist` model id (e.g. `claude-sonnet:persist`).

**Tool calling:** when OpenCode sends `tools`, the proxy injects the tool list into the prompt, asks Copilot to answer with a fenced ```tool_call JSON block, and translates it back into standard OpenAI `tool_calls`. Tools are executed locally by OpenCode; Copilot never touches your files directly. By default one tool call per turn; malformed tool replies are re-asked (see `M365_TOOL_CORRECTION_RETRIES`) and, if they still cannot be parsed, the proxy returns a stable Failure Sentinel instead of leaking raw model text. Parallel tool calls (several `tool_call` blocks in one reply, emitted as multiple OpenAI `tool_calls`) are available as an opt-in via `M365_ALLOW_PARALLEL_TOOL_CALLS=true`.

**Reasoning tones (GPT-5.x Reasoning):** [examples/opencode.json](examples/opencode.json) defaults the coding model to `gpt-5-6-reasoning` (with `gpt-5-5-chat` as `small_model` for titles/summaries). Reasoning tones tend to think in prose before acting and sometimes wrap the tool JSON in a mislabelled ```json (or unlabelled) fence, or refuse by claiming the repository is "not accessible in the workspace". Two mechanisms make them reliable for OpenCode's tool loop: (1) the parser recovers a tool call from a single mislabelled fence when no ```tool_call fence is present, tolerating leading reasoning text; (2) the confabulation guard detects sandbox / "/mnt/data" / "can't locate the repository" / "make the repository available" style refusals and re-asks for a tool call. Local reliability probes on `Gpt_5_6_Reasoning` hit tool calls on `/init` repo scans, single-file reads, and symbol greps, and drive multi-turn read/list loops without sandbox refusals.

**Sampling parameters:** OpenCode's `temperature` and `top_p` are forwarded best-effort into the substrate request's `options` object. The Copilot chat channel exposes no documented sampling controls (the tone fixes the model and decoding), so these may be silently ignored upstream; they are never faked. `top_k`, `max_tokens`, and `reasoning_effort` are parsed but not forwarded (no substrate equivalent).

When `tools` is present and `stream: true`, the proxy replies with HTTP 200 immediately, emits the assistant role chunk, sends `: keepalive` SSE comments (every `M365_STREAM_KEEPALIVE_INTERVAL_S` seconds) while the full reply is buffered and parsed, then delivers plain-text answers as typewriter-style chunks of `M365_STREAM_CHUNK_CHARS` characters. Tool calls are always sent as a single atomic chunk. If a guard fired and retries were exhausted, the final chunk carries an `x_m365_guard` field.

**System prompt on tool turns:** OpenCode's built-in system prompt asserts a competing identity ("You are OpenCode...") that trips the Copilot substrate's identity guardrail, making it refuse in prose instead of emitting a tool call. So the proxy neutralizes just those identity assertions and merges the rest of the system prompt (AGENTS.md, tool discipline, project rules) into the turn, framed as project working guidelines (`M365_SANITIZE_SYSTEM_PROMPT_WITH_TOOLS`, default on). This keeps the engineering guidance while preserving tool-call reliability. To drop the system prompt entirely on tool turns instead, set `M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS=true`.

For a ready-to-use project-level config and the full loop mapping, see [examples/opencode.json](examples/opencode.json) and [docs/opencode-integration.md](docs/opencode-integration.md).

## Persistent Sessions

By default, requests are stateless from the Copilot side, and this is the **recommended mode for agentic clients** like OpenCode: the client owns the conversation history and replays it every turn, so a stateless `m365-copilot` keeps history in a single source of truth. Persistent Copilot-side memory on top of a client that already resends history double-counts context and hits Copilot's input limit sooner â€” do not use it for tool/loop workflows.

If you do want Copilot to keep its own memory across turns (e.g. a plain chat client that does *not* resend history), opt in with the `:persist` model suffix (works on every model id, e.g. `m365-copilot:persist`, `claude-sonnet:persist`). No client-side configuration is needed: picking a `:persist` model in the model picker is enough. The session key is resolved in this order:

1. An `X-M365-Session-Id: my-work-session` header, if the client can send one (most precise; each conversation picks its own id).
2. Otherwise the request `user` field.
3. Otherwise a key derived by the proxy from the hash of the conversation's first user message, so each distinct conversation automatically gets its own persistent Copilot session.

The header only takes effect on `:persist` models; requests for plain model ids always stay stateless.

**Caveats:** the derived key relies on the first user message being resent unchanged each turn; if the client compacts or rewrites early history, the key changes and a fresh Copilot session starts (a safe degradation). Two conversations that begin with the exact same first message share a session. With no header, no `user`, and no user message at all, the request falls back to stateless (a one-time warning is logged). See [docs/tickets/05-persist-session-key-collision.md](docs/tickets/05-persist-session-key-collision.md).

## Examples

**Streaming:**

```bat
curl -N -X POST http://127.0.0.1:8000/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -d "{\"model\":\"m365-copilot\",\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}]}"
```

**Persistent session:**

```bat
curl -X POST http://127.0.0.1:8000/v1/chat/completions ^
  -H "Content-Type: application/json" ^
  -H "X-M365-Session-Id: test1" ^
  -d "{\"model\":\"m365-copilot\",\"messages\":[{\"role\":\"user\",\"content\":\"Remember this code word: sakura. Reply only OK.\"}]}"
```

## Token Management

M365 Copilot browser tokens usually expire in about 1 hour. The proxy refreshes them from the dedicated signed-in Chrome window.

### Refresh

Auto-refresh is on by default:

```bat
uv run teams-copilot-proxy serve
```

Useful controls:

```bat
uv run teams-copilot-proxy serve --refresh-before-seconds 300
uv run teams-copilot-proxy serve --no-auto-refresh
uv run teams-copilot-proxy serve --no-capture-on-start
uv run teams-copilot-proxy serve --no-launch-chrome
```

You can also press `r` in the server console to refresh the token manually.

### Manual Fallback

```bat
uv run teams-copilot-proxy set-token
```

Then paste a fresh Substrate WebSocket URL:

1. Open the signed-in M365 Copilot Chrome window.
2. Open DevTools (`F12`) -> **Network** tab.
3. Filter by `substrate`.
4. Click the WebSocket entry.
5. Go to **Headers** -> right-click the **Request URL** -> **Copy link address**.
6. Paste it into the terminal.

The command extracts `access_token` automatically and writes it to `.env`.

### Health

```bat
curl http://127.0.0.1:8000/healthz
curl http://127.0.0.1:8000/v1/token/status
```

Example:

```json
{
  "status": "ok",
  "token": {
    "valid": true,
    "expires_at": "2026-05-14T02:50:53+00:00",
    "seconds_remaining": 4200
  }
}
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /healthz` | Service health plus token status |
| `GET /v1/token/status` | Token validity, expiry time, and seconds remaining |
| `GET /v1/models` | OpenAI-compatible model list |
| `POST /v1/chat/completions` | OpenAI Chat Completions (the endpoint OpenCode uses), streaming and tool calling supported |
| `GET /monitor` | Read-only monitoring dashboard (static page; data calls need the Bearer token) |
| `GET /monitor/api/summary` | Aggregate counters: requests, tokens, error/guard rates, tone breakdown |
| `GET /monitor/api/requests` | Recent requests (`?limit=`, `?session=`) |
| `GET /monitor/api/requests/{id}` | One request with its full attempt chain |
| `GET /monitor/api/tools` | Tool-call ranking with closure status and error rate |
| `GET /monitor/api/errors` | Guard and substrate error timeline (newest first) |
| `GET /monitor/api/sessions/{key}` | Per-session totals plus request/tool/event streams |

`GET /healthz` also reports the startup probe result when available, e.g. `"capability": {"tier": "T1", "tone": "Claude_Sonnet", ...}`.

## Monitor

The proxy ships a self-hosted monitor for diagnosing OpenCode instability (fake tool completions, guard retries, throttling, slow streams) without any external service. Every `/v1/chat/completions` request is recorded to a local SQLite file (WAL mode) through a bounded in-process queue — if the queue is full events are dropped with a warning; monitoring can never block or fail a chat request.

What is recorded:

- **Requests:** model, tone, session key, stream flag, estimated token usage, duration, final status (`ok` / `guard` / `error`).
- **Attempt chain:** every substrate round trip inside one request (original reply → guard trigger → correction retry → final outcome) with per-attempt duration and guard type.
- **Tool closure:** tool_calls the model emits are classified (builtin / mcp / skill / task / todowrite / webfetch) and paired with the `Tool result` OpenCode sends on the next turn — only an error flag and byte count, never the result body.
- **Stream health:** first-chunk latency, chunk count, average interval, and `[DONE]` completeness as aggregates (no per-chunk rows).
- **Error timeline:** guard hits, throttling, disengagement, and other upstream failures.

Capture policy (`M365_MONITOR_CAPTURE`): `failures` (default) keeps redacted prompt/reply excerpts (~2 KB each) only for failed or guard-triggered requests; `all` keeps them for every request; `off` stores metadata only. Rows older than `M365_MONITOR_RETENTION_DAYS` (default 30) are cleaned up automatically.

Open `http://127.0.0.1:8000/monitor` for the read-only dashboard (Summary / Requests with attempt-chain drill-down / Errors). The page asks for the Bearer token once and keeps it in `localStorage`; the token is `M365_MONITOR_TOKEN` if set, otherwise the current `M365_ACCESS_TOKEN`. Sessions are grouped by an `x-session-id` request header when present, otherwise by a hash of the conversation's first user message.

## Environment Variables

Most users only need `.env` after the proxy captures a token.

| Variable | Default | Description |
|---|---|---|
| `M365_ACCESS_TOKEN` | optional at startup | Browser WebSocket token. If missing, startup capture can fill `.env`. |
| `M365_TIME_ZONE` | `Asia/Tokyo` | Optional. Time zone sent to Copilot. Usually no need to set this if `Asia/Tokyo` is correct. |
| `M365_MODEL_ALIAS` | `m365-copilot` | Optional. Model name returned by `/v1/models`. Usually no need to change this. |
| `M365_DEFAULT_TONE` | `Claude_Sonnet` | Optional. Copilot tone used when the request model name does not start with `claude`/`gpt`/`magic`. Overridden by the startup probe result when the probe runs. |
| `M365_STARTUP_PROBE` | `true` | Optional. Probe candidate tones and run a fenced tool probe at startup to tier the deployment T1/T3. Skipped when no token is present. Set to `false` to disable. |
| `M365_PROBE_CACHE_PATH` | `.probe_cache.json` | Optional. Where the probe result is cached. |
| `M365_PROBE_TTL_SECONDS` | `86400` | Optional. Probe cache lifetime; the probe re-runs after this. |
| `M365_STREAM_KEEPALIVE_INTERVAL_S` | `15` | Optional. Interval between `: keepalive` SSE comments while a tool-carrying streaming request is being resolved. `0` disables keepalives. |
| `M365_STREAM_CHUNK_CHARS` | `24` | Optional. Typewriter chunk size for plain-text answers on tool-carrying streaming requests. `0` sends the whole text in one chunk. |
| `M365_STREAM_CHUNK_DELAY_MS` | `0` | Optional. Delay between typewriter chunks. Default `0` adds no extra latency. |
| `M365_MAX_TRANSCRIPT_CHARS` | `200000` | Optional. Safety-net character budget for the flattened prior-conversation transcript; whole turn units are dropped oldest-first and a tool call is never split from its result. OpenCode is the primary context bounder. |
| `M365_TOOL_CORRECTION_RETRIES` | `1` | Optional. Shared per-request retry budget (capped at 2) for all tool-turn guards: malformed tool calls, confabulation, hallucinated completion, and safety-filter disengagement. The final correction attempt uses a stricter reminder; after the budget is exhausted the proxy reports honestly (Failure Sentinel or the flagged text) with an `x_m365_guard` field. Upstream HTTP 429 is returned as a standard 429 with `Retry-After`. |
| `M365_REDACT_OUTBOUND` | `true` | Optional. When on, scrubs secret-like strings (tokens, API keys, private keys, `KEY=value` env secrets) from everything sent upstream to Copilot, replacing them with `[REDACTED]`. Only affects outbound content, not the client response. |
| `M365_SANITIZE_SYSTEM_PROMPT_WITH_TOOLS` | `true` | Optional. When on, neutralizes OpenCode's competing-identity assertions and merges the rest of the system prompt into tool-turn requests, so the Copilot channel keeps the engineering guidance while still emitting tool calls. Requests without tools are unaffected. |
| `M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS` | `false` | Optional. When on, drops the OpenCode system prompt entirely on requests that carry `tools`. Superseded by the sanitize-merge behavior above; leave off unless you specifically want the old drop-everything behavior. |
| `M365_ALLOW_PARALLEL_TOOL_CALLS` | `false` | Optional. When on, the tool protocol permits several `tool_call` blocks in one reply and the proxy emits them all as OpenAI `tool_calls`. Default off keeps the single-tool-per-turn path, which is the most reliable on the Claude tone. |
| `M365_MONITOR_ENABLED` | `true` | Optional. Turns the built-in monitor on/off. When off, `/monitor` endpoints return 404 and nothing is recorded. |
| `M365_MONITOR_DB_PATH` | `monitor.db` | Optional. SQLite file for monitor data (WAL mode, single file plus `-wal`/`-shm`). |
| `M365_MONITOR_CAPTURE` | `failures` | Optional. Content capture policy: `off` (metadata only), `failures` (excerpts only for failed/guard-triggered requests), `all`. |
| `M365_MONITOR_RETENTION_DAYS` | `30` | Optional. Monitor rows older than this are deleted automatically. |
| `M365_MONITOR_TOKEN` | unset | Optional. Separate Bearer token for `/monitor/api/*`; falls back to `M365_ACCESS_TOKEN` when empty. |
| `M365_PROXY` | unset | Optional. HTTP proxy URL (e.g. `http://127.0.0.1:7890`) for the outbound Substrate WebSocket. Needed when the machine reaches the internet through a local proxy, because the system proxy setting is not applied to the WebSocket automatically. |

## Security Notes

- The proxy listens on `127.0.0.1` by default.
- The browser token is stored locally in `.env`.
- `.env`, `.venv/`, and Python cache files are ignored by Git.
- The proxy does not send your token to any external service besides Microsoft 365 Copilot's own `substrate.office.com` endpoint.
- Anyone who can read your `.env` can use the token until it expires. Treat it like a secret.

## Limitations

- This is an unofficial local proxy over the browser-facing M365 Copilot API.
- Token refresh depends on a signed-in Chrome profile.
- Built for OpenCode 1.18.4 only; other clients (Codex, Claude Code) are not supported.
- Tool calls are emulated via prompting on `/v1/chat/completions` (single tool per turn by default, or several with `M365_ALLOW_PARALLEL_TOOL_CALLS`; with tools the body is buffered upstream, then streamed to the client as typewriter chunks).
- Sampling params (`temperature`/`top_p`) are forwarded best-effort but the substrate chat channel may ignore them; `top_k`/`max_tokens`/`reasoning_effort` have no substrate equivalent.
- Guard detection is heuristic; on T3 tiers (no Claude tone) tool calling is best-effort and unreliable.
- Token usage numbers are local estimates (~4 chars/token), not real counts; they only need to be roughly proportional to drive OpenCode's context tracking against the ~265k conversation cap.
- Image/vision works on GPT-5 / reasoning tones; the Claude tone does not describe images on this channel.
- System prompts and prior conversation history are translated into plain text context.

## Token Automation Details

See [TOKEN_REFRESH.md](TOKEN_REFRESH.md) for the deeper Chrome CDP refresh notes and alternatives.

## License

Apache License 2.0. See [LICENSE](LICENSE).
