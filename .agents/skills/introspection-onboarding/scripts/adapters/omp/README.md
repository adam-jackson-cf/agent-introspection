# OMP adapter

`adapter.ts` is an OMP extension adapter. It uses OMP's extension API directly; unlike shell and Python adapters, it must remain TypeScript because it registers native lifecycle callbacks with OMP.

## Native input

The extension reads only `context.sessionManager.getSessionId()`, `context.cwd`, and the lifecycle callback timestamp. It registers `session_start` and `session_shutdown`; the latter normalizes to `session_end`. A missing or malformed session ID, workspace, or timestamp produces a bounded rejection rather than a partial runtime call.

## Normalization

The extension launches [`../../session-context-runtime.sh`](../../session-context-runtime.sh) with the shared five-field contract. Verify registration end to end with the onboarding skill's end-to-end validation workflow.

## Attribution boundary

OMP is supported only when a fresh proof establishes that `getSessionId()` equals the SigNoz `gen_ai.conversation.id` source correlation. The extension must not infer that equality from its CWD, local session artifacts, or telemetry content.

Runtime exit statuses 0 (recorded), 64 (usage error or recorded workspace
rejection), and 65 (recorded Git rejection) are all accepted; any other status is
an adapter error.

## Activity hooks

`activity.ts` is a separate omp extension. Each handler builds a small JSON envelope
(`session_id` from `getSessionId()`, `cwd`, and the fields below) and spawns
`~/.local/lib/agent-introspection/session-context-runtime-v1/activity-shim.sh omp <event>` detached; override the path with
`AGENT_INTROSPECTION_SHIM`. Handlers never block, throw, or return a result.

| omp event | Record | Envelope fields |
| --- | --- | --- |
| `tool_call` | `tool_call` | `toolCallId`, `toolName`, `input` |
| `before_agent_start` | none; sends an `omp.user_prompt` OTLP log (see below) | `prompt` |
| `tool_approval_resolved` | `approval` | `toolCallId`, `toolName`, `approved` |
| `auto_retry_start` / `auto_retry_end` | `retry` | `attempt`, `success` |
| `input` while `ctx.isIdle()` is false | `steer` | text length only is kept |

omp exports no prompt event, so on `before_agent_start` the extension posts one
`omp.user_prompt` log (attributes `event.name`, `session.id`, `prompt`,
`prompt_length`; resource `service.name` from `OTEL_SERVICE_NAME`, default
`oh-my-pi`) as OTLP/JSON to omp's own collector: `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`,
else `OTEL_EXPORTER_OTLP_ENDPOINT` + `/v1/logs`, with `OTEL_EXPORTER_OTLP_*HEADERS`.
omp loads these from `~/.omp/.env`. With no endpoint it sends nothing. `facts sync`
labels the prompt with Jev; the facts store never keeps its text.

omp does not load this extension from ambient discovery: list it under `extensions:`
in `~/.omp/agent/config.yml`, then restart omp sessions.

Install (the shim from `../../activity-shim.sh` and the extension beside the runtime):

```sh
R=~/.local/lib/agent-introspection/session-context-runtime-v1
install -m 0755 ../../activity-shim.sh "$R/activity-shim.sh"
mkdir -p "$R/adapters/omp"
install -m 0644 adapter.ts "$R/adapters/omp/adapter.ts"
install -m 0644 activity.ts "$R/adapters/omp/activity.ts"
ln -sfn "$R/adapters/omp/adapter.ts" ~/.omp/agent/extensions/agent-introspection.ts
ln -sfn "$R/adapters/omp/activity.ts" ~/.omp/agent/extensions/agent-introspection-activity.ts
```

Remove with `rm ~/.omp/agent/extensions/agent-introspection-activity.ts`.
