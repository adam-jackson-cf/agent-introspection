# SigNoz Connection

Use this guide to connect Agent Introspection to the SigNoz already running on this
machine.

## Purpose

The facts store lives in the local SigNoz's ClickHouse. Agent Introspection runs on one
machine, for one person, against a self-hosted SigNoz already installed there. It does
not install or start SigNoz, and it refuses any SigNoz ClickHouse or collector address
that is not local (loopback, `localhost`, or a local container name such as OrbStack's
`*.orb.local`). It assumes no particular container runtime. It cannot
use SigNoz Cloud, which gives no direct ClickHouse access for the facts store, or a
multi-node ClickHouse cluster, where the `introspection` tables would land on only one
node.

## Preconditions

- A self-hosted SigNoz on this machine, with ClickHouse 24.10 or later.
- `~/.config/agent-introspection/config.toml`, copied from
  [`config.example.toml`](../../config.example.toml). Its `[signoz]` table selects
  exactly one connection mode.

## Steps

1. Choose a connection mode.

   The **docker** mode (the default) runs `clickhouse-client` in the SigNoz ClickHouse
   container:

   ```toml
   [signoz]
   clickhouse_container = "signoz-clickhouse"   # the name `docker ps` shows
   # docker_context = "desktop-linux"          # optional; else the current context
   ```

   The **http** mode uses ClickHouse's HTTP interface at a local address. A password,
   when the user has one, comes from `clickhouse_password_command` (for example a
   Keychain lookup, which the scheduled sync can also run) or, for interactive use only,
   from the environment variable `clickhouse_password_env` names; it is never stored in
   the file.

   ```toml
   [signoz]
   clickhouse_url = "http://127.0.0.1:8123"
   clickhouse_user = "default"
   otlp_endpoint = "http://localhost:4318"
   ```

   The dashboard always reads ClickHouse over HTTP. In docker mode, also set
   `[dashboard] clickhouse_url` to ClickHouse's local HTTP address: its port published
   on loopback (SigNoz's Compose file does not publish it by default), or a container
   name your runtime resolves on the host.

2. Grant the CLI user `SELECT` on `signoz_traces.*`, `signoz_logs.*`, and
   `system.view_refreshes`, plus
   `CREATE DATABASE, CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW, INSERT, SELECT`
   on `introspection.*`. The facts database is always `introspection`.

3. Run `agent-introspection facts preflight`. It checks the version, the SigNoz columns
   the projections read, grants, and recent producer data, read-only, and exits non-zero
   when a check fails.

## What To Check

- `facts preflight` exits zero.
- Each harness's OTLP export points at the local collector; the
  `introspection-onboarding` skill's
  [setup](../../.agents/skills/introspection-onboarding/references/setup-workflow.md)
  and [harness configuration](../../.agents/skills/introspection-onboarding/references/harness-configuration-workflow.md)
  workflows cover the connection, the install order, and each harness's export.

## Related Docs

- [Dashboard Companion](dashboard-companion.md): the dashboard's own ClickHouse user.
- [Facts Store](facts-store.md): what the grants are used for.
