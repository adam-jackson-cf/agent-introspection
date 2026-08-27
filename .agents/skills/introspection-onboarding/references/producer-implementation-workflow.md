# Producer configuration workflow

## Objective

Install the managed shared session-context runtime and configure only documented native lifecycle hooks to invoke a thin producer adapter.

## Guidance
- Install the versioned managed runtime and required adapter under `$HOME/.local/lib/agent-introspection`; a hook must invoke only its managed adapter path.
- Install a supported producer with:
+```sh
+source_dir=/absolute/path/to/.agents/skills/introspection-onboarding/scripts
+managed_dir="$HOME/.local/lib/agent-introspection/session-context-runtime-v1"
+install -d -m 0755 "$managed_dir/adapters"
+install -m 0755 "$source_dir/session-context-runtime.sh" "$managed_dir/session-context-runtime.sh"
+install -m 0755 "$source_dir/adapters/<approved-adapter>" "$managed_dir/adapters/<approved-adapter>"
+```
- Configure Claude Code documented `SessionStart` and `SessionEnd` command hooks when those installed surfaces provide authoritative session ID and absolute workspace.
- Configure Codex persistent `notify` only when its installed notification envelope provides authoritative producer boundary, session ID, and absolute workspace.
- Configure the OMP native extension only when its lifecycle callback provides authoritative session ID and absolute workspace.
- Configure Codex app-server through the managed transparent JSONL process proxy selected by Codex Desktop through `CODEX_CLI_PATH`. Run `.agents/skills/introspection-onboarding/scripts/install-codex-app-server.py`; it atomically installs the proxy, shared runtime, and `codex-app-server.sh` under `$HOME/.local/lib/agent-introspection/session-context-runtime-v1`, records the immutable bundled Codex executable path, and persists the override through the user LaunchAgent without restarting Codex Desktop.
- The proxy must preserve protocol bytes, backpressure, signals, stderr, argv, environment isolation, and child exit status. It may decode only transport request ID, method, protocol `thread.id`, and absolute protocol `cwd`; it must structurally skip arbitrary prompt, response, history, and title fields.
- Map the first successful `thread/start` to `session_start`; a known `thread/resume` or `thread/settings/updated` with changed `cwd` to `workspace_changed`; and successful `thread/delete` or `thread/deleted` to `session_end`. Persist only approved `(thread.id, cwd)` state with bounded growth. Same-workspace resume is idempotent; unknown resume or delete must fail closed.
- Keep the producer `codex-app-server`; never reuse or extend the Codex CLI notify contract, depend on Codex SQLite, or create a separate Codex Desktop producer.
- Each adapter must invoke the shared runtime with exactly `PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE`.
- `PRODUCER` must be `claude-code`, `codex-cli`, `codex-app-server`, or `omp`; `EVENT_TYPE` must be `session_start`, `workspace_changed`, or `session_end`; `OCCURRED_AT` must be an RFC3339 timestamp with an offset; `WORKSPACE` must be an existing absolute directory.
- An adapter must reject absent, malformed, ambiguous, or unsupported native values before runtime invocation. It may capture synchronous UTC hook invocation time only when the native envelope has no timestamp.
- Do not parse prompts, retain arbitrary producer payloads, scan artifacts, add static project values, infer identity, or patch producer binaries.
- Do not configure a native surface that cannot provide every required authoritative field; leave that producer unresolved.
- Complete this workflow when every configured hook invokes only a managed thin adapter, each adapter has one canonical runtime contract, and unsupported producers remain unresolved.
