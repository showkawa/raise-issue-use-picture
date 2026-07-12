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
teams-copilot ask "Summarize this project"
```

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
teams-copilot ask "Question"
teams-copilot prd "Project Name"
teams-copilot arch "Project Name"
teams-copilot tasks "Project Name"
teams-copilot repl
```

`prd`, `arch`, and `tasks` write Markdown files to `output/`. `arch` requires `output/PRD.md`; `tasks` requires both `output/PRD.md` and `output/ARCH.md`.

Use `--no-stream` to wait for the full response before printing.

## Disclaimer

This tool automates the Microsoft 365 Copilot web UI. Microsoft may change selectors, URLs, rate limits, or authentication behavior without notice. Use it only with accounts and workspaces you are authorized to access, and review generated documents before relying on them.
