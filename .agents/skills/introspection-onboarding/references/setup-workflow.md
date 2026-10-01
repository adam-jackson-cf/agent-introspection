# Setup workflow

Install Agent Introspection against the SigNoz already running on this machine, or
reinstall it after a change that re-projects the facts or changes finding identities.

## Requirements

- A self-hosted SigNoz on this machine with ClickHouse 24.10 or later, on a single
  node. SigNoz Cloud (no ClickHouse access) and multi-node clusters are unsupported.
- ClickHouse reachable locally, one of:
  - **docker mode:** `docker exec` into its container (the name `docker ps` shows);
  - **http mode:** its HTTP interface at a local address: loopback, `localhost`, or a
    container name the runtime resolves on the host (for example OrbStack's
    `*.orb.local`). The config refuses other hosts.
- The dashboard reads ClickHouse over HTTP in both modes. SigNoz's Compose file does
  not publish the HTTP port by default; if the runtime cannot resolve the container
  name, the user publishes it on loopback (their change to SigNoz, not ours).
- A ClickHouse user with `SELECT` on `signoz_traces.*`, `signoz_logs.*`, and
  `system.view_refreshes`, plus `CREATE DATABASE, CREATE TABLE, CREATE VIEW,
DROP TABLE, DROP VIEW, INSERT, SELECT` on `introspection.*` (a local install's
  default user usually has them).

## Steps

1. **Connect.** With the user (never asking them to paste a secret), find how
   ClickHouse is reached and the local collector endpoints, then write
   `~/.config/agent-introspection/config.toml` from `config.example.toml`:
   - `[signoz]`: exactly one mode. Docker: `clickhouse_container`, and
     `docker_context` only if it is not the current context. HTTP: `clickhouse_url`,
     `clickhouse_user`, and, only if there is a password, `clickhouse_password_command`
     (for example a Keychain lookup; the launchd job runs it too).
     `clickhouse_password_env` works interactively but `facts schedule install`
     refuses it. `otlp_endpoint` records the collector.
   - `[dashboard] clickhouse_url`: the local HTTP address, needed in docker mode.
2. **Preflight.** `uv run agent-introspection facts preflight` must pass (version,
   SigNoz columns, grants, `system.view_refreshes`, recent producer data). Fix each
   failure at its owner; never weaken a check.
3. **Gates and CLI.** `bash scripts/run-ci-quality-gates.sh`, then
   `uv tool install --force --reinstall .` (the launchd job and every hook run this copy).
4. **Facts.** `agent-introspection facts install`, then `facts backfill --days 90`. If
   a reinstall drops a stored attribute, also run
   `OPTIMIZE TABLE introspection.spans FINAL` and `… introspection.logs FINAL`.
5. **Workflow store** (reinstall that changes finding identities): copy
   `~/.local/share/agent-introspection/introspection.sqlite3` to
   `introspection.sqlite3.pre-<date>`. Rows are immutable; retired findings go dormant.
6. **Harnesses.** Run the harness configuration workflow for every harness in use.
7. **Schedule.** `agent-introspection facts schedule install`, then
   `facts schedule status`.
8. **First sync.** `agent-introspection facts sync`: `hooks`, `sessions`, `labels`,
   `findings`, and `evaluations` report no error, and `labels` is not `skipped`.
9. **Validate.** The validation workflow, then the `introspection-operations` health
   workflow over 90 days.
10. **Record** a reinstall in `docs/dashboard-v3-plan.md`'s findings log: date, commit,
    steps, row counts, parity results, and anything unresolved.

## Done when

- Preflight and every step pass, and the Pipeline view is healthy for 90 days for
  every harness (or each gap has a recorded reason).
