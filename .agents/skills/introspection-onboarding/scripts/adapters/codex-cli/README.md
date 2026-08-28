# Codex CLI adapter

`adapter.py` is the persistent Codex CLI `notify` adapter. It receives the documented `agent-turn-complete` JSON argument; unlike lifecycle-hook adapters, it records non-temporal `session_context` evidence.

## Native input

The adapter accepts only one unambiguous JSON argument whose `type` is `agent-turn-complete`. It requires the native `thread-id` and absolute `cwd`; a valid RFC3339 `timestamp` is used when present, otherwise it captures synchronous UTC notify-invocation time. Duplicate authoritative keys, alternate thread-ID spellings, malformed values, and non-directory workspaces fail closed.

## Normalization

It invokes [`../../session-context-runtime.sh`](../../session-context-runtime.sh) as:

```text
codex-cli SESSION_ID session_context OCCURRED_AT WORKSPACE
```

`session_context` is supported only for Codex CLI. The scanner resolves it from exactly one accepted Codex CLI context record for the same producer and correlation ID; it does not create a lifecycle interval.

## Attribution boundary

The native `thread-id` must equal the SigNoz Codex CLI source correlation. Do not turn `agent-turn-complete` into an inferred start, end, or workspace-change event.
