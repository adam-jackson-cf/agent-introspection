# Producer discovery workflow

## Objective

Classify requested producers by authoritative lifecycle context and matching explicit producer/session correlation in source telemetry.

## Required actions

1. Record every producer explicitly requested by the user.
2. Require every end-to-end attribution candidate to provide authoritative native lifecycle session ID and workspace values and to emit that same explicit producer/session correlation key in source telemetry.
3. Classify Claude Code as unsupported and attribution unresolved: its hook payload does not provide an authoritative event timestamp, and installed Claude trace/log telemetry does not document the same explicit producer/session correlation key.
4. Classify Codex app-server as conditionally supported only when its native lifecycle API provides normalized session ID, event type, authoritative timestamp, and absolute workspace values and its OTLP spans expose that same session ID.
5. Classify direct Codex CLI and OMP as unsupported and unresolved until a native lifecycle API and source telemetry provide both authoritative session ID, workspace, timestamp, and matching correlation.
6. Do not add static attributes or infer values from CWD, prompts, paths, aliases, or process state.

## Done when

- Every requested producer is classified as supported or unresolved with the missing capability recorded.
