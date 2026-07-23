# Canonical session-context contract workflow

## Objective

Create deterministic session-context records from normalized native lifecycle values and a Git workspace identity.

## Required actions

1. Accept only producer, session ID, event type, RFC 3339 occurred-at timestamp, and absolute workspace inputs.
2. Require producer value `codex-app-server` and event types `session_start` or `session_end`.
3. Resolve the Git common directory from the absolute workspace, require that its basename is `.git`, and derive the normalized root as that directory's parent; reject every other workspace shape.
4. Derive `agent.project.id` as the 64-hex SHA-256 digest from macOS system utility `shasum -a 256` over `git\0<normalized root>`; set `agent.project.name` to the root basename, `agent.project.root` to the normalized absolute root, and `agent.project.kind` to `git`.
5. Derive the 64-hex `event_id` with `shasum -a 256` over producer, session ID, event type, occurred-at timestamp, and normalized root separated by NUL bytes; `shasum` is a macOS system utility, not a package dependency.
6. Write one JSON record containing `event_id`, `producer`, `session_id`, `event_type`, `occurred_at`, and `agent.project`.
7. Do not parse producer JSON in the runtime, add static attributes, make network calls, or infer identity from telemetry CWD, prompts, paths, or aliases.

## Done when

- The record contains the complete canonical event and project tuple derived only from validated inputs.
