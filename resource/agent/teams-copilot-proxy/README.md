# Teams Copilot Proxy

Give [OpenCode](https://opencode.ai) full coding capabilities backed by Microsoft 365 Copilot.

This project runs a local FastAPI proxy that talks to the same `substrate.office.com` WebSocket API used by the M365 Copilot web UI, then exposes it to OpenCode as an OpenAI-compatible `/v1/chat/completions` endpoint.

**Scope:** this proxy is built and tuned for OpenCode 1.18.x (validated with 1.18.7). It is not intended to support other clients (e.g. Codex or Claude Code); the OpenAI Responses (`/v1/responses`) and Anthropic Messages (`/v1/messages`) endpoints have been removed.

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
  - [Database Schema](#database-schema)
- [Environment Variables](#environment-variables)
- [Security Notes](#security-notes)
- [Limitations](#limitations)
- [Token Automation Details](#token-automation-details)
- [License](#license)

## Features

- Drives OpenCode's agentic coding loop from M365 Copilot
- Works with your existing signed-in Copilot web session
- Runs locally on `127.0.0.1` by default
- Automatically obtains and refreshes the short-lived substrate token through OAuth PKCE or Chrome CDP capture
- Supports persistent Copilot sessions across turns
- Emulated tool calling on `/v1/chat/completions`, so OpenCode can read files, run commands, and edit code
- Image/vision input: OpenCode image attachments are uploaded to the substrate and described by the model (GPT-5 / reasoning tones)
- Maps OpenAI model ids to Copilot tones: an exact tone id (e.g. `gpt-5-6-reasoning`, `gpt-5-5-chat`, `claude-sonnet`, `claude-sonnet-reasoning`) routes to that tone; dotted ids (`gpt-5.5`, `gpt-5.6-reasoning`) and bare aliases (`gpt-5-5`, `claude`, `quick`, `think-deeper`) are normalized to the same catalog; otherwise the `claude*`/`gpt*`/`magic*` prefix picks `Claude_Sonnet`/`Gpt_5_5_Chat`/`Magic`; anything else uses the default tone
- Startup capability probe: tests candidate tones and a fenced tool probe, tiers the deployment T1 (Claude + reliable tools) or T3 (best-effort tools), cached for 24h and reported on `/healthz`
- Guard layer for tool turns: detects confabulation ("I can't access your files"), hallucinated completion, hosted-file links, replies truncated mid-tool-call, safety-filter disengagement, and upstream throttling; each failure mode has its own retry budget and reports honestly via an `x_m365_guard` field instead of faking tool success
- Streaming with tools: immediate HTTP 200, `: keepalive` comments while Copilot thinks, then typewriter-style chunked delivery of plain-text answers (tool calls stay atomic)
- Built-in read-only Monitor (`/monitor`): request/attempt tracing, token usage, tool-call closure stats, per-`planning_mode` tool-planning efficiency (`single` vs the opt-in two-phase `router`), guard/substrate error timeline, and per-session aggregation, backed by a local SQLite file

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

The model id selects the Copilot tone. The full routable catalog (also advertised by `GET /v1/models`):

| Model id (canonical) | Also accepted as | Copilot tone | Tools | Vision |
|---|---|---|---|---|
| `gpt-5-5-chat` | `gpt-5.5`, `gpt-5-5` | `Gpt_5_5_Chat` | yes | yes |
| `gpt-5-5-reasoning` | `gpt-5.5-reasoning` | `Gpt_5_5_Reasoning` | yes | yes |
| `gpt-5-6-reasoning` | `gpt-5.6-reasoning`, `gpt-5-6` | `Gpt_5_6_Reasoning` | yes | yes |
| `claude-sonnet` | `claude` | `Claude_Sonnet` | yes | no |
| `claude-sonnet-reasoning` | — | `Claude_Sonnet_Reasoning` | yes | no |
| `gpt-5-2-chat` / `gpt-5-2-reasoning` | `gpt-5.2` / `gpt-5.2-reasoning` | `Gpt_5_2_*` | yes | yes |
| `gpt-5-3-chat` | `gpt-5.3` | `Gpt_5_3_Chat` | yes | yes |
| `gpt-5-4-chat` / `gpt-5-4-reasoning` | `gpt-5.4` / `gpt-5.4-reasoning` | `Gpt_5_4_*` | yes | yes |
| `gpt-quick` / `gpt-reasoning` | `quick` / `think-deeper` | `Gpt_Quick` / `Gpt_Reasoning` | yes | yes |

Dots are normalized to hyphens, a bare GPT id routes to its chat sibling (`gpt-5-6` has no chat sibling and routes to the reasoning tone), and every model accepts the `reasoning_effort` request field or a `-none`/`-minimal`/`-low`/`-medium`/`-high`/`-xhigh` id suffix — `medium`/`high`/`xhigh` upgrade a chat tone to its reasoning sibling unless the id already spells the tone out (`gpt-5-5-chat` stays on chat), while explicit `*-reasoning` ids are never downgraded. Ids not in the catalog fall back to the `claude`/`gpt`/`magic` prefix rules, then to `M365_DEFAULT_TONE` (or the tone chosen by the startup probe). Whether a given tone actually works depends on your tenant; the startup probe tests the main ones and `/healthz` reports the result.

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

**Tool calling:** when OpenCode sends `tools`, the proxy injects the tool list into the prompt, asks Copilot to answer with a fenced ```tool_call JSON block, and translates it back into standard OpenAI `tool_calls`. Tools are executed locally by OpenCode; Copilot never touches your files directly. Malformed tool replies are re-asked (see `M365_TOOL_CORRECTION_RETRIES`) and, if they still cannot be parsed, the proxy returns a stable Failure Sentinel instead of leaking raw model text. The retry allowance is **per failure mode**, not shared: a redirect guard (`hosted_file_link`, `confabulation`, `hallucinated_completion`), a malformed block (`tool_parse_failure`), a reply cut off mid-argument (`tool_output_truncated`) and a safety-filter `disengaged` each get their own `M365_TOOL_CORRECTION_RETRIES` budget, so an early redirect can no longer consume the retry a later failure needs. A truncated call — typically a whole file inlined into one `write`/`apply_patch` argument that hit the upstream output limit — is never re-asked with the same payload; the retry tells the model to write a smaller portion and continue in later turns. Parallel tool calls (several `tool_call` blocks in one reply, emitted as multiple OpenAI `tool_calls`) are enabled per tone via `M365_PARALLEL_TOOL_TONES` (default `Claude_Sonnet`, the tone that reliably emits them) and can be forced on for every tone with `M365_ALLOW_PARALLEL_TOOL_CALLS=true`; the GPT-5.x reasoning tones keep the single-tool-per-turn path by default because they tend to refuse or disengage when asked for several at once.

**Tool planning mode (`M365_TOOL_PLANNING_MODE`, ported opt-in from [HEXUXIU/M365-Copilot2API](https://github.com/HEXUXIU/M365-Copilot2API)):** `single` (default) resolves the tool call and/or the answer in one model turn. `router` runs a dedicated tool-*selection* turn first — the model must reply with the `tool_call` block(s) or the explicit `NO_TOOL_NEEDED` sentinel and nothing else — and only makes a second turn for the natural-language answer when no tool is needed. This stops a reasoning tone from answering in prose while silently dropping the call it needed, at the cost of one extra substrate round trip on no-tool turns. The selection turn reuses the full single-mode engine (shell-fence recovery, in-reply de-duplication, JSON-schema pre-validation, the `M365_TOOL_CORRECTION_RETRIES` repair budget, and the confabulation/disengagement guards). Keep the default `single` and use the Monitor's per-`planning_mode` metrics to decide whether `router` is worth enabling on your traffic.

**Reasoning tones (GPT-5.x Reasoning):** [examples/opencode.json](examples/opencode.json) defaults the coding model to `gpt-5-6-reasoning` (with `gpt-5-5-chat` as `small_model` for titles/summaries). Reasoning tones tend to think in prose before acting and sometimes wrap the tool JSON in a mislabelled ```json (or unlabelled) fence, or refuse by claiming the repository is "not accessible in the workspace". Two mechanisms make them reliable for OpenCode's tool loop: (1) the parser recovers a tool call from a single mislabelled fence when no ```tool_call fence is present, tolerating leading reasoning text; (2) the confabulation guard detects sandbox / "/mnt/data" / "can't locate the repository" / "make the repository available" style refusals and re-asks for a tool call. Local reliability probes on `Gpt_5_6_Reasoning` hit tool calls on `/init` repo scans, single-file reads, and symbol greps, and drive multi-turn read/list loops without sandbox refusals.

**Sampling parameters:** OpenCode's `temperature` and `top_p` are forwarded best-effort into the substrate request's `options` object. The Copilot chat channel exposes no documented sampling controls (the tone fixes the model and decoding), so these may be silently ignored upstream; they are never faked. `top_k` and `max_tokens` are parsed but not forwarded (no substrate equivalent).

**Reasoning effort:** the substrate has no effort knob — the tone fixes the model and its reasoning depth — so `reasoning_effort` is emulated by tone routing: `medium`/`high`/`xhigh` upgrade a chat tone to its reasoning sibling (`gpt-5-5` → `Gpt_5_5_Reasoning`, an unlisted id falling back to `M365_DEFAULT_TONE=Claude_Sonnet` → `Claude_Sonnet_Reasoning`); `none`/`minimal`/`low` keep the chat tone; explicit `*-reasoning` model ids are never downgraded. A model id that names its tone (`gpt-5-5-chat`, `claude-sonnet`, `gpt-quick`) is a deliberate choice and is **never** promoted by the request's effort field — OpenCode sends `reasoning_effort=medium` on every turn, which would otherwise silently swap the model you picked. The effort can also be given as a model-id suffix (e.g. `gpt-5-5-chat-high`); the request's `reasoning_effort` field wins when both are present. Beyond the probed tones, the extended catalog also routes `gpt-5-2-chat`, `gpt-5-2-reasoning`, `gpt-5-3-chat`, `gpt-5-4-chat`, `gpt-5-4-reasoning`, `gpt-quick`, `gpt-reasoning`, and `claude-sonnet-reasoning` by model id (not probed at startup; availability depends on your tenant).

**Tool-argument schema pre-validation:** parsed tool calls are checked against the tool's JSON schema (required keys, top-level property types, `additionalProperties: false`) before being returned to OpenCode; violations are re-asked on the correction-retry budget instead of wasting a full client round trip. The check is deliberately shallow so a rejection is always a genuine schema violation.

**Agent evidence ledger:** before each turn the proxy reconstructs the tool-call history from the transcript and injects a compact `EVIDENCE_LEDGER` (which calls already completed, and whether each failed) plus a "do not re-issue a completed call" instruction, so the model builds on prior results instead of repeating work. When it detects a loop it adds a strategy-change nudge: an identical call issued more than once, or — more strongly — the same call failing repeatedly with the same error ("change the arguments or approach instead of retrying unchanged"). This supersedes the earlier repeated-failure-only hint. (Design ported selectively from [HEXUXIU/M365-Copilot2API](https://github.com/HEXUXIU/M365-Copilot2API), MIT.)

**Shell-fence recovery:** reasoning tones sometimes emit a shell command as a ` ```bash ` / `sh` / `shell` / `powershell` / `cmd` code fence (or a bare `{"command": ...}` object) instead of the `tool_call` envelope. When a shell-type tool (`bash`/`sh`/…) is actually in the tool set, the proxy recovers such a block as a real call for that tool rather than leaking it to OpenCode as prose. It is deliberately conservative — it only fires for a single unambiguous block and only when a matching shell tool exists — so ordinary illustrative snippets are never turned into executions.

**In-reply de-duplication:** within a single (parallel) reply, byte-identical tool calls (same name + canonical arguments) are collapsed to one, so the client is never asked to run the exact same operation twice; at least one call is always kept.

**Web-search tool de-duplication:** Copilot already grounds answers with Bing web results, so a client-provided web-search tool (`web_search` / `websearch` / `search_web` / `bing_web_search`) usually just triggers redundant turns. By default (`M365_DEDUP_WEBSEARCH`) such tools are stripped from the tool list before the request is sent; set it to `false` to keep them.

**Structured output (JSON mode):** when the request sets `response_format` to `{"type": "json_object"}` or `{"type": "json_schema", ...}`, the proxy appends an instruction asking Copilot to reply with a single valid JSON object (and, for `json_schema`, to match the supplied schema). The substrate has no native JSON mode, so this is prompt-enforced rather than guaranteed.

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

M365 Copilot access tokens usually expire in about 1 hour. The proxy prefers OAuth PKCE refresh when a cached `.oauth_tokens.json` is available and falls back to the dedicated signed-in Chrome window.

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

### OAuth Sign-In (PKCE, browserless refresh)

Instead of scraping the browser WebSocket token, you can sign in once with the
standard Microsoft identity platform OAuth 2.0 + PKCE flow. This yields a
`refresh_token` (via the `offline_access` scope) that renews the substrate
access token automatically, with no browser kept alive. See ADR-0008 for the
protocol details and security trade-offs.

Interactive sign-in (opens a browser, then paste the redirect URL back):

```bat
uv run teams-copilot-proxy login
```

Headless / no-browser environments (device code):

```bat
uv run teams-copilot-proxy login-device
```

Both commands cache the token set to `.oauth_tokens.json` (owner-only
permissions) and write `M365_ACCESS_TOKEN` to `.env`. Once a `refresh_token` is
cached, `serve` runs an eager OAuth refresh at startup (every boot begins on a
fresh substrate token) and prefers OAuth refresh over the Chrome WebSocket
scrape in the background auto-refresh loop (falling back to Chrome if OAuth
refresh fails), so you can run `serve --no-launch-chrome`. `--no-auto-refresh`
disables both. Force a one-off refresh with:

```bat
uv run teams-copilot-proxy oauth-refresh
```

> Security: `.oauth_tokens.json` holds a long-lived `refresh_token` — treat it
> like a password. It is git-ignored; do not commit or share it.

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
| `GET /monitor` | Read-only monitoring dashboard (static page; loopback clients need no token, remote data calls need the Bearer token) |
| `GET /monitor/api/session` | Dashboard bootstrap: current substrate token status (masked, never the full value) |
| `GET /monitor/api/token` | Full current substrate token for an authenticated monitor client (dashboard "copy token" button); loopback clients may be unauthenticated when `M365_MONITOR_LOOPBACK_OPEN=true` |
| `GET /monitor/api/summary` | Aggregate counters: requests, tokens, error/guard rates, tone breakdown |
| `GET /monitor/api/requests` | Recent requests (`?limit=`, `?session=` — matches the OpenCode session id or the derived key, `?project=`, `?turn_kind=`) |
| `GET /monitor/api/requests/{id}` | One request with its full attempt chain |
| `GET /monitor/api/tools` | Tool-call ranking with closure status and error rate |
| `GET /monitor/api/tool-efficiency` | Tool-planning reliability & cost grouped by `planning_mode` (`single` vs opt-in `router`); `?since=<unix seconds>` restricts the window (e.g. to exclude stale traffic from an A/B) |
| `GET /monitor/api/guard-effectiveness` | Per-guard-type retry-recovery stats broken down by tone (hits / recovered / exhausted / recovery rate); `?since=<unix seconds>` restricts the window. Use it to target the worst guard/tone pairs. |
| `GET /monitor/api/context-pressure` | Per-request context growth: prompt tokens, `context_pct` of the M365 limit, transcript/system bytes, message and tool counts (`?limit=`) |
| `GET /monitor/api/errors` | Guard and substrate error timeline (newest first) |
| `GET /monitor/api/sessions/{key}` | Per-session totals plus request/tool/event streams |

`GET /healthz` also reports the startup probe result when available, e.g. `"capability": {"tier": "T1", "tone": "Claude_Sonnet", ...}`.

## Monitor

The proxy ships a self-hosted monitor for diagnosing OpenCode instability (fake tool completions, guard retries, throttling, slow streams) without any external service. Every `/v1/chat/completions` request is recorded to a local SQLite file (WAL mode) through a bounded in-process queue — if the queue is full events are dropped with a warning; monitoring can never block or fail a chat request.

What is recorded:

- **Requests:** model, tone, session key, stream flag, estimated token usage, duration, final status (`ok` / `guard` / `error`).
- **Attempt chain:** every substrate round trip inside one request (original reply → guard trigger → correction retry → final outcome) with per-attempt duration, guard type, the parse/schema `error_detail` that triggered a correction, and (in router mode) the `phase` (`select`/`answer`) the attempt belonged to.
- **Per-request routing context:** `reasoning_effort` and `planning_mode` are stored on every request so guard/latency regressions can be sliced by effort level and by single-vs-router path.
- **Tool closure:** tool_calls the model emits are classified (builtin / mcp / skill / task / todowrite / webfetch) and paired with the `Tool result` OpenCode sends on the next turn — only an error flag and byte count, never the result body.
- **Tool-planning telemetry:** for every request that carries `tools`, the proxy records `planning_mode` (`single` or, when `M365_TOOL_PLANNING_MODE=router`, `router`), whether the round yielded a tool call, how many attempts/corrections it took, and how often the recovery mechanisms fired — shell-fence/bare-command recovery (`shell_recovered`), in-reply de-duplication (`deduped`), and the ledger's repeated-call / repeated-failure flags. This is stored as additive columns and never changes chat behavior.
- **OpenCode request shape:** the client's real `x-session-id` and `User-Agent`, the derived `project_path` (from the stated working directory, else the common prefix of the absolute paths in the transcript), `turn_kind` (`tool` / `title` / `summary` / `chat`, so agent turns are not averaged with OpenCode's title side-requests), message count, transcript/system bytes, `context_pct` of `M365_CONTEXT_LIMIT`, sampling parameters, tool count, per-flavour `tool_kinds` (`builtin:8,mcp:3`) and a `tools_fingerprint` for correlating tool-list changes with behaviour changes.
- **Injected context parts:** which components the proxy actually added to the turn (`tool_protocol`, `system_sanitized`, `transcript`, `transcript_truncated`, `evidence_ledger`, `json_mode`, `router_select`, `correction:<guard>`, …) — recorded per request and per attempt, so a bad turn no longer has to be reverse-engineered from the reply.
- **Upstream round-trip facts (per attempt):** substrate conversation id and client request id, sent prompt bytes, first-frame latency, frame count, the substrate `messageType`s received, reply bytes, Bing citation count, whether the stream **terminated cleanly**, and the upstream status / close reason when it did not. A truncated turn is now visible directly instead of being inferred from half-parsed JSON.
- **Stream health:** first-chunk latency, chunk count, average interval, and `[DONE]` completeness as aggregates (no per-chunk rows).
- **Error timeline:** guard hits, throttling, disengagement, and other upstream failures.

`GET /monitor/api/tool-efficiency` groups these tool-bearing requests by `planning_mode` and reports, per mode: request count, tool-call yield, average attempts and corrections, guard/error rate, p50/p95 latency, and the shell-recovery / dedup / repeated-call / repeated-failure counters. Requests land under `single` by default; setting `M365_TOOL_PLANNING_MODE=router` writes the same columns under `router`, so the two paths can be compared on real traffic (success, cost, latency) instead of guesswork — no schema redesign required.

Capture policy (`M365_MONITOR_CAPTURE`): `failures` (default) keeps redacted prompt/reply excerpts (~2 KB each), the raw final upstream frame (~2 KB), the head/tail of the prompt actually sent upstream (1.5 KB / 2 KB), the raw M365 reply text (~8 KB), the OpenCode request body (~16 KB), tool-call arguments (~4 KB) and the head of failed tool results (512 B) only for failed or guard-triggered requests; `all` keeps them for every request; `off` stores metadata only. Full transcripts, file contents and per-chunk timestamps are never stored, and neither are tokens or credentials. Rows older than `M365_MONITOR_RETENTION_DAYS` (default 30) are cleaned up automatically.

Open `http://127.0.0.1:8000/monitor` for the read-only dashboard (Summary — including a **Tool planning (baseline for router A/B)** table — / Requests with project, turn kind, tool count and ctx% columns plus **Request shape** and **Upstream (M365)** drill-downs / Context pressure / Errors). Loopback clients (127.0.0.1/::1) get in without typing any token (disable with `M365_MONITOR_LOOPBACK_OPEN=false`); the header shows the current substrate token (masked) with its remaining lifetime and a **copy token** button (loopback only). Non-loopback access still asks for the Bearer token once and keeps it in `localStorage`; that token is `M365_MONITOR_TOKEN` if set, otherwise the current `M365_ACCESS_TOKEN`. Sessions are grouped by an `x-session-id` request header when present, otherwise by a hash of the conversation's first user message.

### Database Schema

The monitor stores everything in a single SQLite file (`M365_MONITOR_DB_PATH`, default `monitor.db`, WAL mode). It has **4 tables**, created and migrated additively at startup, plus indexes on `requests(ts)`, `requests(session_key)`, `tool_calls(session_key, name)` and `events(ts)`.

| Table | Grain | Purpose |
|---|---|---|
| `requests` | one row per `/v1/chat/completions` request | The aggregate record: OpenCode-side identity/shape, routing decisions, usage, stream health, final outcome. |
| `attempts` | one row per substrate round trip inside a request | The retry chain (original reply → guard → correction) with the proxy→M365 and M365→proxy round-trip facts. |
| `tool_calls` | one row per tool call the model emitted | What tool was requested, with which arguments, and how the execution result came back on the next turn. |
| `events` | one row per derived error/guard event | A flat timeline for the Errors view, derived from the request record at write time. |

Columns marked **(capture)** are content excerpts, only retained per the `M365_MONITOR_CAPTURE` policy (`failures` default → kept for failed/guard requests only; `all` → always; `off` → never) and truncated with a `... [truncated N chars]` marker.

#### `requests`

| Column | Type | Description |
|---|---|---|
| `id` | TEXT PK | Request id generated by the proxy; join key for `attempts` / `tool_calls` / `events`. |
| `ts` | REAL | Unix epoch seconds when the request started. |
| `session_key` | TEXT | Monitor session identity: the `x-session-id` header when present, else a hash of the conversation's first user message. |
| `model` | TEXT | Model name the client asked for (e.g. `gpt-5-6-reasoning`). |
| `tone` | TEXT | Copilot tone the proxy actually used upstream (e.g. `Gpt_5_6_Reasoning`). |
| `stream` | INTEGER | 1 when the client requested SSE streaming. |
| `status` | TEXT | Final outcome: `ok`, `guard` (returned but a guard fired to the end) or `error`. |
| `guard` | TEXT | Guard that decided the final outcome: `confabulation`, `hosted_file_link`, `hallucinated_completion`, `disengaged`, `tool_parse_failure`, `tool_output_truncated`. NULL when clean. |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | INTEGER | Estimated token usage (heuristic, not billed numbers). |
| `duration_ms` | INTEGER | End-to-end request duration including all retries. |
| `error` | TEXT | Error message (≤512 chars) when `status='error'`. |
| `error_type` | TEXT | `bad_request`, `throttled`, `disengaged` or `upstream_error`. |
| `prompt_summary` | TEXT **(capture)** | Excerpt (~2 KB) of the flattened prompt text. |
| `reply_snippet` | TEXT **(capture)** | Excerpt (~2 KB) of the final reply text. |
| `first_chunk_ms` | INTEGER | Time to the first SSE content chunk (streaming only). |
| `chunk_count` | INTEGER | Number of SSE content chunks (NULL for non-streaming). |
| `avg_chunk_interval_ms` | INTEGER | Average gap between chunks after the first. |
| `stream_complete` | INTEGER | 1 when the stream ended with a proper `[DONE]` (NULL for non-streaming). |
| `had_tools` | INTEGER | 1 when the request carried `tools` — the denominator for tool-call reliability metrics. |
| `planning_mode` | TEXT | `single` or `router` (`M365_TOOL_PLANNING_MODE`), for A/B comparison. |
| `reasoning_effort` | TEXT | Effort tier applied to this turn. |
| `shell_recovered` | INTEGER | How many shell-fence / bare-command replies were recovered into proper tool calls. |
| `deduped` | INTEGER | How many duplicate tool calls were dropped from one reply. |
| `repeated_call` | INTEGER | 1 when the evidence ledger saw the model repeat an identical earlier call. |
| `repeated_failure` | INTEGER | 1 when the ledger saw the model repeat a call that already failed. |
| `client_session_id` | TEXT | The raw `x-session-id` header from OpenCode (NULL when absent — unlike `session_key`, never derived). |
| `client_agent` | TEXT | Client `User-Agent` (e.g. `opencode/1.18.5 ...`). Real traffic is distinguishable from test traffic here. |
| `project_path` | TEXT | Project directory being worked on: the stated working directory, else the common prefix of absolute paths in the transcript. |
| `turn_kind` | TEXT | `tool`, `title`, `summary` or `chat` — keeps OpenCode's side-requests out of agent-turn metrics. |
| `messages_count` | INTEGER | Number of messages in the incoming request. |
| `transcript_bytes` | INTEGER | Byte size of the flattened prior conversation. |
| `system_bytes` | INTEGER | Byte size of the system prompt received from OpenCode. |
| `context_pct` | REAL | Share of `M365_CONTEXT_LIMIT` consumed by this turn. |
| `tools_count` | INTEGER | Number of tool definitions sent by the client. |
| `tool_kinds` | TEXT | Per-flavour breakdown, e.g. `builtin:8,mcp:3`. |
| `tools_fingerprint` | TEXT | Stable hash of the tool list, for correlating tool-set changes with behaviour changes. |
| `temperature` / `top_p` / `max_tokens` | REAL/REAL/INTEGER | Sampling parameters requested by the client. |
| `response_format` | TEXT | `response_format.type` when the client asked for structured output. |
| `injections` | TEXT | CSV of the context parts the proxy added: `tool_protocol`, `system_sanitized`, `transcript_truncated`, `evidence_ledger`, `json_mode`, `router_select`, `correction:<guard>`, … |
| `request_body` | TEXT **(capture)** | The OpenCode request JSON (≤16 KB), serialized from the parsed model — the exact input needed to replay a failure. |

#### `attempts`

Primary key `(request_id, seq)`. One row per substrate round trip; a single request usually has one row, more when guards trigger corrections or router mode runs a select + answer turn.

| Column | Type | Description |
|---|---|---|
| `request_id` | TEXT | FK to `requests.id`. |
| `seq` | INTEGER | 1-based attempt index within the request. |
| `duration_ms` | INTEGER | Duration of this round trip. |
| `guard` | TEXT | Guard triggered by this attempt's reply (NULL when the reply was accepted). |
| `retried` | INTEGER | 1 when this attempt was followed by a correction retry. |
| `status` | TEXT | `ok` / `guard` / `error` for this attempt. |
| `text` | TEXT **(capture)** | Reply excerpt (~2 KB). |
| `error_detail` | TEXT | Short reason for a parse/schema rejection (≤300 chars), e.g. `tool_call block appears truncated`. Always stored, independent of capture level. |
| `phase` | TEXT | `select` / `answer` in router mode; NULL in single mode. |
| `injections` | TEXT | Context parts injected for this specific attempt (a correction attempt differs from the first). |
| `conversation_id` | TEXT | Substrate conversation id — for cross-checking against upstream and for session reuse. |
| `client_request_id` | TEXT | Per-round-trip request id sent to substrate. |
| `images` | INTEGER | Image annotations attached to this round trip. |
| `option_sets` | INTEGER | Number of substrate `optionsSets` flags sent (grows by one when images are attached). |
| `sent_bytes` | INTEGER | Bytes of the prompt the proxy actually sent upstream. |
| `first_frame_ms` | INTEGER | Latency to the first upstream WebSocket frame. |
| `frames` | INTEGER | Number of upstream frames received. |
| `message_types` | TEXT | CSV of substrate `messageType`s seen, e.g. `EscapeHatch,Chat,ReferencesListComplete`. |
| `reply_bytes` | INTEGER | Bytes of assembled reply text. |
| `citations` | INTEGER | Number of Bing citations in the reply. |
| `terminated_cleanly` | INTEGER | 1/0/NULL — whether the upstream stream ended properly. A truncated turn is visible here directly instead of being inferred from half-parsed JSON. |
| `upstream_status` | INTEGER | Upstream HTTP/WS status when the round trip failed. |
| `close_reason` | TEXT | WebSocket close reason when the stream did not end cleanly. |
| `final_frame` | TEXT **(capture)** | The raw last upstream frame (~2 KB). |
| `sent_head` / `sent_tail` | TEXT **(capture)** | Head/tail of the prompt sent upstream (1.5 KB / 2 KB). |
| `response_text` | TEXT **(capture)** | The raw M365 reply text (≤8 KB) exactly as handed to the tool-call parser — replay this to reproduce a parse failure. |

#### `tool_calls`

| Column | Type | Description |
|---|---|---|
| `call_id` | TEXT PK | Tool call id emitted to OpenCode. |
| `request_id` | TEXT | FK to `requests.id` — the request that produced the call. |
| `session_key` | TEXT | Session the call belongs to; used for name-based pairing when `call_id` is unavailable. |
| `ts` | REAL | Timestamp of the producing request. |
| `name` | TEXT | Tool name (e.g. `read`, `bash`, `apply_patch`). |
| `category` | TEXT | `builtin` / `mcp` / `skill` / `task` / `todowrite` / `webfetch` / `other`. |
| `args_bytes` | INTEGER | Byte size of the argument JSON. |
| `arguments` | TEXT **(capture)** | The argument JSON (≤4 KB) the model actually emitted. |
| `result_error` | INTEGER | 1 when OpenCode reported the execution as an error; NULL until the result comes back on a later turn. |
| `result_bytes` | INTEGER | Byte size of the tool result; NULL means the call is still unclosed. |
| `result_head` | TEXT **(capture)** | First 512 B of a **failed** tool result only; NULL for successful results. |
| `unclosed` | INTEGER | 1 when a later turn in the same session arrived without ever returning this call's result — the client never executed it or dropped it from the transcript. Reset to 0 if the result shows up late. |

#### `events`

Derived at write time (not emitted separately), so it never contains anything the other tables lack — it just makes the Errors timeline cheap to query.

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Autoincrement. |
| `ts` | REAL | Timestamp of the originating request. |
| `request_id` | TEXT | FK to `requests.id`. |
| `session_key` | TEXT | Session the event belongs to. |
| `type` | TEXT | `guard` (one per guard-triggering attempt), the request's `error_type` (`throttled` / `disengaged` / `bad_request` / `upstream_error`), `stream_incomplete` when a streaming request never emitted `[DONE]`, `empty_tool_result` when a tool succeeded but returned ≤48 B (a `webfetch` that fetched nothing, a zero-hit `glob`), or `tool_call_unclosed` when a call never got a result back. |
| `detail` | TEXT | Guard name (with ` (retried)` appended when a correction followed), the error message, `<tool> returned <N>B`, or `<tool> (<call_id>)`. |

The `empty_tool_result` / `tool_call_unclosed` pair covers the two silent failures that otherwise look like a healthy `status=ok` turn: the model reasoning on top of an empty tool result, and a session that simply stops at a tool call. Both are also aggregated per tool in `/monitor/api/tools` (`empty_results` / `unclosed` columns in the dashboard's Tools table).

Note: `POST /monitor/api/clear` (the dashboard's **clear db** button) truncates all four tables. Rows older than `M365_MONITOR_RETENTION_DAYS` are deleted automatically.

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
| `M365_TOOL_PLANNING_MODE` | `single` | Optional. Tool-turn strategy. `single` decides the tool call and/or answer in one model turn. `router` runs a dedicated tool-selection turn first (repair via `M365_TOOL_CORRECTION_RETRIES`), then a separate answer turn only when no tool is needed. Ported opt-in from HEXUXIU/M365-Copilot2API; keep `single` and use the Monitor's per-`planning_mode` metrics to evaluate `router` before switching. |
| `M365_TOOL_CORRECTION_RETRIES` | `1` | Optional. Shared per-request retry budget (capped at 2) for all tool-turn guards: malformed tool calls, confabulation, hallucinated completion, and safety-filter disengagement. The final correction attempt uses a stricter reminder; after the budget is exhausted the proxy reports honestly (Failure Sentinel or the flagged text) with an `x_m365_guard` field. Upstream HTTP 429 is returned as a standard 429 with `Retry-After`. |
| `M365_REDACT_OUTBOUND` | `true` | Optional. When on, scrubs secret-like strings (tokens, API keys, private keys, `KEY=value` env secrets) from everything sent upstream to Copilot, replacing them with `[REDACTED]`. Only affects outbound content, not the client response. |
| `M365_SANITIZE_SYSTEM_PROMPT_WITH_TOOLS` | `true` | Optional. When on, neutralizes OpenCode's competing-identity assertions and merges the rest of the system prompt into tool-turn requests, so the Copilot channel keeps the engineering guidance while still emitting tool calls. Requests without tools are unaffected. |
| `M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS` | `false` | Optional. When on, drops the OpenCode system prompt entirely on requests that carry `tools`. Superseded by the sanitize-merge behavior above; leave off unless you specifically want the old drop-everything behavior. |
| `M365_ALLOW_PARALLEL_TOOL_CALLS` | `false` | Optional. When on, allows several `tool_call` blocks in one reply (emitted as multiple OpenAI `tool_calls`) for **every** tone. Leave off to fall back to the per-tone `M365_PARALLEL_TOOL_TONES` allowlist. |
| `M365_PARALLEL_TOOL_TONES` | `Claude_Sonnet` | Optional. Comma-separated tones allowed to emit parallel tool calls even when `M365_ALLOW_PARALLEL_TOOL_CALLS` is off. Default enables it only for `Claude_Sonnet`, which reliably emits several at once; the GPT-5.x reasoning tones stay single-tool-per-turn. |
| `M365_DEDUP_WEBSEARCH` | `true` | Optional. When on, strips client-provided web-search tools (`web_search`/`websearch`/`search_web`/`bing_web_search`) from the tool list, since Copilot already grounds answers with Bing results. Set to `false` to keep them. |
| `M365_CONTEXT_LIMIT` | `265000` | Optional. Hard token ceiling; requests estimated to exceed M365 Copilot's ~265k single-conversation cap are rejected before hitting the substrate. |
| `M365_MONITOR_ENABLED` | `true` | Optional. Turns the built-in monitor on/off. When off, `/monitor` endpoints return 404 and nothing is recorded. |
| `M365_MONITOR_DB_PATH` | `monitor.db` | Optional. SQLite file for monitor data (WAL mode, single file plus `-wal`/`-shm`). |
| `M365_MONITOR_CAPTURE` | `failures` | Optional. Content capture policy: `off` (metadata only), `failures` (excerpts only for failed/guard-triggered requests), `all`. |
| `M365_MONITOR_RETENTION_DAYS` | `30` | Optional. Monitor rows older than this are deleted automatically. |
| `M365_MONITOR_TOKEN` | unset | Optional. Separate Bearer token for `/monitor/api/*`; falls back to `M365_ACCESS_TOKEN` when empty. |
| `M365_MONITOR_LOOPBACK_OPEN` | `true` | Optional. Loopback (127.0.0.1/::1) clients may use `/monitor` and `/monitor/api/*` without a Bearer token. Set `false` to require the token even locally. |
| `M365_THROTTLE_RETRIES` | `2` | Optional. How many times an upstream HTTP 429 is retried (exponential backoff honoring `Retry-After`, capped at 20 s per wait) before the 429 is surfaced to the client. |
| `M365_PROXY` | unset | Optional. HTTP proxy URL (e.g. `http://127.0.0.1:7890`) for the outbound Substrate WebSocket. Needed when the machine reaches the internet through a local proxy, because the system proxy setting is not applied to the WebSocket automatically. |
| `M365_OAUTH_CLIENT_ID` | Office web Copilot client | Optional. Public client used for the PKCE `login`/`login-device` flow (ADR-0008). |
| `M365_OAUTH_AUTHORITY` | `https://login.microsoftonline.com/common` | Optional. OAuth authority (multi-tenant by default). |
| `M365_OAUTH_SCOPE` | substrate sydney + `offline_access` | Optional. Requested scopes; `offline_access` is required to obtain a `refresh_token`. |
| `M365_OAUTH_REDIRECT_URI` | `.../oauth2/nativeclient` | Optional. Redirect URI for the Authorization Code flow; must be registered for the client. |
| `M365_OAUTH_CACHE_PATH` | `.oauth_tokens.json` | Optional. Where the OAuth token set (incl. long-lived `refresh_token`) is cached. Git-ignored; keep private. |

## Security Notes

- The proxy listens on `127.0.0.1` by default.
- The browser token is stored locally in `.env`.
- `.env`, `.venv/`, and Python cache files are ignored by Git.
- The proxy sends OAuth authorization and token requests to Microsoft identity endpoints when using `login`, `login-device`, or refresh; it sends the resulting access token to Microsoft 365 Copilot's `substrate.office.com` endpoint. No third-party service is required by the proxy.
- Anyone who can read your `.env` can use the token until it expires. Treat it like a secret.

## Limitations

- This is an unofficial local proxy over the browser-facing M365 Copilot API.
- Token refresh normally uses OAuth PKCE; Chrome CDP capture remains the fallback when OAuth is unavailable.
- Built for OpenCode 1.18.x (validated with 1.18.7); other clients (Codex, Claude Code) are not supported.
- Tool calls are emulated via prompting on `/v1/chat/completions` (parallel calls per-tone via `M365_PARALLEL_TOOL_TONES`, default `Claude_Sonnet`, or forced on for all tones with `M365_ALLOW_PARALLEL_TOOL_CALLS`; the reasoning tones stay single-tool-per-turn; with tools the body is buffered upstream, then streamed to the client as typewriter chunks).
- Sampling params (`temperature`/`top_p`) are forwarded best-effort but the substrate chat channel may ignore them; `top_k`/`max_tokens` have no substrate equivalent, and `reasoning_effort` is emulated by tone routing (no true per-request effort control).
- Guard detection is heuristic; on T3 tiers (no Claude tone) tool calling is best-effort and unreliable.
- Token usage numbers are local estimates (~4 chars/token), not real counts; they only need to be roughly proportional to drive OpenCode's context tracking against the ~265k conversation cap.
- Image/vision works on GPT-5 / reasoning tones; the Claude tone does not describe images on this channel.
- System prompts and prior conversation history are translated into plain text context.

## Token Automation Details

See [TOKEN_REFRESH.md](TOKEN_REFRESH.md) for the deeper Chrome CDP refresh notes and alternatives.

## License

Apache License 2.0. See [LICENSE](LICENSE).
