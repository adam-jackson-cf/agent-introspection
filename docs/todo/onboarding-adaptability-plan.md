# Onboarding adaptability plan

Make install and onboarding work the same way for producers this repository knows
(Claude Code, Codex, omp) and producers it does not (opencode, or any other agent
harness). It must be easy to run on a new desktop, and an agent or user must be able
to diagnose every step.

## Why

The registry (`signal_support.toml`) already says what each dashboard signal is for
and how each harness reaches it. Onboarding does not use that knowledge:

- nothing ties signals to the producer data they consume;
- no command identifies or explores producers, or checks a harness's configuration;
- the five service names are copied by hand into about seven places;
- known-harness setup is done by hand-editing files, so the docs drift from what works.

## Decisions

- **Parity stays strict.** A producer is enabled only when every signal it cannot
  reach is acceptable to hide. `harness impact` shows that cost before anyone enables
  the producer.
- **Installing SigNoz is opt-in and guided.** The tool never reconfigures an existing
  SigNoz. It installs a new one only when the user explicitly asks, through the guided
  install script, and only after `signoz detect` finds no SigNoz or ClickHouse.
- **Single machine is the default path.** Additional machines that send their harness
  telemetry and hook records to a host are an explicit add-on.
- **Prompt labels do not depend on omp.** `OPENROUTER_API_KEY` or
  `[labels] openrouter_key_command` is the documented route. `omp token openrouter`
  stays as a last fallback.

## Lifecycle

```text
identify ─▶ explore ─▶ assess ─▶ decide ─▶ configure ─▶ validate ─▶ watch
producers   producers  capability harness   harness      doctor      doctor --drift
discover    profile    projection impact    configure    (--live)    producers fingerprint
```

## Contracts

These are fixed. Implementers may add fields, but must not rename these.

### Capabilities (`signal_support.toml`)

A new top-level `[[capability]]` table. Each entry has:

- `id`: one of the ids below;
- `title`;
- `need`: what to find in a producer;
- `check`: how to verify it;
- `hints`: patterns used by `producers profile`:
  - `span_names`, `event_names`, and `attribute_keys`;
  - each is a list of case-insensitive regex strings.

Every harness-scoped `[[signal]]` gains `requires = [<capability id>, ...]`, a
non-empty subset of the ids. System signals have no `requires`.

| id                 | Need                                                                        |
| ------------------ | --------------------------------------------------------------------------- |
| `export`           | OTLP export to the local collector with a stable `service.name`             |
| `session_identity` | a session key, stable across the session's usage, task, and tool rows       |
| `task_boundary`    | one span or event per user turn or run, with start and end                  |
| `model_usage`      | per-call input, cached-input, and output tokens (cache creation if present) |
| `reasoning_usage`  | reasoning or thinking tokens                                                |
| `model_identity`   | the served model and the requested reasoning effort                         |
| `model_outcome`    | per-call status or error, latency, time to first token, retries             |
| `tool_calls`       | one row per tool call: name, call ID, outcome, joinable to its task         |
| `tool_arguments`   | argument identity (hashable arguments or command) without storing text      |
| `tool_failure`     | a diagnostic line for a failed call                                         |
| `user_friction`    | interrupts, steers, follow-ups                                              |
| `approvals`        | approval or permission decisions, and sandbox outcomes                      |
| `delegation`       | subagent spawns                                                             |
| `prompt_text`      | a prompt event carrying text, for prompt labels                             |
| `session_store`    | a local store mapping session ID to working directory                       |

`src/agent_introspection/capabilities.py` parses and validates these. It provides:

- `load_capabilities()`;
- `requirements(registry_document)`, mapping each signal to its required ids;
- `project(profile, registry)`, returning per signal one of `reachable`,
  `reachable_with_work`, `unreachable`, `unknown`, or `not_applicable`;
- `impact(enabled, candidate, registry)`, returning the signals visible now, the
  signals visible after, and the signals that would become hidden, each with the
  missing capability as the reason.

### Harness profiles (`src/agent_introspection/harness_profiles/<key>.toml`)

This is a package (with `__init__.py`), read through `importlib.resources`. Its
loader and validator is `src/agent_introspection/profiles.py`.

```toml
schema = "agent-introspection.harness-profile/1"
key = "claude-code"              # profile key; `codex` covers the three Codex surfaces
title = "Claude Code"
status = "registered"            # registered | candidate
registry_keys = ["claude-code"]  # [harnesses] keys it covers; empty for a candidate
service_names = ["claude-code"]
binary = "claude"
version_args = ["--version"]
verified_versions = ["2.1.285"]
session_store = "claude-code"    # a [[store]] name in session_stores.toml
hook_adapter = "claude-code"     # hooks.py producer key, or "" when it needs none

[[config_file]]
id = "settings"
paths = ["$CLAUDE_CONFIG_DIR/settings.json", "~/.claude/settings.json"]
format = "json"                  # json | toml | dotenv
table = "env"                    # dotted path of the table holding the settings ("" = top)
roots = "first"                  # first (first path whose variables are set) | each (every set or existing root)

[[setting]]
file = "settings"
key = "CLAUDE_CODE_ENABLE_TELEMETRY"
value = "1"                      # literal, or a template: {otlp_endpoint} {otlp_protocol} {service_name}
group = "export"                 # export | prompt_export
purpose = "Turns on OpenTelemetry export at all."

[capabilities]
model_usage = { status = "native", route = "claude.usage", evidence = "`api_request` logs" }

[capability_overrides.codex_exec]   # per registry key, optional
user_friction = { status = "not_applicable", evidence = "headless: no user" }
```

