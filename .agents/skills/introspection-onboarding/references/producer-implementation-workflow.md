# Supported producer configuration workflow

## Objective

Connect supported native lifecycle APIs to the installed managed runtime without changing producer binaries or inventing attribution.

## Required actions

1. Set `managed_dir` to `$HOME/.local/lib/agent-introspection/session-context-runtime-v1`.
2. Configure Codex app-server's supported lifecycle callback with `$managed_dir/adapters/codex-app-server.sh` followed by normalized native `SESSION_ID EVENT_TYPE OCCURRED_AT ABSOLUTE_WORKSPACE` arguments.
3. Require Codex app-server to pass only `session_start` or `session_end` event types and expose that same session ID in its OTLP spans.
4. Do not configure Claude Code because it lacks an authoritative event timestamp and its installed trace/log telemetry does not document the matching correlation key.
5. Do not configure direct Codex CLI or OMP. Record each as unresolved until a native lifecycle API and matching source telemetry are available.
6. Do not execute the skill source scripts after installation, parse producer JSON in shell, patch producer binaries, or add fallback attribution.

## Done when

- Codex app-server invokes only the managed adapter with authoritative native lifecycle values and matching source telemetry correlation.
