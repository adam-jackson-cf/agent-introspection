# Codex CLI adapter

`adapter.py` is the persistent Codex CLI `notify` adapter. It receives the documented `agent-turn-complete` JSON argument; unlike lifecycle-hook adapters, it records non-temporal `session_context` evidence.

## Native input

The adapter accepts only one unambiguous JSON argument whose `type` is `agent-turn-complete`. It requires the native `thread-id` and absolute `cwd`; a valid RFC3339 `timestamp` is used when present, otherwise it captures synchronous UTC notify-invocation time. Duplicate authoritative keys, alternate thread-ID spellings, malformed values, and non-directory workspaces fail closed.

## Normalization

It invokes [`../../session-context-runtime.sh`](../../session-context-runtime.sh) as:

```text
codex-cli SESSION_ID session_context OCCURRED_AT WORKSPACE
```

`session_context` is supported only for Codex CLI. `facts sync` stores each record in `introspection.session_projects`, and the dashboard takes the session's latest recorded project; no lifecycle interval is created. Codex app-server and exec share session IDs with this hook, so their sessions are attributed through it too.

## Attribution boundary

The native `thread-id` must equal the SigNoz Codex CLI source correlation. Do not turn `agent-turn-complete` into an inferred start, end, or workspace-change event.

## Prompt export

Codex needs no activity hook: tool, approval, interrupt, steer, retry, and delegation
signals are native. Task-type and correction labels come from Codex's own
`codex.user_prompt` events once `log_user_prompt = true` is set under `[otel]` in each
`<codex-root>/config.toml` (`$CODEX_HOME` when set to a non-empty absolute path, else
`~/.codex`); `facts sync` labels them with Jev. See `docs/hook-events.md`, Prompt export.
