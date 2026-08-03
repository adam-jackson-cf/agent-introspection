# Producer discovery workflow

## Objective

Classify requested producers by whether their installed persistent configuration exposes a documented native local-command lifecycle hook for bounded artifact backfill.

## Guidance

- Record every producer explicitly requested by the user.
- Inspect only installed configuration, extension APIs, launcher surfaces, and documented producer-owned artifacts without exposing secrets.
- Require every enabled hook to invoke bounded backfill without lifecycle payloads, prompts, telemetry CWD, static project values, or inferred identity.
- Classify Claude Code as hook-capable when its documented SessionStart command-hook configuration is available.
- Classify Codex app-server and direct Codex CLI as scheduled capture when no installed persistent local-command configuration surface exists.
- Classify OMP as scheduled capture unless its native extensions safely launch a local command without static project values; an external bridge is not an OMP-native hook.
- Record available producer/session keys, explicit workspace fields, lifecycle timestamps, and tool-target fields for later evidence classification.
- Do not patch producer binaries or add static attributes, CWD, prompt, path, alias, process-state, telemetry, or thread inference.
- Complete this workflow when every requested producer is classified as configured-hook or scheduled-capture and every unavailable native surface is explicit.
