# Producer configuration workflow

## Objective

Install the managed shared session-context runtime and configure only documented native lifecycle hooks to invoke a thin producer adapter.

## Guidance

- Install the versioned managed runtime and required adapter under `$HOME/.local/lib/agent-introspection`; a hook must invoke only its managed adapter path.
- Configure Claude Code documented `SessionStart` and `SessionEnd` command hooks when those installed surfaces provide authoritative session ID and absolute workspace.
- Configure Codex persistent `notify` only when its installed notification envelope provides authoritative producer boundary, session ID, and absolute workspace.
- Configure the OMP native extension only when its lifecycle callback provides authoritative session ID and absolute workspace.
- Each adapter must invoke the shared runtime with exactly `PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`.
- `PRODUCER` must be `claude-code`, `codex-cli`, `codex-app-server`, or `omp`; `EVENT_TYPE` must be `session_start`, `workspace_changed`, or `session_end`; `OCCURRED_AT` must be an RFC3339 timestamp with an offset; `WORKSPACE` must be an existing absolute directory.
- An adapter must reject absent, malformed, ambiguous, or unsupported native values before runtime invocation. It may capture synchronous UTC hook invocation time only when the native envelope has no timestamp.
- Do not parse prompts, retain arbitrary producer payloads, scan artifacts, add static project values, infer identity, or patch producer binaries.
- Do not configure a native surface that cannot provide every required authoritative field; leave that producer unresolved.
- Complete this workflow when every configured hook invokes only a managed thin adapter, each adapter has one canonical runtime contract, and unsupported producers remain unresolved.
