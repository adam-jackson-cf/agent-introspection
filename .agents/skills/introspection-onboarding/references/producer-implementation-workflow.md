# Producer configuration workflow

## Objective

Use only installed, documented native local-command hooks to request idempotent bounded artifact backfill. Producers without that configuration surface remain on scheduled harvesting.

## Guidance

- Use exactly agent-introspection session-context backfill; do not pass lifecycle payloads, prompts, CWD, static project values, or telemetry attributes.
- Restrict the command to producer-owned session metadata and tool-target records under configured roots.
- Configure Claude Code SessionStart with a command hook when that documented surface is installed.
- If agent-introspection is not on PATH, use the installed managed executable without adding producer values or backfill options.
- Do not add a direct Codex CLI hook or treat request-level app-server APIs as persistent local configuration.
- Do not configure OMP when its native extensions expose no safe local-command lifecycle callback; leave it on scheduled capture.
- Do not patch producer binaries, parse producer payloads in shell wrappers, or add fallback identity inference.
- Complete this workflow when each enabled hook invokes only bounded artifact backfill and every unsupported producer is assigned to scheduled harvesting.
