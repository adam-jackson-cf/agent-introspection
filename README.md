# Agent Introspection

Agent Introspection surfaces the same signals from every agent harness on this machine (omp, Codex app-server/CLI/exec, and Claude Code) so one person can explore model usage and agent process and find problems and improvements. Producer telemetry in the local SigNoz instance is materialized into ClickHouse facts, and a React companion shows question-led views over them. Findings and intervention proposals, with their approval history, live in a small SQLite workflow store.

It never applies a proposal. Approval records a decision only; entering `applying` requires a separate explicit user request.

## Requirements

- Python 3.12 or newer and `uv`
- OrbStack with the existing SigNoz Compose project
- `docker --context orbstack` access to `signoz-clickhouse`
- Bun, for the dashboard companion

## Development

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Install the Git pre-commit trigger and run the complete quality suite with:

```sh
uv run pre-commit install
uv run pre-commit run --all-files
```

Both pre-commit and CI use `scripts/run-ci-quality-gates.sh`. Run it directly for
the same check-only quality suite, including the independent dashboard package:

```sh
bash scripts/run-ci-quality-gates.sh
```

## Dashboard facts

The dashboard reads ClickHouse-materialized facts; see the
[Dashboard v3 Plan](docs/dashboard-v3-plan.md). Refreshable materialized views copy
curated producer spans and logs from SigNoz into the durable `introspection`
database every minute, with raw prompt, command, argument, and output text and
identity keys removed. The views `usage_events`, `task_outcomes`, `tool_calls`,
`user_signals`, and `model_calls` normalize omp, Codex, and Claude Code telemetry;
a snapshot table of each view holds its last 91 days, rebuilt right after every minute
load; windows starting more than 90 days ago read the live views.

The signal support registry,
[`signal_support.toml`](src/agent_introspection/facts_sql/signal_support.toml),
defines each dashboard signal once and records, per harness, the route that
reaches it, whether that route is aligned or differs, or why it is not emitted.
`facts install` validates it and loads it into `introspection.signal_*` tables;
every panel's info note is rendered from those tables.

```sh
uv run agent-introspection facts install           # tables, loaders, views, snapshots, registry
uv run agent-introspection facts backfill --days 90  # re-project everything SigNoz retains
uv run agent-introspection facts status            # loader state and per-harness freshness
```

## Dashboard companion

The React companion is an independent Bun package in `dashboard/`. Its package
commands, which are included in the canonical quality suite, are:

```sh
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard check
bun run --cwd dashboard test
bun run --cwd dashboard lint
bun run --cwd dashboard format:check
bun run --cwd dashboard build
bun run --cwd dashboard start
```

The server binds only to `127.0.0.1:4173`. It queries ClickHouse over HTTP at
`http://signoz-clickhouse.orb.local:8123`, the address OrbStack gives the SigNoz
container on this Mac only (no published port; override with
`INTROSPECTION_CLICKHOUSE_URL`). Every statement runs read-only with the window
and harness bound as query parameters. The Interventions view also reads findings
and proposals from the workflow SQLite store (`INTROSPECTION_WORKFLOW_DB`).

Each view answers one question, outcome first: KPI tiles, daily trends,
breakdowns, exemplar tables, and a session drill-down. The harness selector shows
All or one harness. All is the union of every harness's rows for the same signal,
and ratios are aggregated before division. A harness that does not emit a signal
shows the registry's reason, never zero. Harnesses are never ranked against each
other.

## CLI

```sh
uv run agent-introspection facts install             # tables, loaders, views, snapshots, registry
uv run agent-introspection facts backfill --days 90  # re-project everything SigNoz retains
uv run agent-introspection facts status              # loader state and per-harness freshness
uv run agent-introspection facts sync-projects       # hook inbox -> introspection.session_projects
uv run agent-introspection facts findings            # promote recurring failures into findings
uv run agent-introspection facts sync                # both of the above
uv tool install --force --reinstall .                # standalone copy the launchd job runs
agent-introspection facts schedule install           # run `facts sync` every minute (launchd)
uv run agent-introspection facts schedule status
uv run agent-introspection candidates export --reserved-model-budget <tokens>
uv run agent-introspection proposal list
```

All command results are structured JSON on stdout. Diagnostics are written to stderr and failures use stable non-zero exit codes.

## Project attribution

The session-context hooks installed in Claude Code, Codex, and omp write one JSON
record per session event into `~/.local/share/agent-introspection/session-context-inbox`:
the native session ID and the Git project it runs in, or a rejection such as a
non-Git workspace. The `com.adamjackson.agent-introspection.projects` LaunchAgent runs
`facts sync` every minute from the standalone `uv tool` install: it inserts the records
into `introspection.session_projects`, removes each file once ClickHouse has it, and then
promotes recurring failure signatures into workflow findings. The
dashboard joins facts to `introspection.session_project` (the latest project per
session) by session ID; the Pipeline view shows the attributed share of tasks per
harness, rejections, and the inbox backlog.

Findings, proposals, and review sessions live in the SQLite workflow store at
`~/.local/share/agent-introspection/introspection.sqlite3`. The retired scan ledger is
archived at `/Volumes/UGreen-External/archive/agent-introspection-2026-09-28/`.

What the dashboard cannot show, per harness and per signal, is listed in
[Dashboard Data Gaps](docs/dashboard-data-gaps.md).

## Codex Desktop Attribution

Codex Desktop attribution uses documented global `SessionStart` and `SessionEnd`
hooks. The hook emits only the native session ID, absolute workspace, lifecycle
event, and timestamp to the local session-context runtime. The shared runtime
resolves the canonical Git workspace; the hook never inspects or forwards
prompts, responses, or transcripts. User hooks require interactive
review/trust and are never automatically approved.
Codex's active configuration root is `$CODEX_HOME` when it is set to a non-empty absolute path; otherwise it is `~/.codex`. Global hooks live at `<codex-root>/hooks.json`, and trust state lives at `<codex-root>/config.toml`.

**Producer boundary is not qualified.** These global hooks also run in Codex CLI,
and their documented native envelope does not distinguish CLI from Desktop.
A real CLI run emitted a `codex-app-server` end record, which the retired scanner
quarantined. The two newly added global Desktop registrations were rolled back,
preserving unrelated hooks, trust settings, runtime bytes, and evidence.
Do not install the Desktop adapter into a configuration root shared with Codex CLI
until an authoritative producer boundary is available.

Changing projects within a live Codex Desktop thread is unsupported by design.
Attribution uses the workspace supplied at the thread's lifecycle boundary.
Supporting mid-thread project changes would require complex inspection or
interposition of app-server protocol traffic. This is considered rare, so the
additional complexity is not justified.

## Local SigNoz

The canonical Compose override is `ops/signoz/docker-compose.override.yaml`. It binds the UI and OTLP listeners to loopback, disables tokenizer and API-key authentication, and enables impersonation for this single-user workstation. Root-user values are injected from the connected Infisical project at runtime:

```sh
infisical run --env=dev -- docker --context orbstack compose \
  --project-directory "$HOME/.local/share/codex-observability/signoz/deploy/docker" \
  up --detach --force-recreate signoz
```

Never start this configuration without the loopback-only override and disabled OrbStack LAN port exposure.
