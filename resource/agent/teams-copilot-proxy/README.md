# Teams Copilot Proxy

Use Microsoft 365 Copilot through OpenAI-compatible clients, local scripts, and coding tools.

This project runs a local FastAPI proxy that talks to the same `substrate.office.com` WebSocket API used by the M365 Copilot web UI, then exposes it as OpenAI-style HTTP endpoints.

No Azure app registration. No admin consent. Sign in with your normal M365 Copilot browser session.

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start)
- [Connect a Client](#connect-a-client)
  - [OpenCode](#opencode)
  - [Codex CLI](#codex-cli)
- [Persistent Sessions](#persistent-sessions)
- [Examples](#examples)
- [Token Management](#token-management)
  - [Refresh](#refresh)
  - [Manual Fallback](#manual-fallback)
  - [Health](#health)
- [API Endpoints](#api-endpoints)
- [Environment Variables](#environment-variables)
- [Security Notes](#security-notes)
- [Limitations](#limitations)
- [Token Automation Details](#token-automation-details)
- [License](#license)

## Features

- Use M365 Copilot from OpenAI-compatible clients
- Works with your existing signed-in Copilot web session
- Runs locally on `127.0.0.1` by default
- Auto-captures and refreshes the short-lived browser token
- Supports persistent Copilot sessions across turns
- Supports OpenAI Chat Completions and OpenAI Responses style requests
- Emulated tool calling on `/v1/chat/completions`, so agentic clients like OpenCode can read files, run commands, and edit code

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

## Connect a Client

Use these settings for any OpenAI-compatible client:

| Setting | Value |
|---|---|
| Base URL | `http://127.0.0.1:8000/v1` |
| API Key | `unused` |
| Model | `m365-copilot` |
| Persistent model | `m365-copilot:persist` |

### OpenCode

Recommended: drop a project-level `opencode.json` in your repo root. It declares the proxy as a custom provider with `tool_call: true`, which is **required** — without it OpenCode will not send tool definitions and the agent loop cannot run:

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
            "context": 128000,
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

`limit.context` (`128000`) is a placeholder; Copilot's real input limit depends on your tenant and should be measured against a live session, then backfilled here.

For a quick non-agentic chat setup instead, point OpenCode's built-in OpenAI provider at the proxy:

```bat
set OPENAI_BASE_URL=http://127.0.0.1:8000
set OPENAI_API_KEY=unused
opencode
```

For persistent Copilot-side conversation memory, use the model `m365-copilot:persist`.

**Tool calling:** when OpenCode sends `tools`, the proxy injects the tool list into the prompt, asks Copilot to answer with a single fenced ```tool_call JSON block, and translates it back into standard OpenAI `tool_calls`. Tools are executed locally by OpenCode; Copilot never touches your files directly. One tool call per turn; malformed tool replies are re-asked (see `M365_TOOL_CORRECTION_RETRIES`) and, if they still cannot be parsed, the proxy returns a stable Failure Sentinel instead of leaking raw model text. Note: when `tools` is present, streaming responses are buffered and delivered at once.

**System prompt on tool turns:** OpenCode's built-in system prompt is written for native function calling and makes the Copilot browser channel refuse in prose ("I can't access your local files") instead of emitting a tool call. So when `tools` are present the proxy drops the client system prompt and lets the tool protocol be the only authoritative instruction (`M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS`, default on). To restore forwarding the full system prompt, set `M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS=false`.

For a ready-to-use project-level config and the full loop mapping, see [examples/opencode.json](examples/opencode.json) and [docs/opencode-integration.md](docs/opencode-integration.md).

### Codex CLI

Tool calling works with Codex CLI, but only over the Chat Completions wire API. This proxy emulates tool calls on `/v1/chat/completions` only; its `/v1/responses` endpoint returns plain text with no tool support. Since Codex defaults to the Responses API, you must set `wire_api = "chat"` in its provider config (base URL `http://127.0.0.1:8000/v1`, any API key) so tools are routed through the tool-capable endpoint. Left on the default Responses API, Codex gets no tool calling.

Add a custom provider to `%USERPROFILE%\.codex\config.toml`:

```toml
[model_providers.teams-copilot]
name = "Teams Copilot Proxy"
base_url = "http://127.0.0.1:8000/v1"
wire_api = "chat"
env_key = "TEAMS_COPILOT_API_KEY"

[profiles.m365]
model = "m365-copilot"
model_provider = "teams-copilot"
```

`env_key` names the environment variable Codex reads the API key from; the proxy ignores its value, so set it to any non-empty placeholder (e.g. `set TEAMS_COPILOT_API_KEY=unused`). Then run `codex --profile m365`.

## Persistent Sessions

By default, requests are stateless from the Copilot side.

To reuse the same Copilot conversation across turns, send a stable header:

```http
X-M365-Session-Id: my-work-session
```

Or use the model suffix:

```text
m365-copilot:persist
```

Header mode is better when your client supports custom headers, because each workspace or coding-agent session can choose its own id. If your client only lets you change the model name, use `m365-copilot:persist`.

If a client uses `m365-copilot:persist` without sending a `user` field, all requests share one default persistent session until the proxy restarts.

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
| `POST /v1/chat/completions` | OpenAI Chat Completions, streaming supported |
| `POST /v1/responses` | OpenAI Responses API, streaming supported |

## Environment Variables

Most users only need `.env` after the proxy captures a token.

| Variable | Default | Description |
|---|---|---|
| `M365_ACCESS_TOKEN` | optional at startup | Browser WebSocket token. If missing, startup capture can fill `.env`. |
| `M365_TIME_ZONE` | `Asia/Tokyo` | Optional. Time zone sent to Copilot. Usually no need to set this if `Asia/Tokyo` is correct. |
| `M365_MODEL_ALIAS` | `m365-copilot` | Optional. Model name returned by `/v1/models`. Usually no need to change this. |
| `M365_MAX_TRANSCRIPT_CHARS` | `200000` | Optional. Safety-net character budget for the flattened prior-conversation transcript; whole turn units are dropped oldest-first and a tool call is never split from its result. OpenCode is the primary context bounder. |
| `M365_TOOL_CORRECTION_RETRIES` | `1` | Optional. Number of correction attempts when Copilot returns a malformed tool call. The final attempt uses a stricter reminder; after all attempts fail the proxy returns the Failure Sentinel with `finish_reason: stop`. |
| `M365_REDACT_OUTBOUND` | `true` | Optional. When on, scrubs secret-like strings (tokens, API keys, private keys, `KEY=value` env secrets) from everything sent upstream to Copilot, replacing them with `[REDACTED]`. Only affects outbound content, not the client response. |
| `M365_SUPPRESS_SYSTEM_PROMPT_WITH_TOOLS` | `true` | Optional. When on, drops the client (e.g. OpenCode) system prompt on requests that carry `tools`, so the Copilot browser channel emits a tool call instead of refusing in prose. Set to `false` to forward the full system prompt on tool turns. Requests without tools are unaffected. |
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
- Tool calls are emulated via prompting on `/v1/chat/completions` only (single tool per turn, buffered streaming); `/v1/responses` does not support tools yet.
- Token usage numbers are placeholders.
- System prompts and prior conversation history are translated into plain text context.

## Token Automation Details

See [TOKEN_REFRESH.md](TOKEN_REFRESH.md) for the deeper Chrome CDP refresh notes and alternatives.

## License

Apache License 2.0. See [LICENSE](LICENSE).
