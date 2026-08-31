# OMP adapter

`adapter.ts` is an OMP extension adapter. It uses OMP's extension API directly; unlike shell and Python adapters, it must remain TypeScript because it registers native lifecycle callbacks with OMP.

## Native input

The extension reads only `context.sessionManager.getSessionId()`, `context.cwd`, and the lifecycle callback timestamp. It registers `session_start` and `session_shutdown`; the latter normalizes to `session_end`. A missing or malformed session ID, workspace, or timestamp produces a bounded rejection rather than a partial runtime call.

## Normalization

The extension launches [`../../session-context-runtime.sh`](../../session-context-runtime.sh) with the shared five-field contract. Registration is verified by the private adapter test suite rather than a public slash command.

## Attribution boundary

OMP is supported only when a fresh proof establishes that `getSessionId()` equals the SigNoz `gen_ai.conversation.id` source correlation. The extension must not infer that equality from its CWD, local session artifacts, or telemetry content.
