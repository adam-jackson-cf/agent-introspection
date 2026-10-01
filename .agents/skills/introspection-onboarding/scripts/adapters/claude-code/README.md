# Claude Code activity hooks

`install_activity.py` registers the activity shim for six Claude Code events; each
runs `/bin/sh ~/.local/lib/agent-introspection/activity-hooks-v1/activity-shim.sh claude-code <Event>` with a 10-second timeout.

| Claude event                       | Record             | Reads                                                                                      |
| ---------------------------------- | ------------------ | ------------------------------------------------------------------------------------------ |
| `PreToolUse` (matcher `*`)         | `tool_call`        | `tool_name`, `tool_input`, `tool_use_id`, `cwd`                                            |
| `PostToolUseFailure` (matcher `*`) | `tool_failure`     | `tool_name`, `tool_use_id`, `error`, `is_interrupt`                                        |
| `UserPromptSubmit`                 | `prompt_submitted` | `prompt_id`, prompt length (for interrupt and steer inference)                             |
| `Stop`                             | `turn_stop`        | `prompt_id`; numeric usage and model of the turn's assistant messages in `transcript_path` |
| `StopFailure`                      | `turn_failure`     | `error` (kept only when it is an identifier)                                               |
| `SubagentStart`                    | `subagent_start`   | `agent_id`, `agent_type`, `prompt_id`                                                      |

The shim prints nothing, so `PreToolUse` never makes a permission decision. No
prompt, argument, output, or error text is stored; see
[`docs/hook-events.md`](../../../../../../docs/hook-events.md).

```sh
python3 install_activity.py --dry-run   # print the settings diff, change nothing
python3 install_activity.py             # install (backup: settings.json.bak-<UTC timestamp>)
python3 install_activity.py --remove    # remove only the activity entries
```

`--settings PATH` and `--runtime-dir DIR` select another settings file or shim
location. Entries are recognised by their `activity-shim.sh … claude-code` command,
so reinstalling replaces them and other hooks (for example Orca's) are untouched.
