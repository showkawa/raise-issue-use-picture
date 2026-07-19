# 02 — Delete coding-agent, document, and copilot-web code

**What to build:** Remove everything that the 5 Whys product no longer uses so no dead
code remains.

**Blocked by:** 01 (entrypoint no longer imports the removed modules).

**Status:** open

- [ ] Delete `src/agent/*` (loop, tools/*, permissions, protocol, tasks-file, redaction, audit, lock, command-classify, system-prompt).
- [ ] Delete subcommand implementations `src/cli/{ask,review,prd,arch,tasks,code,implement,repl,notices,prompt-content}.ts` (keep any small helpers still needed by the entrypoint, e.g. multiline reader — move it if so).
- [ ] Delete `src/provider/copilot-web/*` and its config/types.
- [ ] Remove now-unused deps (Playwright, etc.) from `package.json`; `npm run build` and lint pass with no unresolved imports.
- [ ] Keep `src/provider/mock.ts` only if used by tests; otherwise remove.
