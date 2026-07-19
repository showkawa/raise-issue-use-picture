# 04 — 5 Whys orchestrator

**What to build:** `FiveWhysSession` that runs the guided dialogue: seeds the facilitator
system prompt, sends the problem + each user answer, prints one focused "why" per turn,
tracks depth with adaptive convergence, and emits the final structured summary.

**Blocked by:** 03 (provider).

**Status:** open

- [ ] Facilitator system prompt: one concise "why" per turn grounded in the last answer; never answer for the user; language follows the user's input.
- [ ] Full conversation history is replayed each turn (stateless `m365-copilot`); no `:persist`.
- [ ] Interactive loop: print the assistant's "why", read the next answer from stdin, repeat.
- [ ] Adaptive depth: stop on convergence (assistant emits the structured summary), on user stop, or at a depth cap that offers to continue.
- [ ] Final summary is structured: problem statement -> why-chain -> root cause -> countermeasures; printed to stdout and saved as Markdown when `-o/--output` is set.
- [ ] Unit tests with the mock/fake provider assert: one question per turn, history growth, convergence stop, and summary formatting/saving.
