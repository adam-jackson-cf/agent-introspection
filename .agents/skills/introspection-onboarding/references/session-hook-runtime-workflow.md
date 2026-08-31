# Managed runtime installation workflow

## Objective

Install one shared session-context runtime and only the thin producer adapters required by approved producer integrations at a stable versioned managed location.

## Guidance

- Set `source_dir` to the directory containing this skill's `scripts/` directory.
- Set `managed_dir` to the current versioned path under `$HOME/.local/lib/agent-introspection`.
- Copy the shared session-context runtime and only the required producer adapters into `managed_dir`; ensure copied scripts are executable.
- Configure Claude Code `SessionStart` and `SessionEnd`, Codex CLI `notify`, and the OMP native extension only with their copied managed adapter path.
- Configure Codex app-server only through `.agents/skills/introspection-onboarding/scripts/adapters/codex-app-server/install.py`. The installer owns the managed `adapter.py`, shared runtime installation, and global `<codex-root>/hooks.json` merge, where `<codex-root>` is `$CODEX_HOME` when it is set to a non-empty absolute path and otherwise `~/.codex`; trust state is at `<codex-root>/config.toml`. It preserves unrelated hooks and requires interactive trust. Its hooks are `SessionStart` matcher `^(startup|resume|clear|compact)$` and `SessionEnd` matcher `^other$`. Do not manually configure a hook or write trust state.
- Never execute a skill source path, mutable shell alias, prompt fragment, inline command, or static project value from a hook.
- The shared runtime accepts exactly `PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`.
- Require canonical producer `claude-code`, `codex-cli`, `codex-app-server`, or `omp`; non-empty single-line session ID; canonical event type `session_start`, `workspace_changed`, or `session_end`, except `session_context`, which is supported only for `codex-cli`; RFC3339 timestamp with an offset; and existing absolute workspace.
- Adapters may normalize only documented native lifecycle envelopes. The Codex app-server adapter reads one hook JSON object and only its documented `hook_event_name`, `session_id`, and absolute `cwd`, plus `source` for `SessionStart` or `reason` for `SessionEnd`; it must not read transcript paths, prompts, responses, or arbitrary payloads. It maps only `SessionStart` to `session_start` and `SessionEnd` to `session_end`, captures synchronous UTC hook invocation time because the envelope has no timestamp, and execs the five-argument shared runtime contract.
- An adapter may capture synchronous UTC hook invocation time only when its native envelope has no timestamp.
- Preserve the versioned directory until every configured producer is deliberately migrated to another managed version.
- Validate installed file presence, executable mode, and configuration reference without printing file content that could contain local configuration.
- Complete this workflow when each configured producer invokes a managed thin adapter, every adapter reaches the same shared runtime contract, and unsupported mid-thread project changes remain unresolved.
