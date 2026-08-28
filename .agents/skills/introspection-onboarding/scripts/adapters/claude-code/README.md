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

The adapter can capture canonical context, but Claude Code is unresolved for activity attribution until the source contract accepts `claude-code` and a fresh proof establishes that the hook session ID equals the SigNoz source correlation. Do not add a secondary Claude correlation path.
