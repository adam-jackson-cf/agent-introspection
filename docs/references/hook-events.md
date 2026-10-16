# Hook Events

Native producer telemetry leaves some dashboard signals unreachable for a harness.
Where a harness hook exposes the missing field, an **activity hook** derives a
normalized value from it and writes one JSON record to a local spool. The spool is
delivered to SigNoz over OTLP from every machine and loaded into the facts store from
there. This closes the gap without storing raw prompt, command, argument,
or output text (decision D4, in [Facts Store](facts-store.md#standing-decisions)). A signal that no hook or native field can supply for
every harness is excluded from the dashboard (see
[Dashboard Data Gaps](data-gaps.md)).

Prompt labels do not use hooks: see [Prompt export](#prompt-export).

## Route

```text
harness hook (Claude Code settings, omp extension)
  └─> activity shim: reads the hook envelope on stdin, returns at once,
      hands the envelope to a detached `agent-introspection hook <producer> <event>`
        └─> normalizer (src/agent_introspection/hooks.py): derives fields, drops text
              └─> spool: ~/.local/share/agent-introspection/hook-inbox/<event_id>.json
                    └─> OTLP/HTTP logs POST (the collector's /v1/logs)
                          └─> SigNoz collector ─> signoz_logs
                                └─> loader load_hook_events (every minute)
                                      └─> introspection.hook_events
                                            └─> fact views (tool_calls, task_outcomes, …)
```

Every machine runs the same route; there is no direct ClickHouse write. The record
families that share it are:

| Family       | Schema                               | `event.name`                       | Producer                   | Stored in                                          |
| ------------ | ------------------------------------ | ---------------------------------- | -------------------------- | -------------------------------------------------- |
| Hook event   | `agent-introspection.hook-event/1`   | `agent_introspection.hook_event`   | `agent-introspection hook` | `introspection.hook_events`                        |
| Spool status | `agent-introspection.spool-status/1` | `agent_introspection.spool_status` | each `spool sync`          | not stored; the Pipeline view reads it from SigNoz |

SigNoz holds older rows under the same `service.name` (`agent-introspection`) that
belong to a retired pipeline. Every reader of these logs (loaders, backfill, Pipeline
freshness and sanitization) therefore selects a family by `service.name`,
`event.name`, **and** the JSON body's `schema`, never by service name alone.

- Hooks never block the harness: the shim reads stdin, starts the normalizer in the
  background, and exits 0 with no output. A failing normalizer loses one record; it
  never fails a tool call or a prompt.
- Prompt text reaches the normalizer only from Claude Code's `UserPromptSubmit`, and
  only its length is kept.
- Tool arguments and outputs are read in memory and reduced to the fields below.
- The status heartbeat carries counts, the oldest pending age, and an exception class
  name only.

## Delivery

- A hook process writes its record atomically, then sends at most one batch of 50.
  `agent-introspection spool sync` (inside the SigNoz host's scheduled `facts sync`,
  from a systemd user timer on other machines) drains the rest within a 20-second
  budget under a non-blocking lock on `hook-inbox/.drain.lock`.
- The endpoint is `/v1/logs` on the collector this machine's harnesses export to
  (`[signoz] otlp_endpoint`, default `http://127.0.0.1:4318`, or `[machine]
export_endpoint` on an additional machine); `[hooks] otlp_logs_endpoint` overrides it
  with a full logs URL on an OTLP/HTTP port (never the gRPC port). The
  `OTEL_EXPORTER_OTLP_*` environment never redirects records. Records that reach
  SigNoz before `facts install` creates the loaders are loaded by `facts backfill` or
  the hourly 3-day sweep.
- Outcome per batch: a 2xx with an empty body, `{}`, or no rejected count deletes the
  files. `partialSuccess.rejectedLogRecords` above zero, or a 400 carrying an OTLP
  status, moves the batch to `hook-inbox/rejected/` (it may hold accepted records;
  duplicates are harmless because readers use `FINAL`). A 404, 405, 415, or other 4xx
  except 429 is class `endpoint_misconfigured`: files stay and the Pipeline view shows
  the class. A 429, 5xx, timeout, or connection error keeps the files for the next
  pass. `hooks.log` records only `<UTC> spool <class> <count>`.
- A collector 2xx is not durable storage: a collector restart between its ack and the
  ClickHouse write can lose a batch while the backlog reads 0. That exposure is
  accepted and detected, not prevented: each status heartbeat carries cumulative sent
  counts per family (`spool sync`, under one drain lock, deletes unsent older
  heartbeats, drains records, snapshots, then sends the heartbeat as its own request, so
  a heartbeat never counts a record observed after it, except for a microsecond race with
  a hook drain that the delivery check's 2-record floor absorbs;
  heartbeats from before this ordering can show a first-window gap for up to 24 hours)
  and the Pipeline view compares their 24-hour growth with the records SigNoz received per host, flagging a host when received falls short of sent by more than 2 records or 2%
  (`GAP_FLOOR = 2`, `GAP_FRACTION = 0.02` in `dashboard/server/record-route.ts`).

### Lag

| Stage                             | Typical lag                                                                                      |
| --------------------------------- | ------------------------------------------------------------------------------------------------ |
| Hook to SigNoz (online)           | seconds (one batch from the hook process)                                                        |
| Hook to SigNoz (offline or timer) | up to the timer interval (1 minute), or the outage                                               |
| SigNoz to `hook_events`           | up to 1 minute (loader), 30-minute window by arrival time                                        |
| Late records                      | hourly sweep re-reads the last 3 days by arrival time                                            |
| Hook-derived facts in snapshots   | up to the 10-minute rebuild; hooks over 8 days late wait for the 00:35-01:35 UTC history rebuild |

## Record

Every record shares one envelope; `event_type` selects the fields in `attrs`.

```json
{
  "schema": "agent-introspection.hook-event/1",
  "event_id": "<sha256 of producer, session_id, event_type, occurred_at, key>",
  "producer": "claude-code | omp",
  "session_id": "<native session ID>",
  "event_type": "tool_call | tool_failure | prompt_submitted | turn_stop | turn_failure | subagent_start | approval | retry | steer",
  "occurred_at": "<RFC 3339 with offset>",
  "attrs": {}
}
```

| `event_type`       | Producers        | Hook                                                          | `attrs`                                                                                                                                                                                                                                                                                                          |
| ------------------ | ---------------- | ------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tool_call`        | claude-code, omp | Claude `PreToolUse`; omp `tool_call`                          | `tool_use_id`, `tool_name`, `arguments_hash`, `arguments_length`, `command_head`, `command_sub`, `segments`, `verification_kinds` (list, sorted), `gate_run` (0/1), `targets` (list, home as `~`, at most 20), `gate_bypass` (0/1), `workdir` (the `workdir`/`cwd` argument, else the hook's `cwd`; home as `~`) |
| `tool_failure`     | claude-code, omp | Claude `PostToolUseFailure`; omp `tool_result` with `isError` | `tool_use_id`, `tool_name`, `failure_signature` (most specific diagnostic line, digits as `N`, ≤ 80 chars), `failure_signature_hash` (16 hex of SHA-256 over the uncapped normalized line), `interrupted` (0/1), `exit_code` (when the harness reports one)                                                      |
| `prompt_submitted` | claude-code      | `UserPromptSubmit`                                            | `prompt_id`, `prompt_length`, `turn_open` (0/1: a turn was still running, see [Turn state](#turn-state)); used to infer interrupts and steers                                                                                                                                                                    |
| `turn_stop`        | claude-code      | `Stop`                                                        | `prompt_id`, `reasoning_tokens` (sum of `thinking_tokens` over the turn's assistant messages in the transcript), `response_model`                                                                                                                                                                                |
| `turn_failure`     | claude-code      | `StopFailure`                                                 | `prompt_id`, `error_class`                                                                                                                                                                                                                                                                                       |
| `subagent_start`   | claude-code      | `SubagentStart`                                               | `agent_id`, `agent_type`, `prompt_id`                                                                                                                                                                                                                                                                            |
| `approval`         | omp              | `tool_approval_resolved`                                      | `tool_use_id`, `tool_name`, `approved` (0/1)                                                                                                                                                                                                                                                                     |
| `retry`            | omp              | `auto_retry_start` / `auto_retry_end`                         | `attempt`, `phase` (`start`/`end`), `success` (0/1 on end)                                                                                                                                                                                                                                                       |
| `steer`            | omp              | `input` while the agent is not idle                           | `prompt_length`                                                                                                                                                                                                                                                                                                  |

Codex emits tool identity, approvals, interrupts, steers, retries, and delegations
(`codex.agent_communication` spawn) natively, so it has no activity hook.

## Normalization

`hooks.py` mirrors the log projection's Codex derivations so the fields mean the same
thing for every harness:

- `command_head` / `command_sub`: the program and subcommand of the significant segment,
  from `command_shape.py` (mirrored in `facts_sql/command_shape.sql`). The command splits
  on `&&`, `||`, `;`, `|`, and newlines (quotes are not honoured); leading environment
  assignments, `env`, and `exec` are dropped; `cd`, `export`, `set`, and `source`
  segments are skipped; up to three wrappers (`uv run`, `npx`, `sudo`, `python -m`,
  `bash script.sh`, `bash -c "..."`, ...) are unwrapped, each along with the flags that
  follow it. The head is the basename of the resolved program; the subcommand is the
  second token when it looks like one, the script name for `npm run <script>`, and the
  file name for `python file.py`.
- `segments`: the number of non-empty command segments.
- `verification_kinds`: sorted distinct kinds over all segments (`unit_test`,
  `integration_e2e`, `smoke`, `lint_format`, `typecheck`, `ci_remote`), from program names
  and package-script aliases. `gate_run` is 1 for a full local gate (CI or check-all
  scripts, `pre-commit`, `npm run check`, `make ci`); it is a flag, not a kind. The
  command text is not stored.
- `targets`: file paths from edit tools, `path`/`file_path` arguments, and path-like
  command arguments (a slash or a known file extension).
- `gate_bypass`: `--no-verify`, `HUSKY=0`, `SKIP=`, or `--no-gpg-sign` anywhere in
  the arguments.
- `arguments_hash`: SHA-256 of the canonical JSON arguments, first 16 hex digits. It
  is compared only within one harness, so it need not equal Codex's `cityHash64`.
- `failure_signature`: the stack trace's final exception line, else the first
  diagnostic line that is not a generic header or frame, else the first diagnostic
  line; home as `~`, digit and hex runs as `N`, at most 80 characters.
- `failure_signature_hash`: first 16 hex digits of SHA-256 over the full normalized
  line (after redaction, home and digit normalization, before the 80-character cap);
  empty when the signature is empty. The ClickHouse projections compute the same value
  with `lower(substring(hex(SHA256(line)), 1, 16))`.

Parity notes: ClickHouse measures `arguments_length` in bytes and caps the signature in
characters (`substringUTF8`), as `hooks.py` does; `hooks.py` measures `arguments_length`
in UTF-8 bytes. `command_sub` is kept only for tools whose second token is a
subcommand (`command_shape.SUBCOMMAND_HEADS`, rendered into the SQL), so `echo word` keeps none.

## Turn state

Claude Code has no "turn running" flag on its prompt hook, so
`hooks.py` keeps one marker file per session in
`~/.local/share/agent-introspection/hook-state/<sha256(producer, session_id)>.turn`,
holding only the prompt ID and the time it opened. A prompt reads the marker
(`turn_open`), then writes it; `Stop` and `StopFailure` remove it. A marker older
than six hours counts as closed.

## Install

`agent-introspection harness configure <harness>` installs a harness's hooks after its
export settings. The runtime ships in the CLI package
(`src/agent_introspection/hook_runtime/`): it copies the shim and omp's plugin package
to `~/.local/lib/agent-introspection/activity-hooks-v1/` (the shim mode 0755, each
replaced file backed up), so no hook runs a repository path, then registers the hooks.
A second run changes nothing; `--dry-run` reports the same actions and writes nothing;
`agent-introspection harness remove-hooks <harness>` takes them out again. `doctor`
warns when the installed shim differs from the one the CLI ships, so rerun
`harness configure` after updating the CLI. Failures are logged by exception type only
to `~/.local/share/agent-introspection/hooks.log`. On a machine without `facts sync`,
delivery also needs the `spool sync` timer (see the onboarding skill's
[additional machines workflow](../../.agents/skills/introspection-onboarding/references/additional-machines-workflow.md)).

### Claude Code

One entry per event in the `hooks` of the settings file `harness configure` writes
(`$CLAUDE_CONFIG_DIR/settings.json`, else `~/.claude/settings.json`), each running
`/bin/sh ~/.local/lib/agent-introspection/activity-hooks-v1/activity-shim.sh claude-code <Event>`
with a 10-second timeout. Entries are recognised by that command, so a rerun replaces
them and another tool's hooks are kept as they are. The shim prints nothing, so
`PreToolUse` never makes a permission decision.

| Claude event                       | Record             | Reads                                                                                      |
| ---------------------------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| `PreToolUse` (matcher `*`)         | `tool_call`        | `tool_name`, `tool_input`, `tool_use_id`, `cwd`                                            |
| `PostToolUseFailure` (matcher `*`) | `tool_failure`     | `tool_name`, `tool_use_id`, `error`, `is_interrupt`                                        |
| `UserPromptSubmit`                 | `prompt_submitted` | `prompt_id`, prompt length (for interrupt and steer inference)                             |
| `Stop`                             | `turn_stop`        | `prompt_id`; numeric usage and model of the turn's assistant messages in `transcript_path` |
| `StopFailure`                      | `turn_failure`     | `error` (kept only when it is an identifier)                                               |
| `SubagentStart`                    | `subagent_start`   | `agent_id`, `agent_type`, `prompt_id`                                                      |

### omp

`activity.ts` is an omp extension in a plugin package (`package.json` declares it
under `omp.extensions`). `harness configure omp` links it with
`omp plugin link ~/.local/lib/agent-introspection/activity-hooks-v1/omp`, which records
it in omp's plugin directory (`~/.omp/plugins`); nothing goes in
`~/.omp/agent/extensions/` or `config.yml`, so a `~/.omp` shared across machines never
names an extension a machine lacks. Updating the installed files needs no relink;
`harness remove-hooks omp` runs `omp plugin uninstall agent-introspection-activity`.

Each handler builds a small JSON envelope (`session_id` from `getSessionId()`, `cwd`,
and the fields below) and spawns the shim (`AGENT_INTROSPECTION_SHIM` overrides its
path) detached. Handlers never block, throw, or return a result.

| omp event                             | Record                                                | Envelope fields                                 |
| ------------------------------------- | ----------------------------------------------------- | ----------------------------------------------- |
| `tool_call`                           | `tool_call`                                           | `toolCallId`, `toolName`, `input`               |
| `tool_result` with `isError`          | `tool_failure`                                        | `toolCallId`, `toolName`, error text, exit code |
| `before_agent_start`                  | none; sends an `omp.user_prompt` OTLP log (see below) | `prompt`                                        |
| `tool_approval_resolved`              | `approval`                                            | `toolCallId`, `toolName`, `approved`            |
| `auto_retry_start` / `auto_retry_end` | `retry`                                               | `attempt`, `success`                            |
| `input` while `ctx.isIdle()` is false | `steer`                                               | text length only is kept                        |

A `tool_result` that is an error hands its text to the shim in memory; `hooks.py` keeps
only the normalized failure signature, its hash, and the exit code. A failed spawn
appends `<time> omp <event> spawn <code>` to the hook log, with no payload.

## Prompt export

Task-type, correction, and sentiment labels come from the prompt text the producers
export to SigNoz, labelled in the app rather than in a hook:

| Harness              | Export setting                                                                                                                                                          | Event               |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| Claude Code          | `OTEL_LOG_USER_PROMPTS=1`, with `OTEL_LOG_ASSISTANT_RESPONSES=0`, in the `env` of `$CLAUDE_CONFIG_DIR/settings.json`, else `~/.claude/settings.json`                    | `user_prompt`       |
| Codex (all surfaces) | `log_user_prompt = true` under `[otel]` in each `<codex-root>/config.toml`                                                                                              | `codex.user_prompt` |
| omp                  | The activity extension sends it on `before_agent_start` to omp's own OTLP endpoint (`OTEL_EXPORTER_OTLP_*`, loaded from `~/.omp/.env`), because omp has no prompt event | `omp.user_prompt`   |

omp's log carries the attributes `event.name`, `session.id`, `prompt`, and
`prompt_length`, with resource `service.name` from `OTEL_SERVICE_NAME` (default
`oh-my-pi`), posted as OTLP/JSON to `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`, else
`OTEL_EXPORTER_OTLP_ENDPOINT` + `/v1/logs`, with `OTEL_EXPORTER_OTLP_*HEADERS`. It sends
nothing until `AGENT_INTROSPECTION_OMP_PROMPT_EXPORT=1` is set in `~/.omp/.env`
(`harness configure omp --prompt-export`).

`facts sync` (`classify.run`) reads unlabelled prompts from SigNoz, sends each
redacted and clipped to Jev with the OpenRouter key that `[labels]
openrouter_key_command` prints (the one key source), and stores only the answers in
`introspection.prompt_labels`; a failed decision is retried up to three times, and
`facts classify --days N` labels on demand. The facts loader drops the `prompt`
attribute, so prompt text is stored only in SigNoz's own tables, for their 90-day
retention, where the SigNoz UI and anything else reading that instance can see it.
Prompts exported before the settings were switched on are a `REDACTED` placeholder
and cannot be labelled.

Claude Code's `OTEL_LOG_USER_PROMPTS` also exports the text of every assistant
response on `assistant_response` events while `OTEL_LOG_ASSISTANT_RESPONSES` is unset,
so `harness configure claude-code --prompt-export` sets that to `0` and `doctor` warns
when `OTEL_LOG_USER_PROMPTS=1` is set without it. While `OTEL_LOG_USER_PROMPTS` is not
`1`, `doctor` reports prompt export `off`, whatever `OTEL_LOG_ASSISTANT_RESPONSES`
holds. Beside `OTEL_LOG_USER_PROMPTS=1`, `doctor` reports a value set by hand, such as
`1` to export responses on purpose, as `info` rather than as unset. Under Claude Code's detailed beta tracing
(`ENABLE_BETA_TRACING_DETAILED`, which the profile does not set), the same switch would
also export system prompt and model output text on spans.
