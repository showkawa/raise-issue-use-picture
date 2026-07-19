# 03 — Proxy provider (OpenAI HTTP, no tools) + config

**What to build:** A `ProxyProvider` implementing the existing `Provider` /
`ChatSession` interfaces that talks to teams-copilot-proxy's OpenAI-compatible
`/v1/chat/completions`, sending the conversation with `model=m365-copilot` and **no**
`tools` field. Supports streaming to stdout.

**Blocked by:** None — can start immediately.

**Status:** open

- [ ] `createProvider` returns `proxy` by default; `mock` retained for tests; `copilot-web` removed.
- [ ] `ProxyProvider.createSession().send(messages)` POSTs an OpenAI chat request (no `tools`) and returns assistant text; streaming updates surface via `onUpdate`.
- [ ] Config: proxy base URL (default `http://127.0.0.1:8000/v1`), model (default `m365-copilot`), API key placeholder (`unused`), request timeout; `browser.*`/`copilot.*` are ignored if present (legacy-tolerated).
- [ ] Upstream/network errors surface a clear message (e.g. proxy not running / 502).
- [ ] Unit tests hit a fake OpenAI endpoint and assert the request body carries no `tools` and the full replayed history.
