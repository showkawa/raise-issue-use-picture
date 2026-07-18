# 01 — Configurable Correction Retry + Failure Sentinel

**What to build:** When Copilot emits a malformed Tool Call Envelope, the proxy re-asks with an
escalating, stricter Correction Retry a configurable number of times. If every attempt still
fails, the `/v1/chat/completions` response is an explicit **Failure Sentinel** (recognizable
text, `finish_reason: stop`) instead of the raw malformed text — so an OpenCode Agent Loop
stops visibly rather than treating garbage as the final answer. A clean plain-text reply with
no tool_call fence still ends the turn normally (task complete) without triggering correction.
Behaviour is identical on the non-stream and buffered-with-tools stream paths. No blind retry
of an identical request.

**Blocked by:** None — can start immediately.

**Status:** ready-for-agent

- [ ] A new `M365_*`-style setting controls the correction attempt count; default preserves today's single-correction behaviour.
- [ ] The final correction attempt uses a stricter, minimal-schema reminder prompt.
- [ ] After attempts are exhausted, the response content is the documented Failure Sentinel string with `finish_reason: "stop"`; the raw model text is not passed through.
- [ ] Non-stream and stream (tools present) paths return the sentinel consistently.
- [ ] A clean plain-text reply (no tool_call fence) returns `finish_reason: "stop"` without any correction attempt.
- [ ] Tests at the HTTP seam (fake client scripted to return malformed calls for N+1 turns) assert attempt count == configured count and the sentinel result, for both non-stream and stream.
