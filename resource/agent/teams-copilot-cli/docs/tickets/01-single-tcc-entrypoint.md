# 01 — Reduce CLI to a single `tcc` 5 Whys entrypoint

**What to build:** `tcc <problem...>` (and `tcc @` multiline) starts the 5 Whys flow
directly. Remove all subcommands (`ask`, `review`, `prd`, `arch`, `tasks`, `code`,
`implement`, `repl`). Keep the binary name `tcc` and the `teams-copilot` alias. Global
flags reduce to what 5 Whys needs (`--config`, `--output`, `--no-stream`).

**Blocked by:** 03 (provider) and 04 (orchestrator) for a working flow; the argv/command
rewrite itself can start immediately.

**Status:** open

- [ ] `src/cli/index.ts` registers no subcommands; `tcc <problem...>` and `tcc @` route to the 5 Whys orchestrator.
- [ ] `--browser` / `--port` and other browser flags are removed from the program.
- [ ] `-o/--output <path>` and `--no-stream` remain and are honored.
- [ ] `teams-copilot` alias and `tcc @` multiline input still work.
