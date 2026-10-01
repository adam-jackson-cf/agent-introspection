# omp activity extension

`activity.ts` is an omp extension. Each handler builds a small JSON envelope
(`session_id` from `getSessionId()`, `cwd`, and the fields below) and spawns
`~/.local/lib/agent-introspection/activity-hooks-v1/activity-shim.sh omp <event>` detached; override the path with
`AGENT_INTROSPECTION_SHIM`. Handlers never block, throw, or return a result.

| omp event                             | Record                                                | Envelope fields                      |
| ------------------------------------- | ----------------------------------------------------- | ------------------------------------ |
| `tool_call`                           | `tool_call`                                           | `toolCallId`, `toolName`, `input`    |
| `before_agent_start`                  | none; sends an `omp.user_prompt` OTLP log (see below) | `prompt`                             |
| `tool_approval_resolved`              | `approval`                                            | `toolCallId`, `toolName`, `approved` |
| `auto_retry_start` / `auto_retry_end` | `retry`                                               | `attempt`, `success`                 |
| `input` while `ctx.isIdle()` is false | `steer`                                               | text length only is kept             |

omp exports no prompt event, so on `before_agent_start` the extension posts one
`omp.user_prompt` log (attributes `event.name`, `session.id`, `prompt`,
`prompt_length`; resource `service.name` from `OTEL_SERVICE_NAME`, default
`oh-my-pi`) as OTLP/JSON to omp's own collector: `OTEL_EXPORTER_OTLP_LOGS_ENDPOINT`,
else `OTEL_EXPORTER_OTLP_ENDPOINT` + `/v1/logs`, with `OTEL_EXPORTER_OTLP_*HEADERS`.
omp loads these from `~/.omp/.env`. With no endpoint it sends nothing. `facts sync`
labels the prompt with Jev; the facts store never keeps its text.

omp does not load this extension from ambient discovery: list it under `extensions:`
in `~/.omp/agent/config.yml`, then restart omp sessions.

Install the shim and the extension, link it, and register it:

```sh
R=~/.local/lib/agent-introspection/activity-hooks-v1
mkdir -p "$R/omp"
install -m 0755 ../../activity-shim.sh "$R/activity-shim.sh"
install -m 0644 activity.ts "$R/omp/activity.ts"
ln -sfn "$R/omp/activity.ts" ~/.omp/agent/extensions/agent-introspection-activity.ts
# then add ~/.omp/agent/extensions/agent-introspection-activity.ts under `extensions:`
# in ~/.omp/agent/config.yml and restart omp sessions
```

Remove the link and the `config.yml` entry to uninstall.
