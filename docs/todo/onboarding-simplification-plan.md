# Onboarding simplification plan

Make a fresh install one guided command with Claude Code and Codex as the default
harnesses, and remove project attribution entirely. Every other data signal stays.

## Why

A fresh install is about fifteen hand steps across three installers (the CLI, a
Python hook script, a shell copy loop), two connection modes, an ordering rule for the
hooks endpoint, a developer quality gate, and a foreground dashboard. None of those
steps produce a signal. Project attribution needs no harness change, but it carries a
session-store scan, a third record family, a loader, a config table, and a doctor
check. Its main consumer, the repeated-correction detector, has produced 8 findings, 0
actionable, and 0 proposals in the live workflow store (failure clusters: 143, 28
actionable).

## Decisions

- **D1: Default harnesses are Claude Code and Codex.** An absent `[harnesses] enabled`
  means `claude-code` plus the three Codex surfaces, not every registry harness. omp
  stays a registered, fully supported harness that the user opts into
  (`enabled = [..., "oh-my-pi"]` or `setup --harness omp`). The omp hook install
  (`omp plugin link`) runs only when omp is enabled. Because an absent list used to
  mean every harness, `facts install` and `setup` print the enabled set, and doctor
  gains a `warn` when a disabled registry harness has telemetry rows in the last 7
  days, so omp never vanishes from a reinstalled host without a message.
- **D2: Project attribution is removed, not replaced.** No workdir-derived project
  stand-in. A signal or panel whose only data is the project is removed. A panel where
  the project is one column loses the column and its part in every calculation, and
  the calculation is reconsidered as a whole (below).
- **D3: Evaluation-workspace exclusion moves from `session_project.project_root` to
  `tool_calls.workdir`.** It is a fact on the tool call itself (Codex from tool
  arguments, Claude Code and omp from the hook record, home-redacted), not
  attribution. The existing `failure_signature` path match stays. Accepted loss: a
  Codex call carries `workdir` only when the model passes one, so a Codex evaluation
  run whose calls omit it and whose signature names no temp path now counts.
- **D4: ClickHouse is reached over HTTP only.** Docker mode (`clickhouse_container`,
  `docker_context`, `docker exec` runner) and the `[dashboard]` table are removed. The
  dashboard already needs HTTP; `[signoz] clickhouse_url` serves both. `signoz detect`
  stays (read-only discovery) and names the override when port 8123 is closed.
  Migration cost: stock SigNoz does not publish 8123, so a current docker-mode host
  applies `docker-compose.clickhouse-http.yaml` (restarting ClickHouse) or, on
  OrbStack, uses `<container>.orb.local`. Code sites: `signoz_detect._clickhouse_notes`
  (recommends docker mode today), `doctor._unreachable_fix`, and `schedule.py`'s docker
  CLI requirement. `clickhouse_container`, `docker_context`, and `[dashboard]` join
  `config._RETIRED_KEYS`, so an old config fails with "retired; delete them".
- **D5: Hooks are installed by `harness configure`.** The shim and the omp plugin are
  package data in `agent_introspection`, copied to the managed runtime directory; Claude
  Code's six hooks are a profile `[[hook]]` table written by a new JSON hooks writer
  (the owned-entry merge from `install_activity.py`, keeping third-party hooks, in the
  exact `/bin/sh <shim> claude-code <event>` form doctor checks).
  `harness configure --remove-hooks` replaces `install_activity.py --remove`.
  `install_activity.py` and the manual copy loop are deleted; `tests/test_hooks.py`
  loads the shim and adapters through `importlib.resources`.
- **D6: `[hooks] otlp_logs_endpoint` defaults to `{[signoz] otlp_endpoint}/v1/logs`.**
  The "set it only after `facts install`" rule goes. Records that reach SigNoz before
  the loaders exist are read by `facts backfill` (its record window keys
  `observed_timestamp` and covers `hook_events`) and the hourly 3-day sweep. An
  additional machine defaults it from `[machine] export_endpoint`. The
  `OTEL_EXPORTER_OTLP_*` environment fallback and doctor's "set only by the
  environment" warn are removed, since a default always exists.
- **D7: One OpenRouter key route.** `[labels] openrouter_key_command` only. The
  `OPENROUTER_API_KEY` environment variable and the `omp token openrouter` fallback are
  removed. Labels are skipped, and doctor's `labels` check stays `off` (labels are
  optional) with the key-command fix, when no command is configured.
