# Producer discovery workflow

## Objective

Classify each requested producer by whether its installed configuration provides a documented native session-context or lifecycle envelope that can supply the canonical required fields to a managed thin adapter.

## Guidance

- Record every producer explicitly requested by the user.
- Inspect only installed configuration, native extension APIs, and documented hook surfaces without exposing secrets.
- Require an authoritative native producer boundary, non-empty single-line session ID, existing absolute workspace, and lifecycle event type. Use a valid native RFC3339 timestamp when supplied; otherwise the adapter may capture the synchronous UTC hook invocation time.
- Classify Claude Code as unresolved for activity attribution until the source contract accepts `claude-code` and a fresh proof establishes equality between its native hook session ID and the SigNoz correlation. Its current documented hook surface alone is insufficient.
- Classify Codex CLI as supported only when its persistent `notify` configuration supplies the documented `agent-turn-complete` envelope with authoritative `thread-id` and absolute workspace. That adapter emits `session_context`, not an inferred lifecycle transition.
- Classify Codex app-server and Codex Desktop as supported only when documented, trusted global `SessionStart` and `SessionEnd` command hooks supply the native `session_id` and absolute `cwd`, and a fresh proof establishes that native session ID equals the OTEL `thread.id` source correlation. This documented hook model has the same managed-adapter standing as the Claude Code, OMP, and Codex CLI native hook surfaces. Both Codex surfaces use canonical producer `codex-app-server`; map only `SessionStart` and `SessionEnd` through the shared session-context runtime. Do not read transcript, prompt, response, or arbitrary payload fields. Until these conditions hold, each surface remains unresolved; do not borrow Codex CLI `notify` identity.
- Classify OMP as supported only when its native extension lifecycle callback supplies authoritative session ID and absolute workspace, and a fresh proof establishes `getSessionId()` equals `gen_ai.conversation.id`.
- Record which canonical event types each installed native surface can expose. Do not claim a lifecycle transition the surface does not expose.
- A surface that omits, ambiguously supplies, or cannot validate any required authoritative field is unsupported and remains unresolved.
- Do not patch producer binaries or infer identity from CWD, prompts, paths, aliases, process state, telemetry, request content, or thread identity.
- Complete this workflow when every requested producer is classified as supported or unresolved, and every supported mapping is limited to managed normalization of authoritative native metadata through the shared session-context runtime.
