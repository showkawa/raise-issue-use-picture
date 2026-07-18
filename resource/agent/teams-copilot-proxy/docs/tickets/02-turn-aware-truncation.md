# 02 — Turn-Aware Truncation of the Transcript Budget

**What to build:** When the flattened Stateless Replay transcript exceeds the Transcript Budget,
the proxy drops whole turn units and never separates a Tool Call Envelope from its matching
tool result. The Transcript Budget stays a last-resort safety net (OpenCode remains the primary
context bounder); this ticket only makes the safety-net truncation protocol-safe.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] Truncation drops content in whole-turn units, oldest first.
- [ ] A retained Tool Call Envelope always keeps its paired tool result (and vice versa); the pair is never split across the budget cut.
- [ ] The transcript handed to the Copilot client stays within the configured Transcript Budget.
- [ ] Existing non-tool truncation behaviour is preserved for histories without tool calls.
- [ ] Tests at the HTTP seam feed an oversized interleaved history and assert the upstream transcript is within budget and every retained envelope has its result.
