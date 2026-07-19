# Agent Introspection

Agent Introspection mines existing Codex telemetry from the local SigNoz instance, applies deterministic reduction and detectors, and retains observations, trends, proposals, approval history, model provenance, and derived-telemetry delivery state in SQLite.

It never applies a proposal. Approval records a decision only; entering `applying` requires a separate explicit user request.

## Requirements

- Python 3.12 or newer and `uv`
- OrbStack with the existing SigNoz Compose project
- `docker --context orbstack` access to `signoz-clickhouse`
- OTLP protobuf over HTTP on `localhost:4318`

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
the same check-only quality suite:

```sh
bash scripts/run-ci-quality-gates.sh
```

## CLI

```sh
uv run agent-introspection doctor
uv run agent-introspection health
uv run agent-introspection scan
uv run agent-introspection analysis-generation stage
uv run agent-introspection analysis-generation activate <generation-id>
uv run agent-introspection candidates export
uv run agent-introspection classification import --input-json -
uv run agent-introspection proposal list
uv run agent-introspection telemetry drain
uv run agent-introspection telemetry reconcile-observations --scan-run-id <failed-scan-id>
uv run agent-introspection dashboard verify
uv run agent-introspection db check
uv run agent-introspection schedule status
```

All command results are structured JSON on stdout. Diagnostics are written to stderr and failures use stable non-zero exit codes.

The installed user LaunchAgent runs at minute zero of each hour and once at user-session load. Missed hour boundaries coalesce into one run after wake. Scheduled mode permits one successful or no-data scan per UTC hourly slot, terminalizes interrupted runs before recovery, and uses a shared lease to prevent overlap. Each ClickHouse query has a ten-minute limit and each scan has a fifteen-minute deadline, so a stalled source produces a terminal failure instead of blocking later hourly slots.

Normal scan projections require a remotely verified active analysis generation
whose source and runtime semantic contracts match before extraction. Stage the
bounded local projection and activate it before relying on dashboard signal
panels. Pipeline health remains available when a generation is not active.

## Local SigNoz

The canonical Compose override is `ops/signoz/docker-compose.override.yaml`. It binds the UI and OTLP listeners to loopback, disables tokenizer and API-key authentication, and enables impersonation for this single-user workstation. Root-user values are injected from the connected Infisical project at runtime:

```sh
infisical run --env=dev -- docker --context orbstack compose \
  --project-directory "$HOME/.local/share/codex-observability/signoz/deploy/docker" \
  up --detach --force-recreate signoz
```

Never start this configuration without the loopback-only override and disabled OrbStack LAN port exposure.
