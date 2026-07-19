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

**Status:** done

- [x] `:persist` without an explicit session id no longer collapses to a single shared global key; it now falls back to a stateless request (with a one-time warning) instead of sharing one Copilot session. (`app._persistent_session`)
- [x] The `X-M365-Session-Id` header remains the primary, documented persistence primitive and takes precedence over the suffix; a `user` field still keys a per-user session.
- [x] Behavior is covered at the HTTP seam: two `:persist` requests without an id both receive `session=None` (no shared session). (`test_persist_suffix_without_id_falls_back_to_stateless`)
- [x] README "Persistent Sessions" and `docs/opencode-integration.md` stay consistent with the final behavior.
