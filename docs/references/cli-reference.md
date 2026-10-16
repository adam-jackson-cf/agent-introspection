# CLI Reference

Use this catalog to find the `agent-introspection` command for a task. Every command
except `hook` writes one JSON object to stdout, writes diagnostics to stderr, and fails
with a stable non-zero exit code (see [Exit codes](#exit-codes)).

Run commands as `uv run agent-introspection …` from the repo, or as
`agent-introspection …` from the standalone copy installed by
`uv tool install --force --reinstall .` (the copy the scheduled job and the activity
hooks run). The global `--config PATH` option, or `AGENT_INTROSPECTION_CONFIG`, selects
a config file other than `~/.config/agent-introspection/config.toml`.

On an additional machine (`[machine] role = "additional"`, which has no SigNoz), every
command that needs ClickHouse exits 10 with a message saying to run it on the host:
the `facts` commands except `facts schedule status` and `remove`, the `producers`
commands, `candidates export`, `proposal draft`, and `proposal evaluate`.

## Facts

| Command                                  | What it does                                                                                                                                                                                                                                                 | When to use it                                      |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------- |
| `facts verify [--compare]`               | Builds the whole store in a scratch database, then drops it, so a statement ClickHouse rejects fails here. `--compare` instead rebuilds the live snapshots now and checks each against a full read of its view; a mismatch prints every result and exits 30. | After an install, or on demand before one.          |
| `facts install`                          | Verifies in a scratch database, then creates tables, loaders, views, and snapshots, loads the signal registry and `[harnesses] enabled`, and creates or migrates the workflow store.                                                                         | First install; after any change under `facts_sql/`. |
| `facts refresh <refresher>`              | Refreshes and waits for one named loader or refresher (a name from `facts status`), retrying once (not when still running after 30 min). An unknown name exits 50.                                                                                           | Filling one empty table without a full rebuild.     |
| `facts backfill [--days 90]`             | Re-projects everything SigNoz retains, including hook events.                                                                                                                                                                                                | After a span or log projection change.              |
| `facts status`                           | Loader state and per-harness freshness.                                                                                                                                                                                                                      | Health checks.                                      |
| `facts findings`                         | Promotes failure clusters into workflow findings.                                                                                                                                                                                                            | Refreshing findings on demand.                      |
| `facts sync [--log]`                     | `spool sync`, then the hook-event loader, prompt labels, findings, and due proposal evaluations. `--log` writes the summary and errors, with a UTC timestamp, to the rotated `~/.local/share/agent-introspection/sync.log` instead of stdout and stderr.     | What the schedule runs every minute, with `--log`.  |
| `facts classify [--days 2] [--limit 60]` | Labels exported prompts now. The OpenRouter key is what `[labels] openrouter_key_command` prints; without that command, or when it prints nothing, labels are skipped with the reason.                                                                       | On-demand prompt labelling.                         |
| `facts schedule install\|remove\|status` | Manages the SigNoz host's job that runs `facts sync --log` every minute: a launchd agent on macOS, a systemd user timer on Linux. Other platforms exit 60.                                                                                                   | Install, uninstall, or check the schedule.          |

### Schedule

- **macOS:** the launchd agent `dev.agent-introspection.sync`, in
  `~/Library/LaunchAgents/dev.agent-introspection.sync.plist`, runs at load and every
  60 seconds. `install` and `remove` also stop the job earlier releases installed under
  another label and move its plist to
  `~/.local/share/agent-introspection/schedule-backups/`, outside `LaunchAgents` so
  launchd never loads the backup; `status` reports that job under `legacy` until then.
  Reinstall the standalone CLI first: an older copy installs the old label again.
  When `launchctl bootout` cannot stop a loaded job (as over SSH, outside the user's
  GUI session), `install` and `remove` stop with an error (exit 60) that says the job
  is still loaded, and leave its plist as it was, so two jobs never sync at once and
  a running job is never reported stopped.
- **Linux:** the systemd user units `agent-introspection-sync.service` and
  `agent-introspection-sync.timer` in `~/.config/systemd/user/` (`OnCalendar=minutely`,
  `Persistent=true`). `install` backs up units it replaces to the same backups folder.
  When `systemctl --user disable --now` cannot stop an enabled or active timer (for
  example, when the user bus is unreachable), `remove` stops with an error (exit 60)
  that says the timer is still scheduled, and leaves both units in place.
  A user timer stops when the user logs out unless lingering is on:
  `loginctl enable-linger "$USER"`. `install` and `status` report `linger`; the command
  never turns it on. These names differ from the `agent-introspection-spool-sync` units
  that machines without SigNoz run. `install` refuses an additional machine (exit 10).
- **Both:** the job runs the `agent-introspection` executable of the environment that
  ran `install`, so run it from the standalone copy. It passes `--config` when one was
  given, and sets only `PATH`: the system directories plus the directories of the
  `clickhouse_password_command` and
  `[labels] openrouter_key_command` executables, and of omp when no key command is
  set. Variables from your shell, such as
  `OTEL_EXPORTER_OTLP_*`, never reach it, and `install` refuses
  `clickhouse_password_env`. A job that fails to start puts back the one it replaced,
  running again only if it was running before: a job you stopped (`launchctl bootout`,
  or `systemctl --user stop` or `disable`) stays stopped, and the error says so.
- **Output:** `install` reports `installed`, `scheduler`, and `interval_seconds`;
  `remove` reports `removed` and `scheduler`; `status` reports `scheduler`,
  `installed`, `loaded` (on Linux, the timer is active), `inbox_backlog`, and
  `inbox_rejected`. On macOS each adds `label` and `legacy`; on Linux each adds `unit`,
  `install` also `backups` and `linger`, and `status` also `enabled` and `linger`.
- A `launchctl` or `systemctl` call that hangs fails after 30 seconds (exit 70).

## Spool

| Command        | What it does                                                                                                                                                        | When to use it                                                                                                       |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `spool sync`   | Records a spool-status heartbeat and sends the spool to the collector's `/v1/logs` (or `[hooks] otlp_logs_endpoint`). Records it cannot send wait for the next run. | Machines without SigNoz run it every minute from the onboarding skill's units; the host runs it inside `facts sync`. |
| `spool status` | Read-only: pending and rejected spool files, the cumulative `sent_total` per record family, and `last_error_class`.                                                 | Checking delivery.                                                                                                   |

## Onboarding

These commands identify producers, configure harnesses, and diagnose each install
step. [Producer Onboarding](producer-onboarding.md) explains the lifecycle they serve.

| Command                                                                             | What it does                                                                                                                                                                                                                                                                                                                                                                                         | When to use it                                                          |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `signoz detect`                                                                     | Read-only: SigNoz and ClickHouse containers, the local ports a SigNoz publishes, and whether ClickHouse answers on loopback, with notes on the next step. Reads no config, never changes anything, and always exits 0.                                                                                                                                                                               | First step on a new machine; before the guided SigNoz install.          |
| `producers discover [--days 7]`                                                     | Lists every `service.name` with spans or logs in SigNoz over the window, classed `registered`, `known_producer`, `internal`, or `unregistered`, with row counts and first and last seen (UTC).                                                                                                                                                                                                       | Finding what sends telemetry; the first step for an unknown producer.   |
| `producers profile <service> [--days 7] [--write PATH] [--force]`                   | Explores one service by name: span names, log event names, resource keys, and attribute keys with row counts, never values. Flags content-bearing keys, guesses each capability from its hints, and drafts a candidate profile with its validation, projection, and parity impact. `--write` saves the draft as TOML; `--force` lets it replace a file.                                              | Exploring and assessing an unknown producer.                            |
| `producers fingerprint [--days 7] [--save] [service ...]`                           | Records each service's span names, event names, and attribute keys with row counts; the default services are the enabled harnesses. `--save` writes each to `~/.local/share/agent-introspection/fingerprints/<service>.json`.                                                                                                                                                                        | Saving a baseline after onboarding or an upgrade, for `doctor --drift`. |
| `harness list [--no-version]`                                                       | Every bundled harness profile: key, title, `status` (`registered` or `candidate`), registry keys, service names, whether this machine enables it (`enabled`, `enabled_keys`), its binary and whether it is on `PATH` (`binary_found`, `binary_path`), the version it prints (`installed_version`, `version_verified`), `verified_versions`, and `hook_adapter`.                                      | Choosing harnesses.                                                     |
| `harness configure <key> [--dry-run] [--prompt-export] [--force] [--root PATH ...]` | Writes a registered harness's export settings into its own config files, then installs its activity hooks, backing up each changed file first. `--dry-run` reports the changes and writes nothing; `--prompt-export` also writes the opt-in prompt-export settings; `--force` rewrites a setting that already reaches the collector over the other OTLP transport; `--root` adds a config directory. | Switching on a harness's export; run `--dry-run` first.                 |
| `harness remove-hooks <key> [--dry-run]`                                            | Takes a harness's activity hooks out (Claude Code's settings entries, the omp plugin link) and keeps its export settings.                                                                                                                                                                                                                                                                            | Turning the hooks off.                                                  |
| `harness impact <key, service, or profile file>`                                    | Previews strict parity: the signals visible now with `[harnesses] enabled`, those still visible if the producer were enabled too, and each signal it would hide, with the outcome, the capabilities in the way, and the registry's note. Accepts a draft profile file.                                                                                                                               | Before enabling a producer.                                             |
| `dashboard install [--repo PATH]`                                                   | Builds the dashboard in a checkout (default: the current directory) and (re)starts its user service on `127.0.0.1:4173`: a launchd agent on macOS, a systemd user service on Linux. Refuses an additional machine, a ClickHouse password, or a path that is not a checkout.                                                                                                                          | First install, and after every update of the checkout.                  |
| `dashboard status` / `dashboard remove`                                             | Whether the dashboard service is installed and running (read-only); stop it and delete its file.                                                                                                                                                                                                                                                                                                     | Checking or turning off the dashboard.                                  |
| `doctor [--live] [--drift]`                                                         | Read-only checks of every onboarding step, each with a status and a `fix`. `--live` also sends one empty OTLP/HTTP logs request, which stores nothing; `--drift` also compares each enabled harness with its saved fingerprint.                                                                                                                                                                      | After each install step, and whenever something looks wrong.            |

### signoz detect

- Fields: `docker` (`cli`, `context`, `reachable`), `containers` (running containers
  whose name or image mentions SigNoz or ClickHouse: `name`, `image`, `ports`), `ports`
  (whether each of 4317, 4318, 8123, and 8080 accepts connections on `127.0.0.1`),
  `clickhouse_ping` (ClickHouse answered `/ping` on `127.0.0.1:8123`),
  `signoz_present` (such a container runs, or ClickHouse answered), and `notes`.
- It probes `127.0.0.1` only, sends `/ping` without a proxy, and gives `docker ps` 10
  seconds. A missing Docker CLI or a daemon that does not answer is a note, not an
  error. See [SigNoz Connection](signoz-connection.md) for what to do next.

### producers

- Every query aggregates in ClickHouse, reads names and counts only, and stops after
  120 seconds. Names show home directories as `~`. A busy producer may need
  `--days 1`. `--days` below 1 is a usage error (exit 2).
- `discover` classes: `registered` (a registry `[harnesses]` key, with an `enabled`
  boolean), `known_producer` (in the registry's `[producers]`), `internal` (this tool's
  own records), and `unregistered` (anything else). `enabled` is null for services that
  are not harnesses. Each entry names the bundled `profile` that covers it, if any.
  Services are ordered by class, then by rows. `enabled_not_seen` lists enabled
  harnesses with no rows in the window. Rows with an empty or blank `service.name` are
  one `unregistered` entry named `(empty)`, with a `note`: no command can select them,
  so set the sender's `OTEL_SERVICE_NAME`. `doctor --drift` never names `(empty)` as
  an unregistered service.
- `profile` keeps the 200 names with the most rows in each list, with `distinct` and
  `dropped` counts, and writes one stderr line per list it cut. `content_keys` lists
  string attribute keys whose last word suggests user text (prompt, command, arguments,
  output, message, query, and similar); their values are never read. Each capability
  guess is `native` when a hint matched, else `unverified`, with the matching names as
  evidence: a lead to check, not a verification. A hint can match an unrelated key.
- The draft profile leaves `binary`, `version_args`, `verified_versions`, and the
  session store empty, so `draft_check` reports `valid: false` and lists them under
  `to_complete`. Fill them before `harness impact <file>` can load the draft.
- `--write` refuses, before any query (exit 50): a path inside the bundled
  `harness_profiles` package, a directory, an existing file without `--force`, or a
  path whose parent directory is missing.
- `fingerprint` keeps up to 5,000 names per list, and a `truncated` map counts the
  rest. Attribute keys are `<source>.<kind>:<key>`, such as `spans.string:tool.name`.
  `--save` writes each file atomically and refuses, before any query, a service name
  that cannot be a file name (exit 50), so nothing is saved.

### harness

- `<key>` is a profile key (`claude-code`, `codex`, `omp`), a registry harness key, or
  a service name; `codex` covers the three Codex surfaces. `harness impact` also takes
  the path of a profile file, such as a draft from `producers profile --write`.
- `list` runs each binary it finds on `PATH` with the profile's version arguments, for
  at most 5 seconds each. `version_verified` says whether that version is one of
  `verified_versions`. Both are null when the version is unknown, and `version_note`
  says why when the binary was found: not run (`--no-version`, or a profile with no
  version arguments), failed, or printed no version. `--no-version` runs no binary.
- `configure` refuses a candidate producer, an unknown name, a `--root` that is not a
  directory, and a config file it cannot parse or edit safely (exit 50).
- `configure` points a harness at the collector `otlp_endpoint` reports: `[signoz]
otlp_endpoint` (default `http://127.0.0.1:4318`), or on an additional machine
  `[machine] export_endpoint`, the host's collector. Both are OTLP/HTTP base URLs
  (config refuses port 4317), so every harness gets an OTLP/HTTP exporter.
- It writes the files `doctor` reads: a set `CLAUDE_CONFIG_DIR` or `CODEX_HOME` names
  the file to write even before the harness has created it there.
- Each managed key reports an action: `add`, `change`, `unchanged`, or `satisfied`.
  Only managed keys are printed, and their values as `doctor` shows them: a URL without
  credentials, query, or fragment. Parts the profile does not manage, such as exporter
  headers, show as `(not shown)`.
- **Transport equivalence:** a setting whose profile value templates the collector
  endpoint or protocol is `satisfied` by an exporter that already reaches the
  collector another way: OTLP/gRPC on 4317, or OTLP/HTTP on 4318 or the configured
  endpoint's port. Its URL must name the collector's machine: any local name
  (`127.0.0.1` and `localhost` alike) for a local collector, the host's own name on an
  additional machine. It is left alone unless `--force`, which writes the profile's
  value. `doctor` applies the same rule.
- **Prompt export** is opt-in. Without `--prompt-export`, those settings are reported
  with `opt_in` and `on` and never changed. With it, prompt text is kept in SigNoz for
  its retention, so confirm with the user first. For Claude Code it also writes
  `OTEL_LOG_ASSISTANT_RESPONSES=0`, which keeps assistant responses redacted, and
  writes over a value set by hand (`--dry-run` shows it as a `change`). `doctor`
  reads a file's first prompt-export setting as its switch: while that is not on,
  prompt export is `off`, whatever the others hold. It warns when the switch is on
  and another setting is unset (`OTEL_LOG_USER_PROMPTS=1` alone), or when some Codex
  homes export prompts and others do not, naming the homes that do, with a `--root`
  in its fix for each home that differs. It reports a value set by hand beside a
  switch that is on (such as `OTEL_LOG_ASSISTANT_RESPONSES=1`) as `info`, with what
  `--prompt-export` would write over it.
- **Writes:** each changed file is first copied to `<file>.bak-<UTC timestamp>` beside
  it (`%Y%m%dT%H%M%S%fZ`). The write is atomic and keeps the file's permissions, and a
  second run changes nothing. JSON keeps its indentation. TOML is edited line by line;
  a table that cannot be (sub-tables, dotted keys, or a value across lines) is
  rewritten as one table of inline values, its comments move above the key each
  described, and that file's `notes` say so. A table written as an inline or dotted key
  of its parent is refused. dotenv keeps `export` prefixes and each rewritten line's
  trailing comment.
- **`--root PATH`:** an extra config directory, configured as `PATH/<file name>`. For a
  file kept in each root (Codex homes) it is added to the existing roots; for a file
  configured once (Claude Code, omp) it replaces the profile's paths. Both `--root a b`
  and a repeated `--root` work.
- **Activity hooks:** after the export settings, `configure` installs the harness's
  hooks from the CLI package and reports each action under `hooks`: `runtime` (the
  shim and omp's plugin package copied to
  `~/.local/lib/agent-introspection/activity-hooks-v1/`), `settings` (Claude Code's
  hook entries in the settings file it wrote, other tools' entries kept), or `plugin`
  (omp's `omp plugin link`, which needs omp on `PATH`). A file this run already wrote
  gets no second backup. `--dry-run` reports them and writes nothing.
- `next_steps` says to restart the harness's sessions when something changed.
- `impact` adds `status` and `projection` (a count of each outcome per surface, or under
  the profile key for a candidate). For a producer this machine already enables, it
  adds `already_enabled`, a `note`, and `hidden_now`.

### doctor

- Output: `passed`, a `summary` count per status, and `checks`, each with `name`,
  `status`, `detail`, and `fix`; per-harness checks add `harness` (the profile key).
  Any `fail` exits 30 after the report prints.
- Statuses: `pass`, `warn`, `fail`, `off` (an optional feature not in use), and
  `info`.
- Order: `config`; `signoz` (every preflight check folded into one, each failing
  check with its detail);
  `collector_http`, `collector_grpc`, `hooks_endpoint`, and `collector_live` (with
  `--live`); for each enabled harness profile `binary`, `config_files`,
  `export_settings`, `prompt_export`, `activity_hooks`, `activity_shim`, and
  `telemetry`; `disabled_harnesses` (a registry harness left out of `[harnesses]
enabled` that sent rows this week); then `spool`, `schedule`, `schedule_legacy`,
  `schedule_linger` (Linux), `prompt_labels`, `dashboard` (the ClickHouse URL it reads),
  and `dashboard_service` (`off` when not installed, else whether it answers on port
  4173); then `drift` and
  `drift_unnamed` (with `--drift`).
- **Additional machine** (`[machine] role = "additional"`): `signoz`, `schedule`,
  `prompt_labels`, `dashboard`, `drift`, and each harness's `telemetry` are `info`,
  left to the host with the reason, and nothing asks ClickHouse. `host_collector`
  (whether `[machine] export_endpoint` accepts connections) replaces `collector_http`
  and `collector_grpc`; `hooks_endpoint` warns when it names a machine other than
  the host's collector; `export_settings` is judged against the host's collector; and
  `spool_sync` checks the onboarding skill's `agent-introspection-spool-sync.timer`
  (with `schedule_linger`) on Linux, or is `info` elsewhere, where no scheduler is
  bundled.
- A config that does not load is a failing `config` check, and the rest run on the
  defaults. A check that raises is reported as a failure naming the exception class,
  and the others still run.
- `--live` posts an empty OTLP/HTTP logs request to the logs endpoint records go to.
  Without it, no request carries data.
- Config files checked: the ones `harness configure` writes. A file named by a set
  `CLAUDE_CONFIG_DIR` or `CODEX_HOME` that does not exist fails `config_files` as
  missing. Codex homes: every configured root that is set or exists, plus every directory
  the codex profile's `home_globs` matches (Orca's per-account homes).
- `prompt_labels` reads only `[labels] openrouter_key_command`, the one key source.
  Secrets are checked for presence only, and key commands are never run.
- `activity_shim` warns when the installed shim differs from the one the CLI ships:
  rerun `harness configure` after updating the CLI.
- When ClickHouse is unreachable, the machine is treated as not the SigNoz host: a
  missing schedule is `info`, and `telemetry` and `drift` report that they were not
  checked.
- `--drift` reports each enabled harness as unchanged, drifted (names added or
  removed), without a baseline, or with an unreadable one, and warns about each service
  that sends telemetry but is not registered. `drift_unnamed` (`info`) counts the rows
  with an empty `service.name`, which no harness can claim: set `OTEL_SERVICE_NAME` on
  the producer that sends them. See
  [Producer Onboarding](producer-onboarding.md#fingerprints-and-drift).

## Proposals

| Command                                                                        | What it does                                                                                  | When to use it                    |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- | --------------------------------- |
| `candidates export --reserved-model-budget <tokens> [--batch-id ID]`           | Picks the highest-impact actionable finding without a proposal and reserves a review session. | Feeding an external drafter.      |
| `proposal draft --reserved-model-budget <tokens> [--dry-run] [--batch-id ID]`  | Exports a candidate and drafts its proposal with `codex exec`.                                | Drafting; run `--dry-run` first.  |
| `proposal create --input-json FILE`                                            | Imports a proposal through the validated transaction.                                         | Importing a drafted proposal.     |
| `proposal list`                                                                | Lists proposals.                                                                              | Reviewing state.                  |
| `proposal show <proposal-id>`                                                  | Shows one proposal.                                                                           | Reviewing one proposal.           |
| `proposal decide <proposal-id> approve\|reject --actor <name> --reason <text>` | Records a decision only.                                                                      | After the user decides.           |
| `proposal mark-applied <proposal-id> --actor <name> --input-json FILE`         | Records a user-applied proposal with validation evidence.                                     | After applying a change yourself. |
| `proposal evaluate [--now <ISO 8601 instant>]`                                 | Evaluates applied proposals whose window has elapsed.                                         | Normally run by `facts sync`.     |

## Hooks

| Command                   | What it does                                                                                                                                                                                                                | When to use it                                    |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `hook <producer> <event>` | Reads one activity-hook envelope from stdin and writes normalized records to the hook inbox; `<producer>` is `claude-code` or `omp`. Always exits 0 and prints nothing; failures go to the hook log by exception type only. | Run by the installed activity hooks, not by hand. |

## Exit codes

| Code | Meaning             | What triggers it                                                                                                                                                                                                                                                                                                                     |
| ---- | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 0    | Success             | The command finished, and any check it reports passed. `hook` and `signoz detect` always exit 0.                                                                                                                                                                                                                                     |
| 2    | Usage error         | An unknown command or option, a missing argument, or a `producers` `--days` below 1 (argparse prints the usage).                                                                                                                                                                                                                     |
| 10   | Configuration error | The config file does not load, or `[harnesses] enabled` names an unknown harness, for every command that reads it except `doctor`, which reports it as a failing check and exits 30.                                                                                                                                                 |
| 30   | Facts error         | ClickHouse cannot be reached or rejects a statement, including the `producers` queries and a refresh still running after 30 minutes. Also any failing check of `facts verify` or `doctor`, after the full report prints.                                                                                                             |
| 40   | Database error      | The workflow store (SQLite) fails.                                                                                                                                                                                                                                                                                                   |
| 50   | Validation error    | Input the command refuses: an unknown refresher or harness profile, a candidate given to `harness configure`, a `--root` that is not a directory, a config file that cannot be parsed or edited safely, an invalid profile file, a `--write` refusal, a fingerprint service name that cannot be a file name, or an invalid proposal. |
| 60   | Conflict            | A state that blocks the command: a proposal that changed while a transition was being written, a `proposal draft` run that fails, `facts schedule` on a platform other than macOS or Linux, a scheduler that refuses the job, a missing executable to schedule, or `clickhouse_password_env` given to `facts schedule install`.      |
| 70   | Internal error      | Anything unexpected, including a `launchctl` or `systemctl` call that times out after 30 seconds.                                                                                                                                                                                                                                    |

## Notes

- Nothing is applied by this tool. See [Proposal Lifecycle](proposal-lifecycle.md) for
  selection, the success metric, and `mark-applied` evidence.
- Source: `src/agent_introspection/cli.py` and the `cli_*.py` modules beside it.
