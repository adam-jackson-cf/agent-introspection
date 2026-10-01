# Harness configuration workflow

Configure the harnesses the user asks for, and only those, so every dashboard signal
each can produce reaches the facts: telemetry export, project attribution, activity
hooks, and prompt export. Record contracts: `scripts/README.md` (session-context records) and
`docs/hook-events.md` (activity-hook records and prompt export).

## Choose the harnesses

1. Ask which harnesses to include; never assume any. Offer what is installed
   (`command -v claude codex omp`, plus any other agent CLI the user names).
2. A **known** harness (below) is configured with the steps that follow. Known
   harnesses the user does not include stay unconfigured; the Pipeline view shows them
   `idle`, which is expected.
3. An **unknown** harness goes through [Unknown harness](#unknown-harness) first; it
   is not configured here until it is known.

## What each known harness needs

| Harness                       | OTLP export                                 | Session-context (project)                        | Activity hooks                    | Prompt export                               |
| ----------------------------- | ------------------------------------------- | ------------------------------------------------ | --------------------------------- | ------------------------------------------- |
| Claude Code                   | `env` in `~/.claude/settings.json`          | `SessionStart`, `CwdChanged`, `SessionEnd` hooks | 6 hooks via `install_activity.py` | `OTEL_LOG_USER_PROMPTS=1` in the same `env` |
| Codex (app-server, CLI, exec) | `[otel]` in each `<codex-root>/config.toml` | `notify` (covers every Codex surface)            | none: its signals are native      | `log_user_prompt = true` in `[otel]`        |
| omp                           | `~/.omp/.env`                               | `adapter.ts` extension                           | `activity.ts` extension           | sent by `activity.ts`                       |

`<codex-root>` is `$CODEX_HOME` when set to an absolute path, else `~/.codex`; configure
every root in use (for example an Orca per-account root and `~/.codex`).

## Steps

1. **Check the surface.** Record `claude --version`, `codex --version`, `omp --version`.
   The hooks above were verified on Claude Code 2.1.285, Codex 0.156.1, and omp
   18.4.4; after an upgrade, confirm the event names and payload fields in the installed
   types or docs, and look for renamed telemetry attributes (omp renamed `pi.gen_ai.*`
   to `omp.gen_ai.*` on 2026-09-29).
2. **Managed files.** Copy the runtime, shim, and adapters from this skill's `scripts/`
   so hooks never run a repo path:

   ```sh
   S=.agents/skills/introspection-onboarding/scripts
   R=~/.local/lib/agent-introspection/session-context-runtime-v1
   mkdir -p "$R/adapters/omp"
   install -m 0755 "$S/session-context-runtime.sh" "$S/activity-shim.sh" "$R/"
   install -m 0755 "$S/adapters/claude-code/adapter.py" "$R/adapters/claude-code.py"
   install -m 0755 "$S/adapters/codex-cli/adapter.py" "$R/adapters/codex.py"
   install -m 0644 "$S/adapters/omp/adapter.ts" "$S/adapters/omp/activity.ts" "$R/adapters/omp/"
   ```

3. **OTLP export** to the local collector (gRPC usually `localhost:4317`, HTTP
   `localhost:4318`):
   - Claude Code `env`: `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER`,
     `OTEL_TRACES_EXPORTER` = `otlp`; `OTEL_EXPORTER_OTLP_ENDPOINT`;
     `OTEL_EXPORTER_OTLP_PROTOCOL` (`grpc` or `http/protobuf` to match the port);
     `OTEL_RESOURCE_ATTRIBUTES` with `service.name=claude-code`.
   - Codex `[otel]`: `exporter`, `trace_exporter`, `metrics_exporter` pointing at the
     collector.
   - omp `~/.omp/.env`: `OTEL_EXPORTER_OTLP_ENDPOINT` (HTTP),
     `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`, `OTEL_SERVICE_NAME=oh-my-pi`.
4. **Session-context hook.**
   - Claude Code: command hooks `SessionStart`, `CwdChanged`, `SessionEnd` running
     `$R/adapters/claude-code.py`.
   - Codex: `notify = ["$R/adapters/codex.py"]`. If another tool already owns `notify`
     (Codex Computer Use wraps it as `--previous-notify`), add ours to that chain;
     never replace it.
   - omp: `ln -sfn "$R/adapters/omp/adapter.ts" ~/.omp/agent/extensions/agent-introspection.ts`.
5. **Activity hooks.**
   - Claude Code: `python3 "$S/adapters/claude-code/install_activity.py" --dry-run`,
     then without `--dry-run` (backs up, idempotent, `--remove` undoes).
   - omp: `ln -sfn "$R/adapters/omp/activity.ts" ~/.omp/agent/extensions/agent-introspection-activity.ts`
     and list `~/.omp/agent/extensions/agent-introspection-activity.ts` under
     `extensions:` in `~/.omp/agent/config.yml` (omp's ambient discovery does not load
     it). Restart omp sessions.
6. **Prompt export** (after the user's confirmation): Claude Code
   `OTEL_LOG_USER_PROMPTS=1`; Codex `log_user_prompt = true` in every root; omp needs
   nothing more (its content-capture option sends whole conversations, so
   `activity.ts` sends just the prompt). Labelling needs an OpenRouter key where
   `facts sync` runs: `OPENROUTER_API_KEY`, or omp's stored credential
   (`omp token openrouter`). New sessions pick up the change; running ones keep sending
   `REDACTED`.

## Done when

- Each configured file has a backup and parses, every hook points at a managed path,
  and the validation workflow passes for the harness.

## Unknown harness

A harness becomes known only when every dashboard signal has a route for it or a
recorded reason it cannot. Until then it is not configured and not added to the
registry, so it cannot show partial or misleading data. Find, and record in a harness
profile (version, evidence, and whether each item is native, needs a hook, or is
missing):

| Need                  | What to find                                                                                       | Check                                                 |
| --------------------- | -------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| Telemetry export      | Its OTLP settings; the `service.name` it reports                                                   | Rows for that service in SigNoz after a short session |
| Session identity      | The attribute naming the session, stable across its turns                                          | Same value on usage, task, and tool rows              |
| Task boundary         | The span or event that is one user turn or run, with start and end                                 | One per prompt                                        |
| Model calls and usage | Per-call tokens (input, cached, output, reasoning), model, effort, errors, latency                 | Sums match the harness's own accounting               |
| Tool calls            | Tool name, outcome, failure text, arguments or a way to hash them, call ID                         | One per call, joinable to the task                    |
| User signals          | Interrupt, steer, approvals                                                                        | Rare-event routes                                     |
| Prompt text           | A prompt event with its text, or a hook that can send one                                          | Not a placeholder                                     |
| Project attribution   | A hook or callback giving the same session ID plus the absolute working directory at session start | Session ID equals the telemetry session key           |
| Hook surface          | Documented hook or extension events and their payload fields                                       | For each gap above that telemetry lacks               |

Inspect only installed docs, configuration, types, and telemetry key names and counts;
never print prompts, commands, or secrets. Then add it with the
`introspection-operations` change workflow ("Add a harness") and return here to
configure it.
