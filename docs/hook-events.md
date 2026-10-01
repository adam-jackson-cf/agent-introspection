# Hook Events

Native producer telemetry leaves some dashboard signals unreachable for a harness.
Where a harness hook exposes the missing field, an **activity hook** derives a
normalized value from it and writes one JSON record to the inbox that `facts sync`
already drains. This closes the gap without storing raw prompt, command, argument,
or output text (decision D4). A signal that no hook or native field can supply for
every harness is excluded from the dashboard (see
[Dashboard Data Gaps](dashboard-data-gaps.md)).

Prompt labels do not use hooks: see [Prompt export](#prompt-export).

## Route

```text
harness hook (Claude Code settings, omp extension)
  └─> activity shim: reads the hook envelope on stdin, returns at once,
      hands the envelope to a detached `agent-introspection hook <producer> <event>`
        └─> normalizer (src/agent_introspection/hooks.py): derives fields, drops text
              └─> ~/.local/share/agent-introspection/hook-inbox/<event_id>.json
                    └─> facts sync (every minute) ─> introspection.hook_events
                          └─> fact views (tool_calls, task_outcomes, task_labels, …)
```

- Hooks never block the harness: the shim reads stdin, starts the normalizer in the
  background, and exits 0 with no output. A failing normalizer loses one record; it
  never fails a tool call or a prompt.
- Prompt text reaches the normalizer only from Claude Code's `UserPromptSubmit`, and
  only its length is kept.
- Tool arguments and outputs are read in memory and reduced to the fields below.

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

| `event_type`       | Producers        | Hook                                  | `attrs`                                                                                                                                                                                                                                       |
| ------------------ | ---------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `tool_call`        | claude-code, omp | Claude `PreToolUse`; omp `tool_call`  | `tool_use_id`, `tool_name`, `arguments_hash`, `arguments_length`, `command_head`, `command_sub`, `targets` (list, home as `~`, at most 20), `gate_bypass` (0/1), `workdir` (the `workdir`/`cwd` argument, else the hook's `cwd`; home as `~`) |
| `tool_failure`     | claude-code      | `PostToolUseFailure`                  | `tool_use_id`, `tool_name`, `failure_signature` (most specific diagnostic line, digits as `N`, ≤ 160 chars), `interrupted` (0/1)                                                                                                              |
| `prompt_submitted` | claude-code      | `UserPromptSubmit`                    | `prompt_id`, `prompt_length`, `turn_open` (0/1: a turn was still running, see [Turn state](#turn-state)); used to infer interrupts and steers                                                                                                 |
| `turn_stop`        | claude-code      | `Stop`                                | `prompt_id`, `reasoning_tokens` (sum of `thinking_tokens` over the turn's assistant messages in the transcript), `response_model`                                                                                                             |
| `turn_failure`     | claude-code      | `StopFailure`                         | `prompt_id`, `error_class`                                                                                                                                                                                                                    |
| `subagent_start`   | claude-code      | `SubagentStart`                       | `agent_id`, `agent_type`, `prompt_id`                                                                                                                                                                                                         |
| `approval`         | omp              | `tool_approval_resolved`              | `tool_use_id`, `tool_name`, `approved` (0/1)                                                                                                                                                                                                  |
| `retry`            | omp              | `auto_retry_start` / `auto_retry_end` | `attempt`, `phase` (`start`/`end`), `success` (0/1 on end)                                                                                                                                                                                    |
| `steer`            | omp              | `input` while the agent is not idle   | `prompt_length`                                                                                                                                                                                                                               |

Codex emits tool identity, approvals, interrupts, steers, retries, and delegations
(`codex.agent_communication` spawn) natively, so it has no activity hook.

## Normalization

`hooks.py` mirrors the log projection's Codex derivations so the fields mean the same
thing for every harness:

- `command_head` / `command_sub`: first command token after environment assignments,
  basename only; the second token when it looks like a subcommand.
- `targets`: file paths from edit tools, `path`/`file_path` arguments, and path-like
  command arguments (a slash or a known file extension).
- `gate_bypass`: `--no-verify`, `HUSKY=0`, `SKIP=`, or `--no-gpg-sign` anywhere in
  the arguments.
- `arguments_hash`: SHA-256 of the canonical JSON arguments, first 16 hex digits. It
  is compared only within one harness, so it need not equal Codex's `cityHash64`.
- `failure_signature`: the stack trace's final exception line, else the first
  diagnostic line that is not a generic header or frame, else the first diagnostic
  line; home as `~`, digit and hex runs as `N`, at most 160 characters.

Parity notes: ClickHouse measures `arguments_length` and the 160-character cap in
bytes; `hooks.py` measures `arguments_length` in UTF-8 bytes and caps the signature
in characters. `command_sub` is kept only for tools whose second token is a
subcommand (`hooks.SUBCOMMAND_HEADS`, mirrored in the SQL), so `echo word` keeps none.

## Turn state

Claude Code has no "turn running" flag on its prompt hook, so
`hooks.py` keeps one marker file per session in
`~/.local/share/agent-introspection/hook-state/<sha256(producer, session_id)>.turn`,
holding only the prompt ID and the time it opened. A prompt reads the marker
(`turn_open`), then writes it; `Stop` and `StopFailure` remove it. A marker older
than six hours counts as closed.

## Install

The shim is `.agents/skills/introspection-onboarding/scripts/activity-shim.sh`;
the installers copy it to
`~/.local/lib/agent-introspection/activity-hooks-v1/activity-shim.sh`.
See the [adapter READMEs](../.agents/skills/introspection-onboarding/scripts/README.md#activity-hooks)
for the commands. Failures are logged by exception type only to
`~/.local/share/agent-introspection/hooks.log`.

## Prompt export

Task-type, correction, and sentiment labels come from the prompt text the producers
export to SigNoz, labelled in the app rather than in a hook:

| Harness              | Export setting                                                                                                                                                          | Event               |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| Claude Code          | `OTEL_LOG_USER_PROMPTS=1` in the `env` of `~/.claude/settings.json`                                                                                                     | `user_prompt`       |
| Codex (all surfaces) | `log_user_prompt = true` under `[otel]` in each `<codex-root>/config.toml`                                                                                              | `codex.user_prompt` |
| omp                  | The activity extension sends it on `before_agent_start` to omp's own OTLP endpoint (`OTEL_EXPORTER_OTLP_*`, loaded from `~/.omp/.env`), because omp has no prompt event | `omp.user_prompt`   |

`facts sync` (`classify.run`) reads unlabelled prompts from SigNoz, sends each
redacted and clipped to Jev, and stores only the answers in
`introspection.prompt_labels`; a failed decision is retried up to three times, and
`facts classify --days N` labels on demand. The facts loader drops the `prompt`
attribute, so prompt text is stored only in SigNoz's own tables, for their 90-day
retention, where the SigNoz UI and anything else reading that instance can see it.
Prompts exported before the settings were switched on are a `REDACTED` placeholder
and cannot be labelled.
