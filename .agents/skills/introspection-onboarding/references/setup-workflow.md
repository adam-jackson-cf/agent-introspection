# Setup workflow

Install Agent Introspection on one machine, the user's own desktop, against the SigNoz
on it; or reinstall it after a change that re-projects the facts or changes finding
identities. This single-machine path is the default. To also send records from other
machines, finish here first, then follow the
[additional machines workflow](additional-machines-workflow.md).

[`docs/references/setup.md`](../../../../docs/references/setup.md) is the human-facing
version of this path. When a step here changes a command, an order, or a success
signal, update that guide in the same change.

Run every command from the repository root. Until step 5 installs the CLI, run it as
`uv run agent-introspection …` (an older installed copy may lack the newer commands).
Before and after each step, `agent-introspection doctor` (read-only) shows what is
missing: every check that is not `pass` carries a `fix` (statuses are explained in the
[validation workflow](validation-workflow.md#configuration-check)).

## Requirements

- A self-hosted SigNoz on this machine with ClickHouse 24.10 or later, on a single
  node. SigNoz Cloud (no ClickHouse access) and multi-node clusters are unsupported.
- ClickHouse's HTTP interface at a local address: loopback, `localhost`, or a container
  name the runtime resolves on this machine (for example OrbStack's `*.orb.local`). The
  CLI and the dashboard both read it at `[signoz] clickhouse_url` (default
  `http://127.0.0.1:8123`); the config refuses other hosts. SigNoz does not publish it
  by default: unless the runtime resolves the container name, the user may publish it
  on loopback with the opt-in `scripts/signoz/docker-compose.clickhouse-http.yaml`
  (their change to SigNoz, which restarts ClickHouse). The override also moves SigNoz's
  OTLP and UI ports to loopback.
- A ClickHouse user with `SELECT` on `signoz_traces.*`, `signoz_logs.*`, and
  `system.view_refreshes`, plus `CREATE DATABASE, CREATE TABLE, CREATE VIEW,
DROP TABLE, DROP VIEW, INSERT, SELECT` on `introspection.*` (a local install's
  default user usually has them).

SigNoz is installed in one of two layouts, which name things differently:

| Layout                                        | ClickHouse container                   | Collector service | Collector config                |
| --------------------------------------------- | -------------------------------------- | ----------------- | ------------------------------- |
| Foundry (current; used by the guided install) | `signoz-telemetrystore-clickhouse-0-0` | `ingester`        | `config.data` in `casting.yaml` |
| `deploy/docker` Compose files (deprecated)    | `signoz-clickhouse`                    | `otel-collector`  | `otel-collector-config.yaml`    |

On its own, a Foundry install publishes the
OTLP ports 4317 and 4318 and the UI port 8080 on all interfaces, and does not publish
ClickHouse's HTTP port 8123. The override, which the guided install applies, publishes
all four on `127.0.0.1` only and needs Docker Compose 2.24.4 or later. For a
`deploy/docker` install, rename its three service keys first (its header says how).

## Steps

1. **Find SigNoz.** `agent-introspection signoz detect` (read-only, always exits 0)
   lists SigNoz and ClickHouse containers, whether Docker answers, which of the local
   ports 4317, 4318, 8123, and 8080 are open, `signoz_present`, `clickhouse_ping`
   (ClickHouse answered `/ping` on loopback), and `notes`. When it finds a SigNoz,
   connect to that one (step 3) and never change it.
2. **Install SigNoz (only when the user asks).** When `signoz detect` finds none, ask
   whether the user wants a new one; without that request, stop and say so. Run
   `bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh --dry-run`,
   show the plan, and run it again without `--dry-run` only after the user confirms. It
   refuses (exit 3) when it finds any SigNoz or ClickHouse, running or stopped, or
   their leftovers, and stops (exit 1) on a missing requirement (including Docker
   Compose older than 2.24.4), a Docker daemon on another machine, or a non-empty
   `--dir`. The config's `[signoz]` defaults reach it. The new SigNoz listens on
   `127.0.0.1` only (ClickHouse HTTP, OTLP, and the UI), and its ClickHouse needs no
   password key. Details are in the [scripts README](../scripts/signoz/README.md).
3. **Configure.** With the user (never asking them to paste a secret), write
   `~/.config/agent-introspection/config.toml` from `config.example.toml`, which
   documents every table:
   - `[signoz]`: only what differs from the defaults: `clickhouse_url`,
     `clickhouse_user`, and, only if there is a password, `clickhouse_password_command` (for example a
     Keychain lookup; the scheduled sync runs it too). `clickhouse_password_env` works
     interactively, but `facts schedule install` refuses it. `otlp_endpoint` is the
     local collector's OTLP/HTTP base URL (default `http://127.0.0.1:4318`, which suits
     the guided install's loopback-only ports; `localhost` also works where it resolves
     to 127.0.0.1 first), which `harness configure` writes into each harness.
   - `[harnesses] enabled`: the harnesses the user chose (see the
     [harness configuration workflow](harness-configuration-workflow.md#choose-the-harnesses)).
   - `[labels] openrouter_key_command`: a command that prints the OpenRouter key for
     prompt labels, which the scheduled sync can run. `OPENROUTER_API_KEY` works only
     interactively; omp's stored credential is the last fallback.
   - `[hooks] otlp_logs_endpoint`: leave it out; records go to the collector's
     `/v1/logs`.
   - `[machine]`: leave it out; this machine is the host. Only an additional machine
     sets it ([additional machines workflow](additional-machines-workflow.md)).
4. **Preflight.** `agent-introspection facts preflight` checks the version, the SigNoz
   columns, grants, `system.view_refreshes`, and recent producer data, read-only. It
   always prints one JSON report, with configuration and connection errors as failing
   checks, and exits 30 when any check fails. Fix each failure at its owner; never
   weaken a check.
5. **Gates and CLI.** `bash scripts/run-ci-quality-gates.sh`, then
   `uv tool install --force --reinstall .` (the scheduled job and every hook run this
   copy).
6. **Facts.** `agent-introspection facts install`, then `facts backfill --days 90`. If
   a reinstall drops a stored attribute, also run
   `OPTIMIZE TABLE introspection.spans FINAL` and `… introspection.logs FINAL`.
7. **Workflow store** (only for a reinstall that changes finding identities): copy
   `~/.local/share/agent-introspection/introspection.sqlite3` to
   `introspection.sqlite3.pre-<date>`. Rows are immutable; retired findings go dormant.
8. **Harnesses.** Run the
   [harness configuration workflow](harness-configuration-workflow.md) for each
   harness the user chose.
9. **Schedule.** `agent-introspection facts schedule install`, then
   `facts schedule status`. The job runs `facts sync --log` every minute:
   - macOS: a launchd agent labelled `dev.agent-introspection.sync`. Install retires
     the job an earlier release installed under another label (status reports it
     under `legacy`), moving its plist to
     `~/.local/share/agent-introspection/schedule-backups/`. Reinstall the CLI first
     (step 5): an older copy would install the old label again.
   - Linux: the systemd user units `agent-introspection-sync.service` and
     `agent-introspection-sync.timer` in `~/.config/systemd/user/`. The timer stops
     when the user logs out unless lingering is on: install and status report `linger`
     but never enable it, so ask the user to run `loginctl enable-linger "$USER"`.
   - Any other platform is refused.
10. **First sync.** `agent-introspection facts sync`: `spool`, `labels`, `findings`,
    and `evaluations` report no error, and `labels` is not `skipped`. Hook records reach
    `hook_events` through SigNoz, so allow a minute before checking them.
11. **Doctor.** `agent-introspection doctor` exits 0 (no check fails), and each
    `warn`, `off`, or `info` has a reason the user accepts.
12. **Validate.** The [validation workflow](validation-workflow.md), then the
    `introspection-operations` health workflow over 90 days.
13. **Record** a reinstall in the commit or pull request message: date, commit, steps,
    row counts, parity results, and anything unresolved.

## Done when

- `facts preflight` and `doctor` pass, every step has evidence, and the Pipeline view
  is healthy for 90 days for every enabled harness (or each gap has a recorded reason).
