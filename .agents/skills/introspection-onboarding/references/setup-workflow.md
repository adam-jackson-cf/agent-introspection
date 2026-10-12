# Setup workflow

Install Agent Introspection against the SigNoz already running on this machine, or
reinstall it after a change that re-projects the facts or changes finding identities.

## Requirements

- A self-hosted SigNoz on the host machine with ClickHouse 24.10 or later, on a single
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
7. **Endpoint and schedule.** Set `[hooks] otlp_logs_endpoint` (the local collector's
   OTLP/HTTP logs URL) only now that the loaders exist, then
   `agent-introspection facts schedule install` and `facts schedule status`.
8. **First sync.** `agent-introspection facts sync`: `spool`, `labels`, `findings`,
   and `evaluations` report no error, and `labels` is not `skipped`. Hook and
   attribution records now reach `hook_events` and `session_projects` through SigNoz,
   so allow a minute before checking them.
9. **Validate.** The validation workflow, then the `introspection-operations` health
   workflow over 90 days.
10. **Record** a reinstall in `docs/dashboard-v3-plan.md`'s findings log: date, commit,
    steps, row counts, parity results, and anything unresolved.

## Other machines (no SigNoz, no facts store)

Every machine that runs a harness sends its hook, attribution, and spool-status
records over OTLP to the collector of the machine that hosts SigNoz and the facts
store, so each session is attributed to a project whichever machine ran it. Do this on
each such machine; the host machine keeps steps 1 to 10 above.

1. **CLI.** Install from the repository at a tag:
   `uv tool install --force git+<repository-url>@<tag>` (run it again to upgrade).
   `agent-introspection` must resolve at `~/.local/bin/agent-introspection`.
2. **Endpoint.** In `~/.config/agent-introspection/config.toml` write
   `[hooks]` `otlp_logs_endpoint = "http://<collector-host>:<otlp-http-port>/v1/logs"`:
   the collector's OTLP/HTTP port (usually 4318), never the gRPC port (4317). Set it
   on the host machine only after `facts install` has created the loaders; records
   sent earlier wait for the 3-day sweep or `facts backfill`.
3. **Harnesses.** Run the harness configuration workflow (omp plugin, Claude hooks,
   Codex export) as on the host machine.
4. **Timer.** Install the systemd user units from `.agents/skills/introspection-onboarding/scripts/` (paths are relative to the repository root) and enable them; the
   service runs `agent-introspection spool sync` (attribution scan, status record,
   OTLP drain) every minute:

   ```sh
   mkdir -p ~/.config/systemd/user
   cp .agents/skills/introspection-onboarding/scripts/agent-introspection-spool-sync.service \
      .agents/skills/introspection-onboarding/scripts/agent-introspection-spool-sync.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now agent-introspection-spool-sync.timer
   loginctl enable-linger "$USER"   # keeps the timer running with no login session
   ```

   The unit sets `PATH` to include `~/.local/bin` (where `uv tool` installs the CLI);
   `Persistent=true` catches up after suspend or downtime. Back up any existing unit
   with the same name first.

5. **Validate.** `systemctl --user list-timers` shows the timer, `agent-introspection
spool sync` exits 0, the spool backlog drains to 0, and the validation workflow's
   probe record shows up in `hook_events` with this machine's host name.

## Done when

- Preflight and every step pass, and the Pipeline view is healthy for 90 days for
  every harness (or each gap has a recorded reason).
- Every other machine's spool status is fresh in the Pipeline view, with a backlog
  that drains and no rejected files.
