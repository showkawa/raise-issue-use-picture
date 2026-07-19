# 05 — `:persist` session-key collision under concurrency

**Problem:** Persistent sessions selected via the `m365-copilot:persist` model suffix are keyed by
the request `user` field, and fall back to a single global `model:default` key when `user` is
absent (see `app._persistent_session`). As a result, multiple concurrent conversations that use
`:persist` without an `X-M365-Session-Id` header all share one Copilot-side session and
cross-contaminate each other's context. The `X-M365-Session-Id` header is the only collision-free
persistence primitive today.

**What to build:** Make `:persist` safe (or explicitly refuse to silently share state) so that two
independent conversations never land on the same Copilot session by accident.

**Blocked by:** None — can start immediately.

**Status:** open

- [ ] `:persist` without an explicit session id no longer collapses to a single shared global key; concurrent conversations get isolated sessions (e.g. derive a stable per-connection/per-conversation key, or require an explicit id and error clearly otherwise).
- [ ] The `X-M365-Session-Id` header remains the primary, documented persistence primitive and takes precedence over the suffix.
- [ ] Behavior is covered at the HTTP seam: two requests representing distinct conversations using `:persist` must not observe each other's session state.
- [ ] README "Persistent Sessions" and `docs/opencode-integration.md` stay consistent with the final behavior.
