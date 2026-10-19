# Validation workflow

Prove a harness's capture works: first check configuration with
`agent-introspection doctor`, which runs nothing in a harness, then, only when the user
wants a live check, run one short session.

## Configuration check

`agent-introspection doctor` is read-only: it writes nothing, never runs the prompt
labels' key command, and checks secrets for presence only. It prints one JSON object
(`passed`, `summary`, and `checks`, each with `name`, `status`, `detail`, `fix`, and,
for a per-harness check, `harness`) and exits 30 when any check fails; the report
still prints.

| Status | Meaning                                                                                                                        |
| ------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `pass` | As expected.                                                                                                                   |
| `warn` | Works, but at risk or only partly (for example, a hooks endpoint the scheduled sync cannot see).                               |
| `fail` | A step is missing or wrong: follow `fix`, then rerun `doctor`.                                                                 |
| `off`  | An optional feature is not in use (prompt export, prompt labels, the dashboard service); turn it on only if the user wants it. |
| `info` | Nothing to fix here, for the record (for example, a harness version newer than the verified ones).                             |

The checks run in this order: `config`, `signoz` (every preflight check folded into
one, with each failing check's detail), `collector_http`, `collector_grpc`,
`hooks_endpoint`, and `collector_live` (with `--live`); then, per enabled harness,
`binary`, `config_files`, `export_settings`, `prompt_export`, `activity_hooks`,
`activity_shim`, `hook_config`, and `telemetry` (rows over 24 hours);
`disabled_harnesses`; then `spool`, `schedule`, `schedule_legacy`, `schedule_linger`,
`prompt_labels`, `dashboard`, and `dashboard_service`. Points to know:

- `collector_grpc` is `info` when port 4317 is closed: every harness profile exports
  over OTLP/HTTP, so only an exporter set to OTLP/gRPC by hand needs it. When an
  enabled harness's export settings are satisfied over gRPC, it warns.
- An exporter that reaches the local collector by another route (OTLP/gRPC on 4317,
  or OTLP/HTTP on 4318 or the configured port) satisfies the export settings, as it
  does for `harness configure`; `127.0.0.1` and `localhost` both name this machine.
- On an additional machine (`[machine] role = "additional"`), the checks that belong
  to the host are `info` and `host_collector` and `spool_sync` take their place; the
  [additional machines workflow](additional-machines-workflow.md) lists them.
- Config files checked: the ones `harness configure` writes. A set `CLAUDE_CONFIG_DIR`
  or `CODEX_HOME` names the file even when it does not exist yet, and `config_files`
  then fails as missing. Codex homes: every configured home that is set or exists,
  plus every directory the codex profile's `home_globs` matches (Orca's account homes).
- `prompt_export` reads a file's first prompt-export setting as its switch (Claude
  Code's `OTEL_LOG_USER_PROMPTS`, Codex's `log_user_prompt`). While the switch is not
  on, the check is `off`, whatever the file's other settings hold. It warns when the
  switch is on and another setting is unset, such as `OTEL_LOG_USER_PROMPTS=1` without
  `OTEL_LOG_ASSISTANT_RESPONSES=0`, which also exports assistant response text, and
  when some Codex homes export prompts and others do not: its detail names the homes
  that do, and its fix has a `--root` for each home that differs. A value set by hand
  beside a switch that is on, such as `OTEL_LOG_ASSISTANT_RESPONSES=1` (assistant
  responses exported on purpose), is `info`: its detail names the value, and its fix
  says what `--prompt-export` would write over it. Leave it if the user chose it.
- `prompt_labels` reads only `[labels] openrouter_key_command`: a key in the shell or
  omp's stored credential is never used.
- `disabled_harnesses` warns when a registry harness left out of `[harnesses] enabled`
  sent rows this week: the dashboard and findings leave its data out.
- The dashboard needs ClickHouse's HTTP interface at a local address: a port published
  on loopback (for example with the opt-in
  `scripts/signoz/docker-compose.clickhouse-http.yaml`) or a container name the runtime
  resolves.
- When ClickHouse is unreachable, `schedule` is `info` (this is not the SigNoz host),
  and `telemetry` and `drift` report that they were not checked.

Options:

- `doctor --live` adds `collector_live`: it sends one empty OTLP/HTTP logs request,
  which stores nothing, to the hooks endpoint (or `<otlp_endpoint>/v1/logs` when none
  is set), proving the collector accepts records from this machine.
- `doctor --drift` adds `drift`: each enabled harness's telemetry against its saved
  fingerprint (see the producer onboarding workflow's
  [Watch](producer-onboarding-workflow.md#watch) step), and `drift_unnamed` when rows
  arrive with an empty `service.name`.

Beyond `doctor`, check by hand:

- Each changed file has a backup (`harness configure` prints each backup path, for its
  settings and its hooks).
- `~/.omp/agent/config.yml` does not list the omp activity extension.

## Live check

1. In each harness, start a fresh session, submit a harmless prompt, run one command
   that fails (for example reading a missing file), and let the turn finish. For Codex
   exec, run one `codex exec` prompt.
2. After the next minute (the host's `facts sync`, or an additional machine's
   `spool sync` timer; run either now), check:
   - the spool backlog (`*.json` in `~/.local/share/agent-introspection/hook-inbox/`)
     drains to 0 and `hook-inbox/rejected/` stays empty
     (`agent-introspection spool status` counts both);
   - a probe record (the session's first hook event) is in
     `introspection.hook_events FINAL` with this machine's `host`;
   - the prompt event (`user_prompt`, `codex.user_prompt`, `omp.user_prompt`) reached
     SigNoz with text, and `introspection.prompt_labels` has a `labelled` row for it,
     while `introspection.logs` holds no `prompt` key;
   - Claude Code and omp: a `hook_events` `tool_call` whose `tool_calls` row carries
     `command_head` and `arguments_hash`; Claude Code: `prompt_submitted`,
     `tool_failure`, and `turn_stop` with `reasoning_tokens`;
   - no hook record holds prompt, argument, or output text, and `hooks.log` has no new
     failures.
3. In the dashboard, the Pipeline coverage grid shows the harness's routes healthy, and
   the session drill-down shows the task's type and correction flag.

## Done when

- `doctor` passes and each harness passes the live check, or each gap is explained (a
  signal no route can reach goes to the `introspection-operations` change workflow).
