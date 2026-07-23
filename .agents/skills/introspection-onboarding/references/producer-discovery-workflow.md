# Producer discovery workflow

## Objective

Classify requested producers by whether their installed persistent configuration exposes a documented native local-command lifecycle hook for bounded artifact backfill.

## Required actions

1. Record every producer explicitly requested by the user.
2. Require every enabled hook to invoke bounded backfill without lifecycle payloads, prompts, telemetry CWD, static project values, or inferred identity.
3. Classify Claude Code as hook-capable when its documented `SessionStart` command-hook configuration is available.
4. Classify Codex app-server as scheduled capture when it offers only request-level lifecycle hooks rather than an installed persistent local-command configuration surface. Direct Codex CLI remains scheduled capture when its configuration exposes no hook surface.
5. Classify OMP as scheduled capture unless its native extensions expose a documented lifecycle callback that safely launches a local command without static project values. Do not treat an external bridge as an OMP-native hook.
6. Do not patch producer binaries or add static attributes, CWD, prompt, path, alias, process-state, or telemetry inference.

## Done when

- Every requested producer is classified as configured-hook or scheduled-capture, with the exact unavailable native surface recorded.
