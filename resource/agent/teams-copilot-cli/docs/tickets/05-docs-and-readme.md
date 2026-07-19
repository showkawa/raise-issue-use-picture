# 05 — README and docs for the 5 Whys CLI

**What to build:** Rewrite user-facing docs to describe the single-command 5 Whys tool
and its proxy backend; remove all references to the old subcommands and browser
automation.

**Blocked by:** 01, 03, 04 (behavior finalized).

**Status:** open

- [ ] README: `tcc <problem>` usage, `tcc @` multiline, `-o/--output`, `--no-stream`; example 5 Whys session transcript.
- [ ] Document the proxy prerequisite (teams-copilot-proxy running, `m365-copilot`, base URL, `unused` key).
- [ ] Remove old command docs (ask/review/prd/arch/tasks/code/implement/repl) and all Edge/Chrome/Playwright/browser sections.
- [ ] `config.example.yaml` reflects the proxy-only settings.
