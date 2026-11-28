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

Both change a SigNoz, so applying either is the user's decision. The steps below apply
and revert each one, including for a SigNoz installed from the deprecated
`deploy/docker` files.

### Applying the override

`install-signoz.sh` starts a new SigNoz with it. For a SigNoz you already run, decide
first, then recreate the stack with the file added after its own:
`docker compose -f compose.yaml -f docker-compose.clickhouse-http.yaml up -d`. Revert by
running the same command without the file; data stays in its volumes. The service
names are Foundry's (`pours/deployment/compose.yaml`). A SigNoz installed from the
deprecated `deploy/docker` files (`docker-compose.yaml`) names them `clickhouse`,
`otel-collector` and `signoz`: rename the three keys to match, or Compose rejects a
service with no image. Keep it on loopback: in a Foundry install, ClickHouse's default
user has no password.

### Applying the filter

1. Add its two processors to the collector config's `processors:`. For a Foundry
   install, put them in `casting.yaml` under
   `spec.ingester.spec.config.data."ingester.yaml"` (Foundry merges that fragment onto
   the generated `pours/deployment/ingester/ingester.yaml`, which `forge` rewrites). For
   a `deploy/docker` install, edit `otel-collector-config.yaml`.
2. Put them first in `service.pipelines.traces.processors`, so dropped spans make no
   span metrics. A list is replaced whole, so restate the existing processors: Foundry
   v0.3.0 `[transform/opencode-db-text, filter/opencode-plumbing, signozspanmetrics/delta, signozspanmapper, signozllmpricing, batch]`;
   `deploy/docker` `[transform/opencode-db-text, filter/opencode-plumbing, signozspanmetrics/delta, batch]`.
3. Recreate the collector: for Foundry, `foundryctl forge -f casting.yaml`, then
   `docker compose -f pours/deployment/compose.yaml up -d --force-recreate`; for
   `deploy/docker`, `docker compose up -d --force-recreate otel-collector`.

To revert, remove both processors from the traces pipeline and from `processors:`,
then recreate the collector the same way. Spans already dropped are not recovered.

The override serves a single machine, the default: SigNoz publishes its OTLP and UI
ports on every interface, and the override replaces those port lists with `127.0.0.1`
ones through Compose's `!override` tag (Docker Compose 2.24.4 or later). An additional
machine that sends hook records to this one needs the collector's OTLP/HTTP port 4318
reachable: delete the override's collector entry, then recreate the stack.
