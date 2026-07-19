# teams-copilot-cli (`tcc`)

A single-command **5 Whys** root-cause analysis assistant. Give it a problem and
it runs an interactive, facilitator-style dialogue — asking one focused "why" at
a time — until it converges on a root cause and proposes countermeasures.

Answers come from Microsoft 365 Copilot through a local
[`teams-copilot-proxy`](#prerequisite-the-proxy) that exposes an
OpenAI-compatible API. `tcc` does **not** read your files, run commands, or act
as a coding agent — it only has a conversation.

## Install

```bash
npm install
npm run build
npm link      # optional: puts `tcc` (and the `teams-copilot` alias) on your PATH
```

Requires Node.js >= 20.

## Prerequisite: the proxy

`tcc` needs a running `teams-copilot-proxy` that speaks the OpenAI
`/v1/chat/completions` protocol and forwards to Microsoft 365 Copilot. By
default `tcc` expects it at:

| Setting  | Default                       |
| -------- | ----------------------------- |
| base URL | `http://127.0.0.1:8000/v1`    |
| model    | `m365-copilot`                |
| API key  | `unused` (the proxy ignores it) |

Start the proxy first; if it is unreachable, `tcc` prints a clear error.

## Usage

```bash
# Inline problem statement
tcc "our nightly deploy failed again"

# Multiline problem: type it, then a single @ on its own line to finish
tcc @

# Save the final summary as Markdown
tcc "checkout conversion dropped 20%" -o rca.md

# Print each answer at once instead of streaming it
tcc "the build is flaky" --no-stream

# Point at a non-default proxy / model
tcc "latency spiked" --config ./config.yaml
```

Options:

| Flag              | Description                                             |
| ----------------- | ------------------------------------------------------- |
| `-o, --output`    | Save the final summary to a Markdown file               |
| `--no-stream`     | Print each answer whole instead of streaming tokens     |
| `--config <path>` | Path to a `config.yaml` (defaults to `./config.yaml`)   |

During the dialogue, type your answer and press Enter. Enter `q`, `/stop`,
`/quit`, or `/exit` (or send EOF) to stop early — `tcc` will then ask Copilot to
produce the final summary from the conversation so far. After about five levels
it offers to keep going deeper.

## Example session

```
$ tcc "customers are getting 500s on checkout"

Why are customers getting 500s on checkout?
> the payment service is timing out

Why is the payment service timing out?
> it can't open new DB connections

Why can't it open new DB connections?
> the connection pool is exhausted

Why is the connection pool exhausted?
> a batch job holds connections for minutes

Why does the batch job hold connections that long?
> it runs a full-table scan inside one transaction

Problem: Customers get 500s on checkout.
Why chain:
  1. Checkout 500s  -> payment service times out
  2. Times out      -> cannot open new DB connections
  3. Can't connect  -> connection pool exhausted
  4. Pool exhausted -> a batch job holds connections for minutes
  5. Long hold      -> full-table scan inside a single transaction
Root cause: A batch job runs an unindexed full-table scan in one long
transaction, monopolizing the DB connection pool.
Countermeasures:
  - Add the missing index and batch the job into smaller transactions.
  - Cap batch-job pool usage on a separate connection pool.
  - Add pool-saturation alerts and a payment-service timeout budget.
```

## Configuration

All settings are optional and fall back to the defaults above. Copy
`config.example.yaml` to `config.yaml` (or pass `--config`) to override:

```yaml
provider: "proxy"          # proxy | mock (mock is for tests only)
proxy:
  baseUrl: "http://127.0.0.1:8000/v1"
  model: "m365-copilot"
  apiKey: "unused"
  timeoutMs: 120000
```

## Development

```bash
npm run typecheck   # tsc --noEmit
npm test            # vitest
npm run build       # emit dist/
```
