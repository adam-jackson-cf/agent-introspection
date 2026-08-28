# Session-context configuration validation workflow

## Objective

Validate managed producer configuration, including native hooks and the Codex app-server proxy, without triggering a producer or hook.

## Guidance

- Parse changed producer configuration with its native format parser and never print secret values.
- Confirm every enabled hook references only its managed thin adapter under `$HOME/.local/lib/agent-introspection`, never a skill source path, inline command, shell alias, prompt fragment, or static project value.
- Confirm Claude Code configuration uses only documented `SessionStart` and `SessionEnd` command hooks.
- Confirm Codex CLI configuration uses only the documented persistent `notify` hook and its managed adapter.
- Confirm Codex app-server configuration uses the installer-owned user LaunchAgent `CODEX_CLI_PATH` override and managed JSONL proxy; validate the installed proxy, immutable real-executable record, runtime, and app-server adapter without executing Desktop or the proxy.
- Confirm OMP configuration uses only the native extension lifecycle callback.
- Confirm each configured adapter has exactly five runtime arguments: `PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`.
- Confirm the producer is one of `claude-code`, `codex-cli`, `codex-app-server`, or `omp`; the event type is `session_start`, `workspace_changed`, or `session_end`, except `session_context`, which is supported only for `codex-cli`; and the adapter boundary requires a non-empty single-line session ID, RFC3339 timestamp with an offset, and existing absolute workspace.
- Confirm each configured lifecycle event is documented by the installed native surface. A missing, malformed, ambiguous, or unsupported authoritative field must fail closed before runtime invocation.
- Record surfaces that cannot meet this contract as unresolved. Do not start a producer, run a hook, scan artifacts, query telemetry, or perform runtime delivery during configuration validation.
- Complete this workflow when syntax validation succeeds, every configured hook or proxy reaches a managed thin adapter, and every unresolved surface is explicit.
