# Microsoft 365 Copilot CLI

Command-line helper that drives Microsoft 365 Copilot Chat at `https://m365.cloud.microsoft/chat` through a local Chrome/Chromium browser via Playwright CDP.

## Requirements

- Node.js 20+
- Google Chrome, Microsoft Edge, or Chromium
- A Microsoft 365 account with Copilot Chat access

The CLI does not manage Microsoft authentication. It reuses the browser profile configured in `browser.userDataDir`; sign in to Microsoft 365 Copilot in that profile before running generation commands. If the configured CDP port already belongs to a browser with an open Copilot Chat tab, the CLI reuses that tab without navigating away from the conversation.

## Install and build

```bash
npm install
npm run build
```

Run from source:

```bash
node dist/cli/index.js ask "Summarize this project"
```

When installed as a package, use:

```bash
tcc "Summarize this project"
```

`tcc <question>` is shorthand for `tcc ask <question>`. The previous
`teams-copilot` executable remains available as a compatibility alias.

## Configuration

The CLI reads `config.yaml` from the current working directory unless `--config <path>` is provided. If no browser path is configured, it checks `TEAMS_COPILOT_BROWSER` and common Edge/Chrome/Chromium install paths.

`copilot.copilotUrl` defaults to `https://m365.cloud.microsoft/chat`. Legacy v1 config files using `edge.executablePath`, `edge.debuggingPort`, and `copilot.inputSelector` are still accepted.

`copilot.responseMode` controls how replies are read:

- `auto` (default): read Microsoft 365 Copilot SignalR/WebSocket updates from the browser session, then fall back to DOM polling if no protocol response is captured.
- `signalr`: require SignalR/WebSocket response capture.
- `dom`: use DOM polling only.

`copilot.requestMode` controls how prompts are submitted:

- `auto` (default): install an in-page WebSocket bridge. The first request after a page load uses the editor and captures the browser's authenticated SignalR request template; later requests use that template directly inside the page.
- `browser-api`: require an already-captured in-page request template.
- `dom`: always use the editor and send button.

The authenticated WebSocket URL and request template remain in page memory. The CLI does not print, persist, or copy Microsoft tokens or cookies into Node configuration.

## Commands

```bash
tcc "Question"
tcc ask "Question"
tcc ask "Explain this code" --file ./src/example.ts
tcc ask "Explain this code" -f ./src/example.ts -o ./answer.md
tcc review ./src/example.ts
tcc prd "Project Name"
tcc arch "Project Name"
tcc tasks "Project Name"
tcc repl
```

`prd`, `arch`, and `tasks` write Markdown files to `output/`. `arch` requires `output/PRD.md`; `tasks` requires both `output/PRD.md` and `output/ARCH.md`.

Use `--no-stream` to wait for the full response before printing.

`tcc ask` writes status messages to stderr while it connects to the browser,
opens Copilot, submits the prompt, and waits for a response. Long waits print
an elapsed-time heartbeat every 15 seconds and include the configured timeout
(120 seconds by default), so a stalled browser or Copilot request is visible
without mixing diagnostics into the response on stdout.

### Ask about code as inline text

Enter a multiline prompt directly in either CMD or Git Bash. Run `tcc @`,
paste any text or code, then finish with `@` on its own line:

```text
tcc @
Explain the following TypeScript:
import { writeFileSync } from 'fs';
const message = `cost: "$5"`;
@
```

The CLI reads all lines after `tcc @` itself, so quotes, dollar signs,
backticks, redirects, and other shell-sensitive characters are preserved.
Only a line containing exactly `@` ends the prompt.

Let the CLI read a local text file and append it to the question as a fenced Markdown code block:

```bash
tcc ask "Explain what this code does" --file ./src/example.ts
tcc ask "Explain what this code does" -f ./src/example.ts
tcc ask "Find correctness issues" --file ./src/example.ts --language typescript
```

Pipe text without creating a file. `ask` automatically reads non-empty stdin, so `--stdin` is optional for pipelines:

```bash
tcc ask "Explain this code" --language typescript < ./src/example.ts
cat ./src/example.ts | tcc ask "Explain this code" --language typescript
tcc ask "Explain this code" --stdin --language typescript <<'CODE'
const value = 1;
console.log(value);
CODE
```

The quoted heredoc delimiter prevents shell characters such as single quotes, backticks, and `<project-name>` from being interpreted by Bash. For large files, use `tcc review <file>` instead of inline text; Copilot prompt-length limits vary by tenant.

Save the answer locally:

```bash
tcc ask "Explain this code" -f ./src/example.ts -o ./answer.md
```

### Review a local code file

```bash
tcc review ./src/example.ts
tcc --no-stream review ./src/example.ts --output ./review.md
tcc --no-stream review ./src/example.ts -o ./review.md
```

`review` uploads the file through the existing authenticated Copilot Chat page, asks Copilot for a Markdown code review, and prints the response. `--output` also saves the final report locally.

The CLI uses Copilot's native file attachment flow. Extensions accepted by the page are uploaded directly. Other text code files, including `.ts`, are uploaded with a temporary `.txt` attachment name while preserving and identifying the original filename in the review prompt; the local source file is not renamed or modified. Binary files and empty files are rejected.

Uploaded code is sent to Microsoft 365 Copilot and may be stored in the account's Copilot upload area. Review only files that the account is authorized to share.

## Disclaimer

This tool automates the Microsoft 365 Copilot web UI. Microsoft may change selectors, URLs, rate limits, or authentication behavior without notice. Use it only with accounts and workspaces you are authorized to access, and review generated documents before relying on them.
