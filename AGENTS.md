# Repository guidance

This repository contains issue assets, documentation, agent-skill resources,
and independent packages. It is not a single buildable workspace.

## Scope first

- Identify the target subtree before changing anything and run commands from
  that package's directory.
- Do not apply Python, Node, Phoenix, or skill-authoring conventions globally.
- Prefer manifests, lockfiles, executable configuration, and tests over README
  claims when they disagree.
- Keep changes surgical; do not reformat or refactor unrelated files.

## Package boundaries

- `issue/` and `blog/`: assets and prose, not application packages.
- `.devin/skills/`: Devin skill resources.
- `resource/phoenix/.agents/skills/`: Phoenix-scoped skill resources.
- `resource/agent/skills/`: independent npm skills package.
- `resource/agent/teams-copilot-cli/`: independent Node 20+ TypeScript package.
- `resource/agent/teams-copilot-proxy/`: Python 3.11+ FastAPI package.

Always check for more specific instructions inside the target subtree.

## Teams Copilot Proxy

Work from `resource/agent/teams-copilot-proxy`.

```bash
uv sync --extra dev
uv run teams-copilot-proxy serve
uv run pytest
uv run pytest tests/test_tool_protocol.py
uv run pytest path/to/test_file.py::test_name
```

The installed CLI entrypoint is `teams_copilot_proxy.cli:main`; the FastAPI
application is constructed by `teams_copilot_proxy.app:create_app`.

The proxy defines pytest configuration but no package-level lint, format,
typecheck, or code-generation command. Do not infer one from local caches.

## Teams Copilot CLI

Work from `resource/agent/teams-copilot-cli` and use npm; this package has its
own lockfile and requires Node 20 or newer.

```bash
npm install
npm run build
npm test
npm run typecheck
npm start
```

The installed commands are `tcc` and `teams-copilot`, both backed by
`dist/cli/index.js`. `npm run build` deletes and recreates `dist`.

## Agent skills

Work from `resource/agent/skills` and follow its `CLAUDE.md`; the nested
`AGENTS.md` redirects there. The package uses npm 10.9.4 and defines Changesets
commands only, not build, test, lint, or typecheck commands.

Promoted skills live in `skills/engineering` and `skills/productivity`. Keep
their top-level and bucket README entries, `.claude-plugin/plugin.json` entries,
human-facing docs, and the `ask-matt` router synchronized when applicable.
Keep the versions in `package.json` and `.claude-plugin/plugin.json` aligned.
After changing either plugin manifest, run:

```bash
claude plugin validate . --strict
```

Run `scripts/link-skills.sh` only when local skill symlinks need to be installed
or refreshed; it modifies `~/.claude/skills` and `~/.agents/skills`.

## Secrets and generated state

Never commit, expose, or use as test fixtures:

- `.env`
- `.oauth_tokens.json`
- `.probe_cache.json`
- `monitor.db`, `monitor.db-wal`, or `monitor.db-shm`
- `.venv/`, Python caches, or pytest/Ruff caches
- the Chrome profile under `~/.teams-copilot-proxy/`

Run the proxy from its package directory because several runtime paths are
relative to the process directory. Unit tests must not require live M365
credentials, a real browser session, or upstream network access.

## Verification

- Use the narrowest relevant test while iterating.
- Run the affected package's full test suite before finishing when practical.
- For runtime changes, distinguish unit verification from live M365 smoke
  testing and report when live authentication was not exercised.
- Do not claim a root-level build or test passed; none is defined.