- **D8: One check command.** `doctor` already runs preflight; `facts preflight` is
  removed as a command (its module stays as doctor's check). Doctor's `signoz` check
  then carries each failing preflight check's `detail`, not only its name, and every
  "run `facts preflight`" fix text goes. The quality gates leave the install path;
  they stay the developer and CI suite.
- **D9: The dashboard is a scheduled service.** `agent-introspection dashboard install`
  builds the dashboard (`bun install --frozen-lockfile`, `bun run build`) and installs
  a launchd agent or systemd user service that runs `bun server.ts` from the repository
  checkout recorded at install time; `dashboard status` and `dashboard remove` mirror
  `facts schedule`. The plist and unit record the resolved `bun` path and PATH, as
  `schedule.plist` does. Doctor's existing `dashboard` check (which pings ClickHouse)
  also checks the service is loaded and port 4173 answers. Running from the checkout
  is the user's choice (2026-10-16): the dashboard needs the repository to build, and
  pulling or switching branches changes what the service serves at its next restart.
  The CLI takes the checkout as `--repo` (default: the current directory, which must
  hold `dashboard/package.json`), since an installed CLI has no checkout of its own.
- **D10: One `agent-introspection setup` command.** It runs the whole single-machine
  path, plan first (`--dry-run` prints every step and file change), from flags the
  onboarding skill collects: `--clickhouse-url`, `--clickhouse-user`,
  `--clickhouse-password-command`, `--otlp-endpoint`, `--harness` (repeatable; default
  `claude-code codex`), `--prompt-export`, `--openrouter-key-command`,
  `--backfill-days` (default 90), `--no-dashboard`. Steps: write or update the config
  (backed up), doctor's connection and preflight checks (stop on fail), `facts
install`, `facts backfill`, `harness configure` per harness (export, hooks, opt-in
  prompt export), `facts schedule install`, `dashboard install`, then `doctor`. Each
  step is idempotent, so a rerun is a reinstall. It never installs SigNoz;
  `install-signoz.sh` stays the opt-in, separate step. Its options are one dataclass
  (ruff `max-args`), and it refuses an additional machine (`require_host`), pointing at
  the additional machines workflow.

## Project attribution removal

### Removed outright (project is the only data)

| Item                                     | Where                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Signal `pipeline.project_attribution`    | `signal_support.toml`; Pipeline panel (`project_coverage`, `project_sync`, `project_rejections`); freshness row `attribution` in `pipeline.ts`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Signal `recur.project_concentration`     | `signal_support.toml`; Recurrence KPI (localized signatures) and the concentration table; `LOCALIZED_SHARE`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| Repeated-correction detector             | `findings.py` (`facts.repeated_correction` moves to `RETIRED_DETECTORS`; the actionable count reads `DETECTOR_ID` only), `evidence.correction_queries` and the pack's `projects` entry, `proposals.py` subject branch (a non-cluster subject raises), `evaluation.py` `correction_task_rate`, `drafting.py` prompt text                                                                                                                                                                                                                                                                                                                                                                                                                |
| Intent "Repeated corrections by project" | `repeated`, `repeated_count`, its KPI, `REPEATED_PAIRS`, `REPEAT_MIN_*`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| Interventions repeated-correction panel  | the findings table, `correction_daily`, `kpis.corrections`, the `corrections` dataset, `windowCounts`' correction branch, `DAILY_INPUTS`; `SUBJECT_SCALARS`, `isCorrection`, and the subject label stay so dormant correction findings still render (as retired scan detectors do)                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| Guardrails "Where to tune project rules" | `verification_projects`. Its project-free remainder is kept: "Verification runs by kind" gains `caught` and `last_seen` per kind × harness (today caught exists only per harness)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Capability `session_store`               | `signal_support.toml` `[[capability]]` and `requires`, `capabilities.py`, every harness profile (`session_store`, `session_store_spec`, including opencode's), `profiles.py`, `producers.py`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Attribution pipeline                     | `sessions.py`, `session_stores.toml`, `select_session_projects.sql`, `introspection.session_projects` table and `session_project` view, loaders `load_session_projects` and `sweep_session_projects` (`cli_facts._LOADERS`, `refreshers.ts`), the `session-project` record family (`spool.py` schema, `FAMILIES`, `_COUNTED`, the `sessions.resolve` call in `sync`; `record-route.ts` family list, heartbeat join, counters; Pipeline's `attribution_sent`/`attribution_received` delivery columns and freshness rows), `facts sessions`, `[sessions.roots]` (to `_RETIRED_KEYS`), doctor's `session_store` check and store loading, `session-scan.json`, the `sessions.py` entry in `check-python-quality.py`'s subprocess allowlist |
| Candidate `project_root`                 | `candidates.py`; `drafting.working_directory` uses the fallback directory                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| Docs                                     | `project-attribution.md` (deleted); README, `hook-events.md`, `data-gaps.md`, `producer-onboarding.md`, `docs/ux/discovery.md` (DF6), `docs/todo/opencode-onboarding.md`, the three skills' workflows; generated `signal-catalog.md` and `producer-capabilities.md` via `scripts/render-measures.py`                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Feature audit                            | Each removed title in `titles.json` gets `removed` naming a new `DCnn` row in `docs/ux/feature-audit/README.md`: "Project attribution", "Project concentration", "Repeated corrections" (Intent KPI and Interventions), "Repeated corrections by project", "Where to tune project rules", and the "Which corrections repeat?" section if only "Recent corrected tasks" remains (it is renamed instead). Renamed titles (Friction "Where to look") are edited in place; the per-view audit docs are updated                                                                                                                                                                                                                             |

`facts install` explicitly drops `load_session_projects`, `sweep_session_projects`,
`introspection.session_project`, and `introspection.session_projects` (`DROP … IF
EXISTS`): once the loaders leave `LOADERS`, nothing else would drop them, and they
would keep selecting into a dropped table.

### Column removed, calculation reconsidered

| Panel                                                                        | Change                                                                                                                                                                                                                       |
| ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Failure clusters (`failures()`, findings `WINDOW_QUERY`, Recurrence targets) | Join to `session_project` removed; exclusion by `c.workdir` (D3). Unchanged otherwise.                                                                                                                                       |
| Friction "where to look"                                                     | Was project × failure class over attributed tasks only (`INNER JOIN`, `project != ''`). Becomes failure class over **all** friction tasks, with harnesses, tasks, failures, last seen. Title and subtitle drop "by project". |
| Friction tasks table                                                         | `project` column removed.                                                                                                                                                                                                    |
| Tools repeated calls                                                         | `project` column and its `LEFT JOIN` removed.                                                                                                                                                                                |
| Intent recent corrections                                                    | `project` column removed; the `project_root` exclusion goes (labels carry no workdir; evaluation runs are headless `codex_exec`, already excluded).                                                                          |
| Interventions baseline                                                       | Corrections per 100 labelled tasks was over tasks **with a project**; adopts Intent's `CORRECTED_N` / `CORRECTED_DEN` verbatim (drops the `labelled = 1` filter and the join). Subtitle updated.                             |

### Workflow store

Findings are immutable. The 8 existing `facts.repeated_correction` rows stay as
history. Adding the id to `RETIRED_DETECTORS` makes the host's next `facts sync` move
any non-dormant one to `dormant` through `findings._stale`, so `candidates.py` never
offers one again. Readers that switch on the detector id keep rendering a dormant
correction subject. The unused
`findings.project_identity_id` column is dropped from `workflow.sql` for new stores;
existing stores keep the nullable column untouched (SQLite `STRICT` tables tolerate
it, and no code reads it).

## Onboarding changes

| Before                                                    | After                                       |
| --------------------------------------------------------- | ------------------------------------------- |
| Config hand-edited from `config.example.toml`             | `setup` writes it; the example documents it |
| docker or http mode, `[dashboard] clickhouse_url`         | `[signoz] clickhouse_url` (D4)              |
| `facts preflight`, then `doctor`                          | `doctor` (D8)                               |
| quality gates in the install path                         | developer-only (D8)                         |
| shell copy loop, `install_activity.py`, `omp plugin link` | `harness configure` (D5)                    |
| set `[hooks]` after `facts install`                       | defaulted (D6)                              |
| three key routes                                          | `openrouter_key_command` (D7)               |
| `bun install && bun run build && bun run start`           | `dashboard install` service (D9)            |
| all registry harnesses by default                         | Claude Code and Codex (D1)                  |

The onboarding skill's setup workflow becomes: `signoz detect`; offer
`install-signoz.sh` only when none is found and the user asks; ask the user's
decisions (harnesses, prompt export, OpenRouter key command); `setup --dry-run`; show
it; `setup`; `doctor`; validation. The harness configuration workflow keeps the
per-harness detail for reruns and opt-ins (omp, prompt export, extra Codex homes).

## Phases

1. **Attribution removal** (Python, SQL, registry, profiles, dashboard, tests, docs).
2. **HTTP only and hooks endpoint default** (D4, D6).
3. **Hooks in `harness configure`** (D5), **one key route** (D7), **default harnesses**
   (D1), **one check command** (D8).
4. **Dashboard service** (D9).
5. **`setup` command** (D10), then the skills, README, setup guide, and CLI reference.

Phase 1 also covers the tests that pin attribution (`test_sessions.py` deleted;
`test_cli_sync.py`, `test_facts_schedule.py`, `test_facts.py`, `test_findings.py`,
`test_doctor_harness.py`, `tests/support/doctor_machine.py`, `test_producers.py`,
`test_profiles.py`, `test_capabilities.py`, `test_python_quality_scope.py`, and the
dashboard's `views`, `pipeline`, `interventions`, `partial`, and `server` tests). Phase 3
updates the three `test_facts.py` cases that assert an absent list enables every
harness.

Each phase ends with `bash scripts/run-ci-quality-gates.sh`, `uv run pytest`, and the
dashboard's `bun test`, and is one commit.

## Safety rules for this work

- Never run `facts install`, `backfill`, `sync`, `schedule install`, `dashboard
install`, or `setup` without `--dry-run` against this machine, and never run
  `uv tool install`.
- Never edit `~/.claude`, `~/.codex`, `~/.omp`, `~/.config/agent-introspection`,
  LaunchAgents, or systemd units on this machine.
- Read-only ClickHouse and workflow-store queries print only names and counts.
