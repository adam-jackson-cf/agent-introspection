# Claude Code adapter

`adapter.py` is a command-hook adapter for Claude Code. It is the direct `SessionStart`, `CwdChanged`, and `SessionEnd` boundary; unlike the Codex app-server integration, it has no installer or process proxy.

## Native input

Claude supplies one JSON object on standard input. The adapter accepts only an unambiguous object with `hook_event_name`, `session_id`, and absolute `cwd`; it uses an RFC3339 `timestamp` when present and otherwise captures synchronous UTC hook-invocation time. It rejects duplicate keys, malformed JSON, control characters, unsupported hook names, and relative workspaces.

## Normalization

| Claude event | Central event |
| --- | --- |
| `SessionStart` | `session_start` |
| `CwdChanged` | `workspace_changed` |
| `SessionEnd` | `session_end` |

The adapter invokes [`../../session-context-runtime.sh`](../../session-context-runtime.sh) with the shared five-field contract.

## Attribution boundary

Claude Code is supported: the hook `session_id` equals the OTEL `session.id`, and the dashboard joins facts to `introspection.session_project` by it (72% of 90-day Claude Code tasks attributed on 2026-09-29; the rest ran outside a Git workspace or before the hook). Do not add a secondary Claude correlation path.

## Activity hooks

`install-activity.py` registers the activity shim for six Claude Code events; each
runs `/bin/sh ~/.local/lib/agent-introspection/session-context-runtime-v1/activity-shim.sh claude-code <Event>` with a 10-second timeout.

| Claude event | Record | Reads |
| --- | --- | --- |
| `PreToolUse` (matcher `*`) | `tool_call` | `tool_name`, `tool_input`, `tool_use_id`, `cwd` |
| `PostToolUseFailure` (matcher `*`) | `tool_failure` | `tool_name`, `tool_use_id`, `error`, `is_interrupt` |
| `UserPromptSubmit` | `prompt_submitted` | `prompt_id`, prompt length (for interrupt and steer inference) |
| `Stop` | `turn_stop` | `prompt_id`; numeric usage and model of the turn's assistant messages in `transcript_path` |
| `StopFailure` | `turn_failure` | `error` (kept only when it is an identifier) |
| `SubagentStart` | `subagent_start` | `agent_id`, `agent_type`, `prompt_id` |

The shim prints nothing, so `PreToolUse` never makes a permission decision. No
prompt, argument, output, or error text is stored; see
[`docs/hook-events.md`](../../../../../../docs/hook-events.md).

```sh
python3 install-activity.py --dry-run   # print the settings diff, change nothing
python3 install-activity.py             # install (backup: settings.json.bak-<UTC timestamp>)
python3 install-activity.py --remove    # remove only the activity entries
```

`--settings PATH` and `--runtime-dir DIR` select another settings file or shim
location. Entries are recognised by their `activity-shim.sh … claude-code` command,
so reinstalling replaces them and other hooks (for example Orca's) are untouched.
