# Managed runtime installation workflow

## Objective

Install one shared session-context runtime and only the thin producer adapters required by approved native lifecycle configuration at a stable versioned managed location.

## Guidance

- Set `source_dir` to the directory containing this skill's `scripts/` directory.
- Set `managed_dir` to the current versioned path under `$HOME/.local/lib/agent-introspection`.
- Copy the shared session-context runtime and only the required producer adapters into `managed_dir`; ensure copied scripts are executable.
- Configure Claude Code `SessionStart` and `SessionEnd`, Codex `notify`, and the OMP native extension only with their copied managed adapter path.
- Never execute a skill source path, mutable shell alias, prompt fragment, inline command, or static project value from a hook.
- The shared runtime accepts exactly `PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`.
- Require canonical producer `claude-code`, `codex-cli`, `codex-app-server`, or `omp`; non-empty single-line session ID; canonical event type `session_start`, `workspace_changed`, or `session_end`; RFC3339 timestamp with an offset; and existing absolute workspace.
- Adapters may normalize only documented native lifecycle envelopes. They must fail closed when a required authoritative value is absent, malformed, ambiguous, or unsupported, and must not infer project identity, scan artifacts, read prompts, or retain arbitrary payloads.
- An adapter may capture synchronous UTC hook invocation time only when its native envelope has no timestamp.
- Preserve the versioned directory until every configured producer is deliberately migrated to another managed version.
- Validate installed file presence, executable mode, and configuration reference without printing file content that could contain local configuration.
- Complete this workflow when each configured producer invokes a managed thin adapter and every adapter reaches the same shared runtime contract.
