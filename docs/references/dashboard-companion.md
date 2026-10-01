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
start rather than assume a container runtime. It refuses any address that is not local
(loopback, `localhost`, or `*.orb.local`) before sending credentials. The build writes to
a temporary directory and replaces `dist/` only when every step succeeds.
Every statement runs with `readonly=2` and the window and harness bound as query
parameters, so the dashboard user's settings profile must allow `readonly=2` and needs
only `SELECT ON introspection.*`.

## Environment variables

| Variable                            | What it sets                                                                    | Default                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `INTROSPECTION_CLICKHOUSE_URL`      | Local ClickHouse HTTP address; overrides the config.                            | `[dashboard]` or `[signoz]` `clickhouse_url`               |
| `AGENT_INTROSPECTION_CONFIG`        | Config file the address is read from.                                           | `~/.config/agent-introspection/config.toml`                |
| `INTROSPECTION_CLICKHOUSE_USER`     | Basic-auth user, when the server needs a login.                                 | `default` when only a password is set                      |
| `INTROSPECTION_CLICKHOUSE_PASSWORD` | Basic-auth password. Supply it from a secret manager, never a file.             | none                                                       |
| `INTROSPECTION_WORKFLOW_DB`         | Workflow SQLite store the Interventions view reads findings and proposals from. | `~/.local/share/agent-introspection/introspection.sqlite3` |
| `INTROSPECTION_HOOK_INBOX`          | Activity-hook inbox counted as the Pipeline backlog.                            | `~/.local/share/agent-introspection/hook-inbox`            |

Sources: `dashboard/server.ts`, `dashboard/server/clickhouse.ts`.

## Reading the views

Each view answers one question, outcome first: KPI tiles, daily trends, breakdowns,
exemplar tables, and a session drill-down. The harness selector shows All or one of
the harnesses this machine enables (config `[harnesses] enabled`, loaded into
`introspection.harnesses` by `facts install`). All is the union of the enabled
harnesses' rows for the same signal, and ratios are aggregated before division. A cell
that cannot exist for a harness (headless Codex exec has no user to interrupt or follow
up) shows the registry's reason, never zero. A signal an enabled harness does not emit
is hidden on this machine: its panels are not rendered, and the Pipeline view lists it
under "Signals hidden on this machine". Harnesses are never ranked against each other.

Each panel's chips show, per enabled harness, the coverage state of its route in the
window: rows (with `from <date>` when the route's first row falls inside the window,
such as an activity hook installed then), route missing (no rows while the harness is
otherwise active: a possible break), no activity (the harness is idle), no events (a
rare-event route), or not applicable. The coverage grid shows harnesses this machine
does not enable as not in use, flagged when their routes still have rows.

## Related Docs

- [Facts Store](facts-store.md): the facts and registry behind every panel.
- [Development](development.md): the package's quality commands.
