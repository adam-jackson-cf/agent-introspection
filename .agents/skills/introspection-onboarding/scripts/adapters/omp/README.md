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
omp loads these from `~/.omp/.env`. With no endpoint it sends nothing. It also sends
nothing until the user opts in to prompt export by setting
`AGENT_INTROSPECTION_OMP_PROMPT_EXPORT=1` in `~/.omp/.env` (the onboarding workflow
does this only in its user-confirmed prompt-export step). `facts sync`
labels the prompt with Jev; the facts store never keeps its text.

omp loads it as a linked plugin: `package.json` declares `activity.ts` under
`omp.extensions`, and `omp plugin link` records it in omp's plugin directory
(`~/.omp/plugins`). Nothing goes in `~/.omp/agent/extensions/` or `config.yml`, so a
`~/.omp` shared across machines never names an extension that a machine lacks.

Install the shim and the plugin package, then link it. Diff and back up any existing
destination before replacing it. The subshell stops at the first failed backup or
install without closing your shell:

```sh
R=~/.local/lib/agent-introspection/activity-hooks-v1
T=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p "$R/omp"
(
  for pair in "../../activity-shim.sh:$R/activity-shim.sh:0755" "activity.ts:$R/omp/activity.ts:0644" "package.json:$R/omp/package.json:0644"; do
    src=${pair%%:*}; rest=${pair#*:}; dst=${rest%%:*}; mode=${rest##*:}
    if [ -e "$dst" ]; then diff -u "$dst" "$src"; cp -p "$dst" "$dst.bak-$T" || exit 1; fi
    install -m "$mode" "$src" "$dst" || exit 1
  done
  omp plugin link "$R/omp"
)
omp plugin list   # shows agent-introspection-activity
```

Restart omp sessions. Updating the installed files needs no relink. Earlier installs
linked `~/.omp/agent/extensions/agent-introspection-activity.ts` and listed it under
`extensions:` in `~/.omp/agent/config.yml`; remove both, since a synced `config.yml`
entry makes omp report a load failure on every machine without the link. Uninstall
with `omp plugin uninstall agent-introspection-activity`.
