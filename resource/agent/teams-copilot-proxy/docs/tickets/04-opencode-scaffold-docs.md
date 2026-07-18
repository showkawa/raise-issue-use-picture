# 04 — opencode.json scaffold + integration docs

**What to build:** Ship a ready-to-use project-level `opencode.json` example and document how the
OpenCode Agent Loop maps onto the proxy, so a developer can drop the config into a repo and start
a Copilot-backed session without reverse-engineering the code. Docs cover the Model Endpoint
config (`@ai-sdk/openai-compatible`, `baseURL`, dummy `apiKey`, model `m365-copilot` with
`tool_call: true` and the `limit.context` placeholder), the Failure Sentinel string, and the
correction-count and redaction settings.

**Blocked by:** 01 (Failure Sentinel string + correction setting), 02 (truncation behaviour to
document), 03 (redaction setting name).

**Status:** ready-for-agent

- [ ] A committed example `opencode.json` parses and declares `tool_call: true` for the `m365-copilot` model.
- [ ] Docs explain the loop mapping: OpenCode owns the loop; Copilot is the model; one tool call per turn; Stateless Replay.
- [ ] Docs document the Failure Sentinel string and the correction-count setting (from 01).
- [ ] Docs document the Transcript Budget as a safety net and Turn-Aware behaviour (from 02).
- [ ] Docs document the Outbound Redaction setting and default (from 03).
- [ ] Docs note that `limit.context` (128k) is a placeholder pending real-tenant calibration.
