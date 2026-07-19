# SPEC — teams-copilot-cli as a 5 Whys root-cause assistant

Status: proposed (grilled 2026-07). Replaces the previous coding-agent product. The CLI
is reduced to a single `tcc` entrypoint that runs a guided **5 Whys** conversation to
help the user find the root cause of a problem. Model access goes through
`teams-copilot-proxy` (OpenAI-compatible API).

## Goal

`tcc <problem>` starts an interactive, facilitator-style 5 Whys dialogue. Copilot (via
the proxy) asks one focused "why" at a time about the user's previous answer; after
roughly five levels it converges on a root cause and proposes countermeasures. The tool
does not read local files, execute commands, or act as a coding agent.

## Decisions (from grill)

- **W1 — Pure guided dialogue.** Interactive, turn-by-turn "why" questioning; the user
  answers each round. No local file/log access, no tools, no code execution.
- **Q2a — Adaptive depth.** Target five "why" levels but adapt: stop early once an
  actionable root cause is reached; if five levels are not enough, offer to go deeper.
  Not rigidly fixed at five rounds.
- **Q2b — Per-round output + structured summary.** Each round Copilot returns exactly
  one focused follow-up "why" aimed at the user's last answer. At the end it emits a
  structured summary: problem statement -> the chain of whys -> **root cause** ->
  suggested countermeasures.
- **Q2c — Backend channel.** Uses `teams-copilot-proxy` with the stateless
  `m365-copilot` model; the CLI replays the full conversation each turn. Because no
  `tools` are sent, the proxy does **not** suppress the system prompt, so the 5 Whys
  facilitator persona lives in the `system` role.
- **Q3a — Output.** Live dialogue on stdout; the final structured summary is printed and
  can be saved to Markdown with `-o/--output <path>`.
- **Q3b — Deletion scope.** Delete the entire coding-agent and document-command stack:
  `src/agent/*` (loop, tools, permissions, protocol, tasks-file, redaction, ...),
  `src/cli/{ask,review,prd,arch,tasks,code,implement,repl,notices,prompt-content}.ts`
  (all subcommands), and `src/provider/copilot-web/*`. Keep only: CLI arg parsing, the
  5 Whys orchestrator, a proxy HTTP provider, and config. `provider/mock` may be kept
  for tests.

## Target architecture

```
tcc <problem statement>              single entrypoint (no subcommands)
        |
   FiveWhysSession                   builds messages, tracks depth, detects convergence
        |
   ProxyProvider  --OpenAI /v1/chat/completions (model=m365-copilot, NO tools)-->  teams-copilot-proxy --> Copilot
        |
   stdout dialogue + final summary (optional -o Markdown)
```

- **Single command.** `tcc <problem...>` (and `tcc @` multiline) runs the 5 Whys flow.
  No `ask/review/prd/arch/tasks/code/implement/repl` subcommands remain. The binary name
  `tcc` and the `teams-copilot` compatibility alias stay.
- **Provider seam.** Reuse the existing `Provider` / `ChatSession` interfaces
  (`src/provider/types.ts`). `ProxyProvider.createSession().send()` performs one OpenAI
  chat request with no `tools` field.
- **Config.** `provider` defaults to `proxy`. Settings: proxy base URL (default
  `http://127.0.0.1:8000/v1`), model (default `m365-copilot`), API key placeholder
  (`unused`), request timeout. All `browser.*` / `copilot.*` settings are removed
  (legacy-tolerated/ignored).

## System prompt (5 Whys facilitator)

The `system` role instructs Copilot to act as a 5 Whys facilitator:
- Ask exactly one concise "why" question per turn, grounded in the user's last answer.
- Do not answer for the user or invent facts.
- After ~5 levels (or earlier convergence), output the structured summary: problem, the
  why-chain, the root cause, and concrete countermeasures.
- Language follows the user's input language.

## Conversation flow

1. `messages` = [system persona] + prior turns + current user answer.
2. Send to proxy `/v1/chat/completions` with `model=m365-copilot`, no `tools`, streaming
   to stdout.
3. Print Copilot's "why" question; read the user's next answer from stdin.
4. Repeat, tracking depth. Stop when: Copilot emits the structured summary
   (convergence), the user opts to stop, or a depth cap is reached (offer to continue).
5. Print (and optionally save) the final summary.

## Out of scope / removed

- All coding-agent behavior: local tools, permission gate, worktree/commit handling.
- All document generators (PRD/ARCH/TASKS) and `review`/file-upload.
- The embedded browser channel and its session-resilience machinery.

## Acceptance criteria

- `tcc <problem>` starts an interactive 5 Whys session over the proxy; there are no
  other subcommands; build and unit tests are green.
- Each assistant turn is a single focused "why"; the session ends with a structured
  summary containing problem, why-chain, root cause, and countermeasures.
- Depth is adaptive (early convergence supported; depth cap offers to continue).
- `-o/--output` saves the final summary as Markdown; stdout shows the live dialogue.
- The agent/tools/document/copilot-web code is deleted; the proxy provider sends no
  `tools`.

## Open items (need live runs / user)

- Real end-to-end 5 Whys run against a signed-in proxy — not yet performed.
- Wording/robustness of the facilitator system prompt against real Copilot behavior.
- Depth-cap default (e.g. offer to continue after 5) — confirm during live runs.
