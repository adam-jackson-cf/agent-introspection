# Producer configuration workflow

## Objective

Use only installed, documented native local-command hooks to request idempotent bounded artifact backfill. Producers without that configuration surface remain on scheduled harvesting.

## Required actions

1. Use exactly `agent-introspection session-context backfill`; do not pass lifecycle payloads, prompts, CWD, static project values, or telemetry attributes.
2. The command reads only producer-owned session metadata and tool-target records under its configured roots.
3. Configure Claude Code `SessionStart` with a command hook. If `agent-introspection` is not on `PATH`, use the installed virtualenv executable without adding backfill options or producer values.
4. Do not add a direct Codex CLI hook. Codex app-server's documented request-level `hooks/list` surface is not a persistent local configuration surface; leave app-server capture to the scheduled scan unless such a documented configuration surface is installed.
5. Do not configure OMP when its extensions expose no native lifecycle callback that safely launches a local command without static project values. Leave OMP capture to the scheduled scan; do not use the external extension bridge as a substitute.
6. Do not patch producer binaries, invoke the command from shell wrappers that parse producer payloads, or add fallback identity inference.

## Done when

- Every enabled hook invokes only bounded artifact backfill after its documented lifecycle event.
- Every unsupported producer surface is explicitly assigned to scheduled harvesting.
