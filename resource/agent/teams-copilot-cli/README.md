# Teams Copilot CLI

Command-line helper that drives Microsoft Teams Copilot through a local Chromium/Edge browser via Playwright CDP.

## Requirements

- Node.js 20+
- Microsoft Edge, Chrome, or Chromium
- A Teams account with Copilot access

The CLI does not manage Microsoft authentication. It reuses the browser profile configured in `browser.userDataDir`; sign in to Teams in that profile before running generation commands.

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

Legacy v1 config files using `edge.executablePath`, `edge.debuggingPort`, and `copilot.inputSelector` are still accepted.

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

This tool automates the Teams web UI. Microsoft may change selectors, iframe URLs, rate limits, or authentication behavior without notice. Use it only with accounts and workspaces you are authorized to access, and review generated documents before relying on them.