- Capability status is one of:
  - `native`: in the producer's own telemetry now;
  - `hook`: an installed activity hook supplies it;
  - `hook_available`: a documented hook or plugin surface exists, but no adapter yet;
  - `missing`;
  - `not_applicable`;
  - `unverified`.
- A candidate may carry an inline `[session_store_spec]` table, in the same shape as
  a `[[store]]` entry, in place of `session_store`.
- Profiles to write: `claude-code`, `codex`, and `omp` (registered), and `opencode`
  (candidate, from measured evidence).
- A test asserts that every registry cell a profile reaches (`aligned` or `differs`)
  has every required capability `native` or `hook` for that harness.

### Identity lists from the registry

- `select_spans.sql` and `select_logs.sql` use a `{services}` placeholder, which
  `facts.render_select` fills from the registry's `[harnesses]` keys.
- Preflight's producer-data check reads its services from the registry when it runs.
- `dashboard/src/harnesses.generated.ts` is generated by `scripts/render-measures.py`:
  - it holds `HARNESSES`, `HARNESS_LABEL`, and the services SQL list;
  - `contracts.ts`, `format.ts`, and `pipeline.ts` import from it;
  - the drift test covers it.
- Harness colours get enough categorical slots for the registered harnesses plus
  three candidates, never shared with the part colours (`cat-6`…`cat-8`). A test pins
  the slot count to at least the registry harness count.

### Commands (all print one JSON object; read-only unless stated)

| Command                                                                            | Module                                            | Notes                                                                                                                                                                          |
| ---------------------------------------------------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `producers discover [--days N]`                                                    | `producers.py`, `cli_producers.py`                | each service with span and log counts and first and last seen, classed `registered` (with an `enabled` boolean), `known_producer`, `internal`, or `unregistered`               |
| `producers profile <service> [--days N] [--write PATH] [--force]`                  | same                                              | span, event, resource and attribute **key names** with counts, never values; capability guesses from hints; content-bearing keys flagged; draft profile; projection and impact |
| `producers fingerprint [--days N] [--save] [service ...]`                          | same                                              | `~/.local/share/agent-introspection/fingerprints/<service>.json`; `--save` writes                                                                                              |
| `doctor [--live] [--drift]`                                                        | `doctor.py`, `doctor_harness.py`, `cli_doctor.py` | checks with `pass`, `warn`, `fail`, `off`, or `info`, each with a `fix`; exits non-zero on `fail`                                                                              |
| `harness list [--no-version]`                                                      | `harness_config.py`, `cli_harness.py`             | profiles, status, installed version                                                                                                                                            |
| `harness configure <key> [--dry-run] [--prompt-export] [--force] [--root PATH...]` | same                                              | writes the `export` (and, opt-in, `prompt_export`) settings with a timestamped backup and a diff of managed keys only; idempotent                                              |
| `harness impact <key, service, or profile path>`                                   | same                                              | parity preview over config `[harnesses] enabled`                                                                                                                               |
| `signoz detect`                                                                    | `signoz_detect.py`, `cli_signoz.py`               | SigNoz and ClickHouse containers, local ports 4317, 4318, 8123, 8080; never changes anything                                                                                   |

The four command groups register through `cli_onboarding.py`, which keeps `cli.py`
within the fan-out limit.

Shared function signatures:

- `producers.drift_report(run, saved_dir: Path, services: Sequence[str]) -> dict`
  returns `{"services": [...], "unregistered": [...], "unnamed_rows": int,
"drifted": bool}`;
- `producers.discover(run, registry, enabled, days) -> dict`.

### Other changes

- **Preflight** always prints its JSON report: configuration and runner errors become
  a failing check.
- **Scheduling** depends on the platform:
  - macOS uses launchd with the neutral label `dev.agent-introspection.sync`; install
    boots out and removes the job an earlier release installed under a personal label
    (`schedule.LEGACY_LABEL`), backing up its plist;
  - Linux uses systemd user units for the host's `facts sync`
    (`agent-introspection-sync.service` and `.timer`);
  - any other platform gets a clear error.
- **Session stores** gain `format = "sqlite"` (a read-only connection, a `query`
  returning `session_id`, `cwd`, and `started_at`) beside the JSONL default.
- **Config:**
  - `[labels] openrouter_key_command`;
  - `config.example.toml` documents `[hooks]`, `[sessions.roots]`, and `[labels]`.
