# Session-context configuration validation workflow

## Objective

Validate hook configuration syntax without triggering producers or capture commands.

## Guidance

- Parse changed producer configuration files with their native format parser and never print secret values.
- Confirm every enabled hook invokes exactly agent-introspection session-context backfill with no prompt, CWD, static project, or telemetry-derived arguments.
- Confirm the configured lifecycle event is documented by the producer.
- Record Codex app-server, direct Codex CLI, or OMP as scheduled capture when the required installed native hook surface is unavailable.
- Do not start a producer, run a hook, backfill, scan, telemetry query, or scheduler operation during configuration authoring.
- Complete this workflow when syntax validation succeeds and the configured-hook versus scheduled-capture boundary is recorded without triggering capture.
