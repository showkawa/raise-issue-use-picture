# 03 — Outbound Redaction layer

**What to build:** Before any request content leaves for Microsoft's Substrate API, the proxy
scrubs secret-like tokens (API keys, bearer tokens, private keys, `.env`-style values) from the
outbound prompt and additional context (flattened transcript, tool definitions, tool results).
This is the only safeguard on the wire to Microsoft. It is configurable and can be disabled, and
it only affects what is sent upstream — it never alters what OpenCode stores locally.

**Blocked by:** None — can start immediately.

**Status:** done

- [x] A redaction transform runs on the outbound prompt and additional context between request translation and the Substrate client, so all entry points can reuse it. (`redaction.redact_outbound`, applied in all three endpoints via `_redact_translated`)
- [x] Common secret shapes (API keys, bearer/JWT tokens, private-key blocks, `KEY=value` env secrets) are detected and replaced with a non-reversible placeholder. (`[REDACTED]`)
- [x] A `M365_*`-style setting toggles redaction on/off; default is on. (`M365_REDACT_OUTBOUND`, default true)
- [x] Redaction does not change the response returned to the client, only what is sent upstream.
- [x] Tests at the HTTP seam send a request whose transcript/tool result contains secret-like strings and assert the fake client received a scrubbed transcript with the secrets absent.
