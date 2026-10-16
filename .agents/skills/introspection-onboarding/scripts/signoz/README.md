# SigNoz files

Opt-in files for the SigNoz on this machine. Agent Introspection never applies them on
its own and never reconfigures an existing SigNoz: connect to the one you have instead
(`agent-introspection signoz detect`, then `agent-introspection doctor`).

| File                                  | Purpose                                                                           |
| ------------------------------------- | --------------------------------------------------------------------------------- |
| `install-signoz.sh`                   | guided install of a new SigNoz, run only when the user asks for one               |
| `docker-compose.clickhouse-http.yaml` | Compose override publishing ClickHouse HTTP, OTLP, and the UI on `127.0.0.1` only |
| `otel-collector-opencode-filter.yaml` | collector processors dropping opencode's plumbing spans and its SQL text          |

## install-signoz.sh

- `--dry-run` runs every check and prints the plan without changing anything. Run it
  first.
- It refuses (exit 3) when `signoz detect` or its own checks find a SigNoz or
  ClickHouse container (running or stopped), a `signoz` Compose project, a `signoz-*`
  volume, or any of ports 4317, 4318, 8123, and 8080 in use. It stops (exit 1) on a
  missing requirement (including Docker Compose older than 2.24.4), a Docker daemon on
  another machine, or a non-empty `--dir`.
- It installs through Foundry, SigNoz's supported installer; the old `deploy/` Compose
  files are deprecated. foundryctl, the SigNoz image (`--tag`), and the collector image
  are pinned, and foundryctl is checked against a pinned SHA-256. The stack starts with
  the override, so SigNoz listens on `127.0.0.1` only (UI 8080, OTLP 4317 and 4318,
  ClickHouse HTTP 8123), where the config's `[signoz]` defaults reach it.
- `--dir` (default `${XDG_DATA_HOME:-~/.local/share}/agent-introspection/signoz`) holds
  `casting.yaml`, the generated Compose files, and foundryctl. Stop the stack with
  `docker compose -f <dir>/pours/deployment/compose.yaml down`; adding `-v` deletes its
  data.

## The override and the filter

Both change a SigNoz, so applying either is the user's decision. Each file's header
says how to apply and revert it, including for a SigNoz installed from the deprecated
`deploy/docker` files.

The override serves a single machine, the default: SigNoz publishes its OTLP and UI
ports on every interface, and the override replaces those port lists with `127.0.0.1`
ones through Compose's `!override` tag (Docker Compose 2.24.4 or later). An additional
machine that sends hook records to this one needs the collector's OTLP/HTTP port 4318
reachable: delete the override's collector entry, then recreate the stack.
