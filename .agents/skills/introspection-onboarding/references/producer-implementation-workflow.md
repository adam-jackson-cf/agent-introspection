# Producer configuration workflow

## Objective

Install the managed shared session-context runtime and configure only documented native lifecycle hooks to invoke a thin producer adapter.

## Guidance

- Install the versioned managed runtime and required adapter under `$HOME/.local/lib/agent-introspection`; each managed adapter must invoke only its managed runtime path.
- Configure Claude Code documented `SessionStart` and `SessionEnd` command hooks when those installed surfaces provide authoritative session ID and absolute workspace.
- Configure Codex CLI persistent `notify` only when its installed `agent-turn-complete` envelope provides authoritative producer boundary, `thread-id`, and absolute workspace; it emits the Codex-CLI-only `session_context` event type.
- Configure the OMP native extension only when its lifecycle callback provides authoritative session ID and absolute workspace.
- Configure Codex app-server through documented global `SessionStart` and `SessionEnd` command hooks at `<codex-root>/hooks.json`, where `<codex-root>` is `$CODEX_HOME` when it is set to a non-empty absolute path and otherwise `~/.codex`; trust state is at `<codex-root>/config.toml`. Run `.agents/skills/introspection-onboarding/scripts/adapters/codex-app-server/install.py`; it atomically installs the shared runtime and managed `adapter.py` under `$HOME/.local/lib/agent-introspection/session-context-runtime-v1/adapters/codex-app-server`, then merges installer-owned hooks while preserving unrelated hooks. It requires interactive trust and must not write trust state programmatically.
- The installer-owned `SessionStart` hook uses matcher `^(startup|resume|clear|compact)$`; the installer-owned `SessionEnd` hook uses matcher `^other$`. The managed adapter reads one hook JSON object from standard input, validates only documented `hook_event_name`, `session_id`, and absolute `cwd`, plus `source` for `SessionStart` or `reason` for `SessionEnd`. It must not read transcript paths, prompts, responses, or arbitrary payload fields.
- Map `SessionStart` only to `session_start` and `SessionEnd` only to `session_end`. The hook envelope has no timestamp, so the adapter captures synchronous UTC RFC3339 invocation time and execs the shared runtime as `codex-app-server SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`.
- Keep the producer `codex-app-server`; never reuse or extend the Codex CLI notify contract, depend on Codex SQLite, create a separate Codex Desktop producer, or support mid-thread project changes.
- Each adapter must invoke the shared runtime with exactly `PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`.
- `PRODUCER` must be `claude-code`, `codex-cli`, `codex-app-server`, or `omp`; `EVENT_TYPE` must be `session_start`, `workspace_changed`, or `session_end`, except `session_context`, which is supported only for `codex-cli`; `OCCURRED_AT` must be an RFC3339 timestamp with an offset; `WORKSPACE` must be an existing absolute directory.
- An adapter must reject absent, malformed, ambiguous, or unsupported native values before runtime invocation. It may capture synchronous UTC hook invocation time only when the native envelope has no timestamp.
- Do not parse prompts, retain arbitrary producer payloads, scan artifacts, add static project values, infer identity, or patch producer binaries.
- Do not configure a native surface that cannot provide every required authoritative field; leave that producer unresolved.
- Complete this workflow when every configured hook invokes only a managed thin adapter, each adapter has one canonical runtime contract, and unsupported producers or unsupported mid-thread project changes remain unresolved.
