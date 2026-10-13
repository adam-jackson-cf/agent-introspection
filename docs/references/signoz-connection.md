# SigNoz Connection

Use this guide to find the SigNoz on this machine and connect Agent Introspection to
it, or, only when you ask for one, to install a new SigNoz.

## Purpose

The facts store lives in the local SigNoz's ClickHouse. By default Agent
Introspection runs on one machine, a person's own desktop, against a self-hosted
SigNoz on that same machine. It never reconfigures an existing SigNoz, and it installs
a new one only when the user asks, through the guided install below. It refuses any
ClickHouse or collector address in `[signoz]` or `[dashboard]` that is not local
(loopback, `localhost`, or a local container name such as OrbStack's `*.orb.local`),
and it assumes no
particular container runtime. It cannot use SigNoz Cloud, which gives no direct
ClickHouse access for the facts store, or a multi-node ClickHouse cluster, where the
`introspection` tables would land on only one node.

Additional machines that run harnesses are an add-on: they keep no SigNoz and no facts
store, and send their harnesses' telemetry and their hook, attribution, and
spool-status records over OTLP to the host's collector. Such a machine sets
`[machine] role = "additional"`: its `export_endpoint` (where `harness configure`
points its harnesses) and `[hooks] otlp_logs_endpoint` are the only addresses in this
tool's config that may name another machine, and its commands that need ClickHouse
refuse to run. The
`introspection-onboarding` skill's
[additional machines workflow](../../.agents/skills/introspection-onboarding/references/additional-machines-workflow.md)
covers them.

## Preconditions

- A self-hosted SigNoz on this machine, with ClickHouse 24.10 or later, or none yet
  and Docker with Compose 2.24.4 or later for the guided install.
- `~/.config/agent-introspection/config.toml`, copied from
  [`config.example.toml`](../../config.example.toml). Its `[signoz]` table selects
  exactly one connection mode.

## Steps

1. Find what runs here. `agent-introspection signoz detect` only reads: it lists
   running containers whose name or image mentions SigNoz or ClickHouse, which of the
   local ports 4317 (OTLP gRPC), 4318 (OTLP/HTTP), 8123 (ClickHouse HTTP), and 8080
   (SigNoz UI) accept connections, and whether ClickHouse answers `/ping` on
   `127.0.0.1:8123`. Its `notes` say what to do next. With a SigNoz present, go to
   step 3 and connect to it as it is.

2. Only when there is no SigNoz and the user asks for one, run the guided install,
   [`install-signoz.sh`](../../.agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh)
   in the onboarding skill, from the repository root, with `--dry-run` first:

   ```sh
   bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh --dry-run
   bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh
   ```

   - It refuses (exit 3) when `signoz detect` or its own checks find a SigNoz or
     ClickHouse container (running or stopped), a Compose project named `signoz`, a
     `signoz-*` volume, or any of ports 4317, 4318, 8123, and 8080 in use. It stops
     (exit 1) on a missing requirement (Docker with Compose 2.24.4 or later, `curl`,
     `tar`, and a SHA-256 tool), a Docker daemon that is not on this machine, or a
     `--dir` that is not empty; a bad argument exits 2. `--dry-run` runs every check and
     changes nothing.
   - It installs through Foundry, SigNoz's supported installer, with pinned versions:
     SigNoz v0.145.0 (`--tag` selects another release), collector v0.144.13, and
     foundryctl v0.3.0, checked against a pinned SHA-256. `--dir` (default
     `${XDG_DATA_HOME:-~/.local/share}/agent-introspection/signoz`) holds
     `casting.yaml`, the generated Compose files, and foundryctl.
   - It starts the stack with the loopback override (step 4) and prints the `[signoz]`
     settings for http mode: `clickhouse_url`, `clickhouse_user`, and `otlp_endpoint`
     (`http://127.0.0.1:4318`). ClickHouse's default user has no password and is
     published on loopback only, so no password key and no `[dashboard]` table are
     needed.
   - On its own, Foundry publishes the OTLP ports 4317 and 4318 and the UI port 8080 on
     every network interface. The override replaces those with `127.0.0.1` ones, so the
     new SigNoz serves this machine alone. A host for additional machines needs 4318
     reachable: delete the override's collector entry and recreate the stack.
   - The folder's
     [README](../../.agents/skills/introspection-onboarding/scripts/signoz/README.md)
     says how to stop the stack, and that removing its volumes deletes the data,
     including the facts store.

3. Choose a connection mode.

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
   otlp_endpoint = "http://127.0.0.1:4318"
   ```

   The container name depends on how SigNoz was installed; see
   [SigNoz layouts](#signoz-layouts).

4. Give the dashboard ClickHouse's HTTP interface. The dashboard always reads
   ClickHouse over HTTP: in http mode it uses `[signoz] clickhouse_url`; in docker mode
   set `[dashboard] clickhouse_url` to a local HTTP address. SigNoz's Compose file does
   not publish port 8123, so either use a container name your runtime resolves on the
   host, or publish it on loopback with the opt-in override
   [`docker-compose.clickhouse-http.yaml`](../../.agents/skills/introspection-onboarding/scripts/signoz/docker-compose.clickhouse-http.yaml).
   The override also replaces the OTLP (4317, 4318) and UI (8080) port lists with
   `127.0.0.1` ones, through Compose's `!override` tag (Docker Compose 2.24.4 or
   later). Applying it to an existing SigNoz is the user's change: add it after the
   stack's own Compose file and recreate the stack; run the same command without it to
   revert. It names Foundry's services; for an older install, rename its three keys to
   `clickhouse`, `otel-collector`, and `signoz` first.

5. Grant the CLI user `SELECT` on `signoz_traces.*`, `signoz_logs.*`, and
   `system.view_refreshes`, plus
   `CREATE DATABASE, CREATE TABLE, CREATE VIEW, DROP TABLE, DROP VIEW, INSERT, SELECT`
   on `introspection.*`. The facts database is always `introspection`. A local
   install's default user usually has them.

6. Run `agent-introspection facts preflight`. It checks the config, the connection,
   the ClickHouse version, the SigNoz columns the projections read, grants, and recent
   producer data, read-only. It always prints its JSON report: a config that does not
   load, or a connection that cannot be set up, is a failing check rather than an
   error message. Any failing check exits 30.

7. Run `agent-introspection doctor` for the rest of the install: the collector ports,
   the hooks endpoint, each enabled harness, the schedule, and the dashboard's
   ClickHouse URL, each with a fix.

## SigNoz layouts

SigNoz has been installed two ways. `signoz detect` shows which one runs here.

|                                    | Older `deploy/docker` install (deprecated)       | Foundry install (current)                                        |
| ---------------------------------- | ------------------------------------------------ | ---------------------------------------------------------------- |
| Compose file                       | `deploy/docker/docker-compose.yaml`              | `pours/deployment/compose.yaml`, generated by `foundryctl forge` |
| ClickHouse Compose service         | `clickhouse`                                     | `signoz-telemetrystore-clickhouse-0-0`                           |
| ClickHouse container               | `signoz-clickhouse`                              | `signoz-telemetrystore-clickhouse-0-0`                           |
| Collector Compose service          | `otel-collector`                                 | `ingester`                                                       |
| UI Compose service                 | `signoz`                                         | `signoz-signoz-0`                                                |
| Collector config                   | `otel-collector-config.yaml`                     | `spec.ingester.spec.config.data` in `casting.yaml`               |
| Docker-mode `clickhouse_container` | `signoz-clickhouse` (the example config's value) | `signoz-telemetrystore-clickhouse-0-0`                           |

The opt-in opencode collector filter in the same folder says how to apply and revert
it for each layout.

## What To Check

- `signoz detect` reports the SigNoz you expect, and `facts preflight` exits zero.
- `doctor` reports no `fail`.
- Each harness's OTLP export points at the local collector; the
  `introspection-onboarding` skill's
  [setup](../../.agents/skills/introspection-onboarding/references/setup-workflow.md)
  and [harness configuration](../../.agents/skills/introspection-onboarding/references/harness-configuration-workflow.md)
  workflows cover the connection, the install order, and each harness's export.

## Related Docs

- [CLI Reference](cli-reference.md#onboarding): `signoz detect`, `doctor`, and
  `facts preflight` output.
- [Dashboard Companion](dashboard-companion.md): the dashboard's own ClickHouse user.
- [Facts Store](facts-store.md): what the grants are used for.
