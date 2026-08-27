# Producer discovery workflow

## Objective

Classify each requested producer by whether its installed persistent configuration exposes a documented native lifecycle envelope that can supply the canonical required fields to a managed thin hook adapter.

## Guidance

- Record every producer explicitly requested by the user.
- Inspect only installed configuration, native extension APIs, and documented hook surfaces without exposing secrets.
- Require an authoritative native producer boundary, non-empty single-line session ID, existing absolute workspace, and lifecycle event type. Use a valid native RFC3339 timestamp when supplied; otherwise the adapter may capture the synchronous UTC hook invocation time.
- Classify Claude Code as supported only when documented `SessionStart` and/or `SessionEnd` command hooks provide authoritative session ID and absolute workspace.
- Classify Codex CLI as supported only when its persistent `notify` configuration supplies a supported lifecycle notification with authoritative producer boundary, session ID, and absolute workspace.
- Classify Codex app-server as supported only when its installed protocol provides a persistent callback registration that invokes a managed adapter with protocol `thread.id`, absolute workspace, and a native lifecycle event. The current generated protocol has only client notification `initialized`, so it is unresolved; do not borrow Codex CLI `notify` or Codex app identity.
- Classify OMP as supported only when its native extension lifecycle callback supplies authoritative session ID and absolute workspace.
- Record which canonical event types each installed native surface can expose. Do not claim a lifecycle transition the surface does not expose.
- A surface that omits, ambiguously supplies, or cannot validate any required authoritative field is unsupported and remains unresolved.
- Do not patch producer binaries or infer identity from CWD, prompts, paths, aliases, process state, telemetry, request content, or thread identity.
- Complete this workflow when every requested producer is classified as supported or unresolved, and every supported mapping is limited to native lifecycle normalization and managed runtime invocation.