- **Registry:** remove the `opencode.not_a_harness` stray, which can never match;
  opencode stays under `[producers]`, which `producers discover` reports.
- **Onboarding skill scripts** gain `signoz/`:
  - a guided install script that refuses when SigNoz exists;
  - a Compose override publishing ClickHouse HTTP, OTLP, and the UI on loopback only;
  - an opt-in collector filter for opencode's plumbing spans.
- **Docs and skills:**
  - single-machine default; multi-machine add-on;
  - every command in `cli-reference.md`;
  - stale references fixed;
  - the onboarding workflows use `doctor`, `harness configure`, and `producers`.

## Safety rules for this work

- Never run `facts install`, `backfill`, `sync`, `schedule install`, or
  `schedule remove`, and never run `uv tool install`.
- Never edit `~/.claude`, `~/.codex`, `~/.omp`, `~/.config/agent-introspection`,
  LaunchAgents, or systemd units on this machine.
- Run `harness configure` against the real home only with `--dry-run`.
- Read-only SigNoz queries may print only names and counts: never attribute values,
  prompts, commands, or secrets. Check secrets for presence only.

### What was built differently

Additions within the contracts are listed in the reference docs. These differ from
the text above:

- `producers discover` has no `enabled` class: a `registered` entry carries an
  `enabled` boolean. Rows with an empty `service.name` are one `(empty)` entry.
- Profiles gained `lacks` (signals a coarser capability cannot serve) and `sources`.
  A setting `value` may be text, a boolean, an integer, or a table, as Codex needs.
  Status follows a weakest-part rule.
- `harness configure` adds `--force` and the action `satisfied`: an exporter that
  reaches the collector over the other OTLP transport is left alone unless forced.
  `doctor` applies the same rule (`profiles.equivalent_transport`). A TOML table that
  cannot be edited line by line is rewritten, its comments moved above their keys.
- The guided SigNoz install uses Foundry (`foundryctl`, pinned with SHA-256), because
  SigNoz no longer ships Compose files. It needs Docker Compose 2.24.4 or later and
  refuses in more cases than stated (stopped containers, a `signoz` project,
  `signoz-*` volumes, a remote Docker daemon).
- The legacy plist backup goes to `~/.local/share/agent-introspection/schedule-backups/`,
  not beside the plist, so launchd cannot load it as a second job.
- A configured `openrouter_key_command` that fails skips labels; it does not fall back
  to omp.
- One profile-path expansion (`profiles.expand_config_path`) serves `doctor`,
  `harness configure`, and `--root`; session-store roots keep their own glob expansion.
  One resolution (`profiles.config_paths`) picks the files both commands use: a set
  config-directory variable (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`) names a file even
  before the harness has created it, and the Claude Code hooks installer follows the
  same rule.
- Collector endpoints are OTLP/HTTP only: config refuses port 4317 in `[signoz]
otlp_endpoint` and `[machine] export_endpoint`, and `{otlp_protocol}` is always
  `http/protobuf`, so every harness is written the same transport. An existing gRPC
  exporter still counts as equivalent.
- Claude Code's `prompt_export` group pairs `OTEL_LOG_USER_PROMPTS=1` with
  `OTEL_LOG_ASSISTANT_RESPONSES=0`, which otherwise follows it and exports assistant
  response text; `doctor` warns on a file holding only part of a group.
- Additional machines are a config role, `[machine] role = "additional"` with
  `export_endpoint` (the host collector's OTLP/HTTP base URL, the one export setting
  that may name another machine). `harness configure` renders `{otlp_endpoint}` from
  it; transport equivalence then accepts only the host's own name. `doctor` leaves the
  host's checks as `info` and adds `host_collector` and `spool_sync`; every command
  that needs ClickHouse, and `facts schedule install`, refuses the role (exit 10).
- The default collector endpoint is `http://127.0.0.1:4318`, not `localhost`, which
  some clients resolve to `::1` first; `localhost` is still accepted as a local name.
- `doctor --drift` also reports `drift_report`'s `unnamed_rows` as `drift_unnamed`.

## Status

| Phase                          | State                                                                                                                                                                                                                |
| ------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1 Foundations                  | done: capabilities and `requires` in the registry, four harness profiles, identity lists generated from `[harnesses]`, always-JSON preflight, launchd or systemd scheduling, SQLite session stores, `[labels]`       |
| 2 Commands                     | done: `signoz detect`, `producers discover/profile/fingerprint`, `harness list/configure/impact`, `doctor`, the guided SigNoz install, the loopback override, and the opencode collector filter                      |
| 3 Docs, skills, opencode pilot | done: CLI reference, producer onboarding and capabilities docs, SigNoz layouts, onboarding and operations skill workflows (single machine first, additional machines as an add-on), opencode measured as a candidate |
| 4 Review and fix               | done: additional-machine role and 127.0.0.1 defaults; PR-time review on Fable (six lenses, each verified by a sceptic): 25 findings, 21 fixed, 4 refuted or low; a Fable recheck found 3 more, fixed                 |
