# Dashboard Companion

Use this reference to run the React companion against an existing SigNoz and to read
its views correctly.

## Server

The companion is an independent Bun package in `dashboard/`. `bun run --cwd dashboard
build` writes the assets to `dashboard/dist/`; `bun run --cwd dashboard start` serves
them. The server binds only to `127.0.0.1:4173`.

It queries the local SigNoz ClickHouse over HTTP at the address it resolves from
`INTROSPECTION_CLICKHOUSE_URL`, else `[dashboard] clickhouse_url`, else the http mode's
`[signoz] clickhouse_url` in the agent-introspection config; with none it refuses to
start rather than assume a container runtime.
Every statement runs with `readonly=2` and the window and harness bound as query
parameters, so the dashboard user's settings profile must allow `readonly=2` and needs
only `SELECT ON introspection.*`.

## Environment variables

| Variable | What it sets | Default |
| --- | --- | --- |
| `INTROSPECTION_CLICKHOUSE_URL` | Local ClickHouse HTTP address; overrides the config. | `[dashboard]` or `[signoz]` `clickhouse_url` |
| `AGENT_INTROSPECTION_CONFIG` | Config file the address is read from. | `~/.config/agent-introspection/config.toml` |
| `INTROSPECTION_CLICKHOUSE_USER` | Basic-auth user, when the server needs a login. | `default` when only a password is set |
| `INTROSPECTION_CLICKHOUSE_PASSWORD` | Basic-auth password. Supply it from a secret manager, never a file. | none |
| `INTROSPECTION_WORKFLOW_DB` | Workflow SQLite store the Interventions view reads findings and proposals from. | `~/.local/share/agent-introspection/introspection.sqlite3` |
| `INTROSPECTION_PROJECT_INBOX` | Session-context inbox counted as the Pipeline sync backlog. | `~/.local/share/agent-introspection/session-context-inbox` |

Sources: `dashboard/server.ts`, `dashboard/server/clickhouse.ts`.

## Reading the views

Each view answers one question, outcome first: KPI tiles, daily trends, breakdowns,
exemplar tables, and a session drill-down. The harness selector shows All or one
harness. All is the union of every harness's rows for the same signal, and ratios are
aggregated before division. A cell that cannot exist for a harness (headless Codex exec
has no user to interrupt or follow up) shows the registry's reason, never zero.
Harnesses are never ranked against each other.

## Related Docs

- [Facts Store](facts-store.md): the facts and registry behind every panel.
- [Development](development.md): the package's quality commands.
