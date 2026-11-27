# Dashboard Companion

Use this reference to run the React companion against an existing SigNoz and to read
its views correctly.

## Server

The companion is an independent Bun package in `dashboard/`. The server binds only to
`127.0.0.1:4173`. `agent-introspection dashboard install --repo <checkout>` runs it as
a user service: it builds the checkout's dashboard (`bun install --frozen-lockfile`,
then `bun run build`) and starts `bun server.ts` there as a launchd agent kept alive
(macOS) or a systemd user service restarted on failure (Linux), logging to
`~/.local/share/agent-introspection/dashboard.log`. The service serves the checkout it
was installed from, so pulling or switching branches changes it at its next restart;
rerun `dashboard install` after an update. `dashboard status` and `dashboard remove`
report and stop it, and `doctor`'s `dashboard_service` check says whether it answers.
The service cannot run a ClickHouse password command, so a password-protected
ClickHouse is refused: run the server by hand instead (`bun run --cwd dashboard build`,
then `bun run --cwd dashboard start` with the credentials below set).

It queries the local SigNoz ClickHouse over HTTP at `INTROSPECTION_CLICKHOUSE_URL`,
else `[signoz] clickhouse_url` in the agent-introspection config, else that key's
default (`http://127.0.0.1:8123`), so it reads the ClickHouse the CLI does. It refuses any address that is not local
(loopback, `localhost`, or `*.orb.local`) before sending credentials. The build writes to
a temporary directory and replaces `dist/` only when every step succeeds.
Every statement runs with `readonly=2` and the window and harness bound as query
parameters, so the dashboard user's settings profile must allow `readonly=2` and needs
only `SELECT ON introspection.*`.

## Environment variables

| Variable                            | What it sets                                                                    | Default                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `INTROSPECTION_CLICKHOUSE_URL`      | Local ClickHouse HTTP address; overrides the config.                            | `[signoz] clickhouse_url`, else `http://127.0.0.1:8123`    |
| `AGENT_INTROSPECTION_CONFIG`        | Config file the address is read from.                                           | `~/.config/agent-introspection/config.toml`                |
| `INTROSPECTION_CLICKHOUSE_USER`     | Basic-auth user, when the server needs a login.                                 | `default` when only a password is set                      |
| `INTROSPECTION_CLICKHOUSE_PASSWORD` | Basic-auth password. Supply it from a secret manager, never a file.             | none                                                       |
| `INTROSPECTION_WORKFLOW_DB`         | Workflow SQLite store the Interventions view reads findings and proposals from. | `~/.local/share/agent-introspection/introspection.sqlite3` |

Sources: `dashboard/server.ts`, `dashboard/server/clickhouse.ts`.

## Reading the views

Each view answers one question, outcome first: KPI tiles, daily trends, breakdowns,
exemplar tables, and a session drill-down. The harness selector shows All or one of
the harnesses this machine enables (config `[harnesses] enabled`, loaded into
`introspection.harnesses` by `facts install`). All is the union of the enabled
harnesses' rows for the same signal, and ratios are aggregated before division. A cell
that cannot exist for a harness (headless Codex exec has no user to interrupt or follow
up) shows the registry's reason, never zero. Signals an enabled harness does not emit are hidden (see the
[harness support matrix](data-gaps.md#harness-support-matrix)). Harnesses are never ranked against each other.

Each panel's chips show, per enabled harness, the coverage state of its route in the
window: rows (with `from <date>` when the route's first row falls inside the window,
such as an activity hook installed then), route missing (no rows while the harness is
otherwise active: a possible break), no activity (the harness is idle), no events (a
rare-event route), or not applicable. The coverage grid shows harnesses this machine
does not enable as not in use, flagged when their routes still have rows.

## Experimental features

`[experimental]` in the agent-introspection config turns on dashboard features that are
not yet proven in use; each is off unless set to `true`. The server reads the table
once at start, so restart the service (`dashboard install`) after a change.

| Key                     | What it shows when on                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `intervention_outcomes` | Findings and fixes: whether an applied intervention reduced what it targeted (`intervene.post_recurrence`): the "Did an intervention reduce what it targeted?" section, the Applied interventions figure, each proposal's verdict, ratio and rates, and `evaluated` events in Approval history. Off, the signal leaves the registry, so the Glossary and `dashboard views` omit it, and the view's question drops its outcome half. |

Off, the server leaves those datasets and fields out of `/api/view` and skips their
queries, and `/api/registry` reports the state in `features`. `facts sync` and
`proposal evaluate` record verdicts either way, so turning a feature on shows past
results at once.

## Reading the views from a terminal

`agent-introspection dashboard views`, `view`, `overview` and `session` print, as JSON,
what the running service's `/api` routes return to the page, so an agent reads what a
person sees without a browser (see the [CLI reference](cli-reference.md)). They need
the service running on this machine and fail with exit code 60 when it is not.
`INTROSPECTION_DASHBOARD_URL` points them at another loopback address (default
`http://127.0.0.1:4173`); an address off this machine is refused.

## Related Docs

- [Facts Store](facts-store.md): the facts and registry behind every panel.
- [Development](development.md): the package's quality commands.
- [CLI Reference](cli-reference.md): the `dashboard` read commands.
