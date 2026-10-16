# Setup workflow

Install Agent Introspection on one machine, the user's own desktop, against the SigNoz
on it; or reinstall it after a change that re-projects the facts or changes finding
identities. This single-machine path is the default. To also send records from other
machines, finish here first, then follow the
[additional machines workflow](additional-machines-workflow.md).

[`docs/references/setup.md`](../../../../docs/references/setup.md) is the human-facing
version of this path. When a step here changes a command, an order, or a success
signal, update that guide in the same change.

Run every command from the repository root. Install the CLI first (step 1): the
scheduled job, every hook, and `setup` itself run that copy. Before and after each
step, `agent-introspection doctor` (read-only) shows what is
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
- [Bun](https://bun.sh) on `PATH` for the dashboard service, unless `--no-dashboard`.
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

1. **CLI.** `uv tool install --force --reinstall .`, then `agent-introspection --help`.
   An older installed copy lacks the newer commands and would reinstall stale hooks
   and an old schedule label. The quality gates are for changing this repository, not
   for installing it.
2. **Find SigNoz.** `agent-introspection signoz detect` (read-only, always exits 0)
   lists SigNoz and ClickHouse containers, whether Docker answers, which of the local
   ports 4317, 4318, 8123, and 8080 are open, `signoz_present`, `clickhouse_ping`
   (ClickHouse answered `/ping` on loopback), and `notes`. When it finds a SigNoz,
   connect to that one and never change it. When its ClickHouse does not answer on
   `127.0.0.1:8123`, publishing it with the opt-in override (which restarts ClickHouse)
   is the user's decision; a container name the runtime resolves (OrbStack's
   `*.orb.local`) is the alternative, passed as `--clickhouse-url`.
3. **Install SigNoz (only when the user asks).** When `signoz detect` finds none, ask
   whether the user wants a new one; without that request, stop and say so. Run
   `bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh --dry-run`,
   show the plan, and run it again without `--dry-run` only after the user confirms. It
   refuses (exit 3) when it finds any SigNoz or ClickHouse, running or stopped, or
   their leftovers, and stops (exit 1) on a missing requirement (including Docker
   Compose older than 2.24.4), a Docker daemon on another machine, or a non-empty
   `--dir`. Every `[signoz]` default reaches it: ClickHouse HTTP, OTLP, and the UI on
   `127.0.0.1` only, and a ClickHouse user with no password. Details are in the
   [scripts README](../scripts/signoz/README.md).
4. **Decisions.** Ask the user, never assuming and never asking them to paste a secret:
   - the harnesses (`--harness`, repeatable; default Claude Code and Codex, omp is
     opt-in as `oh-my-pi`; see the
     [harness configuration workflow](harness-configuration-workflow.md#choose-the-harnesses));
   - prompt export (`--prompt-export`): prompt text is then kept in SigNoz for its
     retention; the facts store never keeps it;
   - prompt labels: a command that prints the OpenRouter key
     (`--openrouter-key-command "security find-generic-password -s openrouter -w"`, for
     example), the one key source, which the scheduled sync runs too; without it,
     labels stay off;
   - only what differs from the `[signoz]` defaults: `--clickhouse-url`,
     `--clickhouse-user`, `--clickhouse-password-command` (a password makes the
     dashboard service refuse; it is then run by hand), `--otlp-endpoint`;
   - the dashboard service (`--no-dashboard` skips it; `--repo` names the checkout).

   `[hooks]` and `[machine]` stay out: records go to the collector's `/v1/logs`, and
   this machine is the host.

5. **Preview.** `agent-introspection setup <options> --dry-run`, and show the user: the
   config diff, the read-only preflight (it stops there on a failing check, naming it
   with its detail; fix each at its owner and never weaken a check), each harness's
   planned settings and hooks, and the doctor report as the machine is now.
6. **Workflow store** (only for a reinstall that changes finding identities): copy
   `~/.local/share/agent-introspection/introspection.sqlite3` to
   `introspection.sqlite3.pre-<date>`. Rows are immutable; retired findings go dormant.
7. **Setup.** After the user confirms, `agent-introspection setup <options>`: config,
   preflight, `facts install` and `facts backfill`, `harness configure` for each
   enabled harness, `facts schedule install`, `dashboard install`, then `doctor`. It
   stops at the first failing step and names it (`stopped_at`, exit 30); fix that step
   and rerun, since every step is idempotent. If a reinstall drops a stored attribute,
   also run `OPTIMIZE TABLE introspection.spans FINAL` and
   `… introspection.logs FINAL`. The schedule:
   - macOS: a launchd agent labelled `dev.agent-introspection.sync`. Install retires
     the job an earlier release installed under another label (status reports it
     under `legacy`), moving its plist to
     `~/.local/share/agent-introspection/schedule-backups/`.
   - Linux: the systemd user units `agent-introspection-sync.service` and
     `agent-introspection-sync.timer` in `~/.config/systemd/user/`, and the dashboard's
     `agent-introspection-dashboard.service`. They stop when the user logs out unless
     lingering is on: install and status report `linger` but never enable it, so ask
     the user to run `loginctl enable-linger "$USER"`.
   - Any other platform is refused.
8. **Restart** running sessions of each configured harness: they read their settings
   at start.
9. **First sync.** `agent-introspection facts sync`: `spool`, `labels`, `findings`,
   and `evaluations` report no error, and `labels` is not `skipped` when a key command
   is set. Hook records reach `hook_events` through SigNoz, so allow a minute before
   checking them.
10. **Doctor.** `agent-introspection doctor` exits 0 (no check fails), and each
    `warn`, `off`, or `info` has a reason the user accepts.
11. **Validate.** The [validation workflow](validation-workflow.md), then the
    `introspection-operations` health workflow over 90 days.
12. **Record** a reinstall in the commit or pull request message: date, commit, steps,
    row counts, parity results, and anything unresolved.

## Done when

- `doctor` passes, every step has evidence, and the Pipeline view
  is healthy for 90 days for every enabled harness (or each gap has a recorded reason).
