# Session-context validation workflow

## Objective

Validate managed capture and scheduler processing without accepting inferred or partial session identity.

## Required actions

1. Start a fresh conditionally supported Codex app-server session in a known Git worktree after its managed adapter is configured.
2. Confirm exactly one new JSON record appears under `$HOME/.local/share/agent-introspection/session-context-inbox/` for the lifecycle event.
3. Validate `event_id`, producer, session ID, event type, occurred-at timestamp, and complete `agent.project` tuple against the canonical session-context contract.
4. Run the supported scheduler operation and confirm it consumes the record and retains the corresponding canonical event in its configured telemetry destination.
5. Reject missing, duplicate, partial, conflicting, non-Git, inferred, or static-attribute records.
6. Keep direct Codex CLI and OMP unresolved; do not validate them through a fallback path.

## Done when

- A supported producer produces a complete deterministic inbox record and scheduler processing preserves its canonical identity.
