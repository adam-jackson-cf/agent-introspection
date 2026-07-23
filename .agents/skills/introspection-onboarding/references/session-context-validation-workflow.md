# Session-context configuration validation workflow

## Objective

Validate hook configuration syntax without triggering producers or capture commands.

## Required actions

1. Parse changed producer configuration files with their native format parser and do not print secret values.
2. Confirm every enabled hook invokes exactly `agent-introspection session-context backfill` with no arguments derived from prompts, CWD, static project values, or telemetry.
3. Confirm the hook lifecycle event is documented by the configured producer.
4. Record Codex app-server and direct Codex CLI as scheduled capture when no installed documented persistent local-command configuration surface exists.
5. Record OMP as scheduled capture when no native extension lifecycle callback safely launches the command.
6. Do not start a producer, run a hook, backfill, a scan, telemetry query, or scheduler operation during configuration authoring.

## Done when

- Syntax validation succeeds and the configured-hook versus scheduled-capture boundary is recorded without triggering capture.
