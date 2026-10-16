# Harness configuration workflow

Configure the harnesses the user asks for, and only those, so every dashboard signal
each can produce reaches the facts: telemetry export, activity hooks, and prompt
export. Each registered harness has a profile,
`src/agent_introspection/harness_profiles/<key>.toml`, that names its config files and
the settings to write, with each setting's purpose; `agent-introspection harness
configure` writes them. Contract: `docs/references/hook-events.md` (activity-hook
records and prompt export).

## Choose the harnesses

1. Ask which harnesses to include; never assume any.
   `agent-introspection harness list` (read-only) shows every profile with its status
   (`registered` or `candidate`), whether it is enabled, whether its binary is on
   `PATH`, the version that binary prints (`installed_version`, and
   `version_verified` against the profile's verified versions; `--no-version` runs no
   binary), and its hook adapter.
2. Record the choice in the config's `[harnesses] enabled` (the registry keys listed in
   `config.example.toml`; `codex` names the three Codex surfaces; absent means every
   registry harness). Parity is judged over this list: a signal is shown only when
   every enabled harness reaches it. Before adding a harness to the list, show the user
   `agent-introspection harness impact <key>`, which lists the signals it would hide.
3. A **registered** harness (Claude Code, Codex, omp) is configured with the steps
   below. Any other producer (opencode, or another agent harness) goes through the
   [producer onboarding workflow](producer-onboarding-workflow.md) first, which checks
   it against
   [Producer capabilities](../../../../docs/references/producer-capabilities.md);
   `harness configure` refuses a candidate.

## What each registered harness needs

This summarizes the profiles; the profile is the source when they differ.

| Harness (profile key)                  | Config file                                                                 | Export settings                                                                                                                                                                                                                                                                                                              | Prompt export                                                     | Activity hooks                      |
| -------------------------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ----------------------------------- |
| Claude Code (`claude-code`)            | `env` in `$CLAUDE_CONFIG_DIR/settings.json`, else `~/.claude/settings.json` | `CLAUDE_CODE_ENABLE_TELEMETRY=1`; `CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1` (the beta traces the task and model-call routes read); `OTEL_METRICS_EXPORTER`, `OTEL_LOGS_EXPORTER`, `OTEL_TRACES_EXPORTER` = `otlp`; `OTEL_EXPORTER_OTLP_ENDPOINT`; `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`; `OTEL_SERVICE_NAME=claude-code` | `OTEL_LOG_USER_PROMPTS=1` with `OTEL_LOG_ASSISTANT_RESPONSES=0`   | six hooks via `install_activity.py` |
| Codex (`codex`: app-server, CLI, exec) | `[otel]` in `config.toml` in every Codex home                               | `exporter`, `trace_exporter`, `metrics_exporter`: an `otlp-http` exporter posting to `<otlp_endpoint>/v1/logs`, `/v1/traces`, and `/v1/metrics` with protocol `binary`                                                                                                                                                       | `log_user_prompt = true`                                          | none: its signals are native        |
| omp (`omp`; service `oh-my-pi`)        | `~/.omp/.env`                                                               | `OTEL_EXPORTER_OTLP_ENDPOINT`; `OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf`; `OTEL_SERVICE_NAME=oh-my-pi`                                                                                                                                                                                                                     | `AGENT_INTROSPECTION_OMP_PROMPT_EXPORT=1` (sent by `activity.ts`) | `activity.ts` plugin                |

`OTEL_SERVICE_NAME` is set explicitly for Claude Code so that a session started from
another harness's shell does not inherit that harness's service name. A Codex home is
`$CODEX_HOME` when set (configured even before Codex creates it), `~/.codex` when it
exists, and every Orca per-account home the profile's `home_globs` matches
(`~/Library/Application Support/orca/codex-accounts/*/home`). Any other home is added
with `--root <home>` (the directory holding its `config.toml`).

## Steps

1. **Check the surface.** `agent-introspection doctor` runs each enabled harness's
   `binary` check against the profile's `verified_versions`. A newer version is `info`:
   confirm the event names and payload fields in the installed types or docs, run
   `agent-introspection doctor --drift` against the saved fingerprints (see the
   producer onboarding workflow's [Watch](producer-onboarding-workflow.md#watch)
   step), and look for renamed telemetry attributes (omp renamed `pi.gen_ai.*` to
   `omp.gen_ai.*` on 2026-09-29).
2. **Export settings.** Run `agent-introspection harness configure <key> --dry-run`,
   show the user the result, then run it again without `--dry-run`:
   - It writes only the profile's `export` settings and prints only those keys, each
     with an action: `add`, `change`, `unchanged`, or `satisfied`. A nested part it
     does not manage (such as exporter headers) shows as `(not shown)`; every other
     key and hook is kept.
   - **Transport equivalence:** an existing exporter that already reaches the
     collector by another route (OTLP/gRPC on port 4317, or OTLP/HTTP on port 4318 or
     the configured port) is `satisfied` and left alone; `--force` rewrites it to the
     profile's OTLP/HTTP value. It must name the collector's machine: any local name
     (`127.0.0.1` and `localhost` alike) on the host, the host's own name on an
     additional machine.
   - Each changed file is first copied to `<file>.bak-<UTC timestamp>`; the write is
     atomic and keeps the file's permissions, and a second run changes nothing. A TOML
     table it cannot edit line by line is rewritten as one table of inline values; its
     comments move above the key each described, and the file's `notes` say so.
   - The endpoint is `[signoz] otlp_endpoint` (default `http://127.0.0.1:4318`), or on an
     additional machine `[machine] export_endpoint`, the host's collector: an OTLP/HTTP
     base URL, which config refuses on the gRPC port 4317, so every harness is written
     the same transport.
   - A set `CLAUDE_CONFIG_DIR` or `CODEX_HOME` names the file to write even before the
     harness has created it there; a file left at the default location is then not
     the one that harness reads. `doctor` reads the same files.
   - `--root PATH …` adds a config directory: another Codex home, beside the existing
     ones; for a file configured once (Claude Code, omp), it replaces the profile's
     path. Each must be an existing directory.
   - It exits 50 for a candidate, an unknown name, a missing root, or a file it cannot
     parse or change safely; fix that file by hand (diff and back up first), then rerun.
   - `next_steps` lists the hook install and the restart that follow.
3. **Managed files.** Copy the shim and the omp extension so hooks never run a repo
   path. Show the diff against each existing destination first, and back up every
   destination that exists before overwriting it. The subshell stops at the first
   failed backup or install without closing your shell:

   ```sh
   S=.agents/skills/introspection-onboarding/scripts
   R=~/.local/lib/agent-introspection/activity-hooks-v1
   T=$(date -u +%Y%m%dT%H%M%SZ)
   mkdir -p "$R/omp"
   (
     for pair in "$S/activity-shim.sh:$R/activity-shim.sh:0755" "$S/adapters/omp/activity.ts:$R/omp/activity.ts:0644" "$S/adapters/omp/package.json:$R/omp/package.json:0644"; do
       src=${pair%%:*}; rest=${pair#*:}; dst=${rest%%:*}; mode=${rest##*:}
       if [ -e "$dst" ]; then diff -u "$dst" "$src"; cp -p "$dst" "$dst.bak-$T" || exit 1; fi
       install -m "$mode" "$src" "$dst" || exit 1
     done
   )
   ```

4. **Activity hooks.**
   - Claude Code: `python3 "$S/adapters/claude-code/install_activity.py" --dry-run`,
     then without `--dry-run` (backs up, idempotent, `--remove` undoes). It edits
     `$CLAUDE_CONFIG_DIR/settings.json` when that variable is set, else
     `~/.claude/settings.json`: the file `harness configure` writes.
   - omp: link the managed package as a plugin with `omp plugin link "$R/omp"` and
     confirm `omp plugin list` shows `agent-introspection-activity`. The link lives in
     omp's plugin directory, so nothing is added to `~/.omp/agent/extensions/` or
     `~/.omp/agent/config.yml`. If an earlier install left
     `~/.omp/agent/extensions/agent-introspection-activity.ts` or its `extensions:`
     entry in `config.yml`, back up `config.yml`, show the diff, and remove both.
5. **Prompt export** (only after the user confirms): run
   `agent-introspection harness configure <key> --prompt-export`, with `--dry-run`
   first. Without the flag, prompt-export settings are left as they are and reported
   with `opt_in` and `on`. For Claude Code it writes `OTEL_LOG_USER_PROMPTS=1` with
   `OTEL_LOG_ASSISTANT_RESPONSES=0`: unset, the second follows the first, and Claude
   Code would also export each assistant response's text; `doctor` warns when the
   first is set without the second. It writes `0` over a value set by hand, such as
   `OTEL_LOG_ASSISTANT_RESPONSES=1`: when the dry run shows such a `change` and the
   user chose that value, do not run the write. For omp, `activity.ts` sends no prompt text until its
   setting is on; omp's own content-capture option sends whole conversations, so
   `activity.ts` sends just the prompt. Labels need an OpenRouter key where `facts sync`
   runs: `[labels] openrouter_key_command` (a command that prints it, which the
   scheduled sync runs too), or `OPENROUTER_API_KEY` for interactive runs only; omp's
   stored credential (`omp token openrouter`) is the last fallback. New sessions pick
   up the change; running ones keep sending `REDACTED`.
6. **Restart** running sessions of each changed harness: they read their settings at
   start.
7. **Check.** `agent-introspection doctor`: for each enabled harness, `config_files`,
   `export_settings`, `activity_hooks`, and `activity_shim` pass, and
   `prompt_export` is `pass` or `off`, as the user chose, or `info` for a value they
   set by hand.

## Done when

- `doctor` passes for each configured harness, each changed file has a backup, every
  hook points at a managed path, and the validation workflow passes for the harness.
