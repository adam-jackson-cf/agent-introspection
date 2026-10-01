# Dashboard v3 Plan

Status: **active** from 2026-09-28. This plan replaces the proof-gated approach,
whose plans, proof register, and Measure v2 remain in git at the tag
`archive/proof-gated-dashboard`.

## Objective

Surface every measure the producers actually support, so that one person can
explore model usage and agent process on this machine and find problems and
improvements. The questions come from Dashboard Measure v2 (at the archive tag) and are now
defined in [Dashboard Measure v3](dashboard-measure-v3.md), extended with cache efficiency and reasoning-effort effectiveness.

Completion means each question-led view below ships with real data, and each
measure the producers cannot support is recorded as unsupported with its reason.

## What changed and why

The previous plan treated a single-user local tool like audited multi-party
production:

- Row-level EvidenceBundles, independent oracles, and a 129-row proof matrix
  blocked every measure. After four weeks, 0 rows were proven.
- The retained request and task audits classified fields as absent from a
  hard-coded list and never queried SigNoz. The raw telemetry contains most of
  those fields (see [Data availability](#data-availability)).
- The pipeline re-emitted derived telemetry to SigNoz and read it back. The
  local ledger grew to 6.3 GB, including 2.0M outbox rows, while holding only
  17 canonical activities.
- Each dashboard request pulled millions of raw rows into Python. A 31-day
  Pipeline request took 41.6 s against a 14.5 s deadline.

## Decisions

| ID  | Decision                                                                                                                                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| D1  | Facts are materialized inside ClickHouse (database `introspection`), not in SQLite.                                                          |
| D2  | Retire the SigNoz re-emission (outbox), read-back, and proof register.                                                                       |
| D3  | Revise the measures document to v3. Measures are per call where no logical request ID exists, and inferred values are allowed when labelled. |
| D4  | Store only normalized forms of prompts, commands, and tool output. Raw text is never copied.                                                 |
| D5  | Keep the React companion, rebuilt as question-led views.                                                                                     |

## Architecture

```text
Producers: omp · Codex (app-server, CLI, exec) · Claude Code
  └─> SigNoz ClickHouse raw tables (90-day retention from 2026-09-28; was 15 days for traces)
        └─> refreshable materialized views in `introspection`
              load_spans   every 1 min  spans that ended in the last 30 min (1-day lookback)
              sweep_spans  every 1 h    all spans from the last 3 days (late exports)
              load_logs    every 1 min  logs ingested in the last 30 min (by observed_timestamp)
              sweep_logs   every 1 h    all logs from the last 3 days (late inserts, F20)
                └─> introspection.spans / introspection.logs
                    (ReplacingMergeTree, no TTL, Codex plumbing spans dropped,
                     raw text and identity keys removed, normalized x.* fields added)
                      └─> views: usage_events · task_outcomes · tool_calls · user_signals · model_calls
                            └─> <view>_snapshot tables: last 91 days, rebuilt right after each minute load
                                  └─> Bun server: small aggregate queries over ClickHouse HTTP → React views
signal_support.toml ─ facts install ─> introspection.signals · signal_routes · signal_support · signal_strays
Harness session stores (session_stores.toml) ─ facts sync, launchd every minute ─> introspection.session_projects
  └─> session_project (attributed sessions' Git project, joined by session ID); activity hooks ─> hook-inbox ─ facts sync ─> introspection.hook_events
SQLite workflow store (findings, proposals, review sessions): ~/.local/share/agent-introspection/introspection.sqlite3
```

The source is [`facts.py`](../src/agent_introspection/facts.py) and
[`facts_sql/`](../src/agent_introspection/facts_sql/). Operate it with
`agent-introspection facts install | backfill --days N | status | sessions | sync | schedule`.
The human-readable catalog of every signal is [Dashboard Measure v3](dashboard-measure-v3.md).

Serving (phase 2):

- **Transport.** ClickHouse HTTP at `http://signoz-clickhouse.orb.local:8123`, the
  address OrbStack gives the container on this Mac only. No port is published and the
  SigNoz stack is untouched. A call costs about 20 ms, against about 400 ms for
  `docker exec`. Statements run with `readonly=2`, and the window and harness are bound
  as query parameters. Each view's named queries go as one request: every query is a
  `UNION ALL` branch that tags its rows and renders them with
  `formatRowNoNewline('JSONEachRow', *)` (see F15).
- **Snapshots.** Each fact view has a `<view>_snapshot` table. A non-append
  refreshable view rebuilds it every minute, `DEPENDS ON` both minute loaders, over
  the last 91 days only (`additional_table_filters` bounds every span and log read), so
  its cost stays bounded however long the durable history grows. The server reads the
  snapshots for windows that start within 90 days and the live views otherwise. The
  view SQL, and so its parity with agent-observability, is unchanged; views read the
  snapshots in 30–40 ms instead of 0.3–0.8 s.
- **Registry tables.** `facts install` validates `signal_support.toml` (every
  harness-scoped signal covers every harness, `differs` and `not emitted` carry a
  note, routes exist) and runs every route predicate through ClickHouse before
  loading four tables: `signals` (definitions), `signal_routes` (per-harness
  predicates, `expect` rows or events), `signal_support` (signal, harness, route,
  unit, alignment, note), and `signal_strays` (explained stray rows).

Window design:

- Spans are exported when they end, and turns can run for more than a day.
  The span loader therefore keys on end time.
- Logs can arrive up to about 2.3 days late (p99). The log loader therefore keys
  on ingest time.

### Normalized fields (D4)

For Codex `codex.tool_result`, the loader derives the following fields and discards
`arguments`, `output`, and `content`:

| Field                                                       | Meaning                                                                                                                                                                                                                 |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `x.command_head`, `x.command_sub`                           | First command token (env assignments skipped) and a lowercase subcommand, e.g. `git` `commit`. The command is the `cmd` argument, or for the JavaScript `exec` tool the first nested `cmd` it passes to `exec_command`. |
| `x.gate_bypass`                                             | `1` when the arguments contain `--no-verify`, `HUSKY=0`, `SKIP=`, or `--no-gpg-sign`.                                                                                                                                   |
| `x.targets`                                                 | Files from `apply_patch` headers, `path` arguments, and path-like command arguments (a slash or a known file extension; inferred), with the home directory shown as `~`; at most 20.                                    |
| `x.workdir`                                                 | Working directory, with the home directory shown as `~`.                                                                                                                                                                |
| `x.exit_code`                                               | Parsed from `Process exited with code N` / `Exit code: N`. Codex reports `success=true` for non-zero exits.                                                                                                             |
| `x.failure_signature`                                       | For failed calls: the first error-like output line, with digits and hex runs replaced by `N`, capped at 160 characters.                                                                                                 |
| `x.arguments_hash`, `x.arguments_length`, `x.output_length` | For repeat detection and size.                                                                                                                                                                                          |

Prompt text (`prompt`, `user_prompt`) and identity keys (`user.email`, account,
organization, and user IDs) are dropped from spans and logs; `prompt_length` is kept.
omp tool intent text (`pi.gen_ai.tool.call.intent`) is dropped. Span status messages
keep only the first error-like line, normalized like `x.failure_signature`, and span
names have home directories shown as `~`. Codex spans are kept by allowlist
(`session_task.*`, `*sampling_request`, `turn/start|interrupt|steer`, and
`handle_responses` with usage); everything else Codex emits is plumbing.

## Verification discipline

Correctness evidence is lightweight and repeatable, following
agent-observability's `audit.md` method:

1. **Source parity.** A fact view must equal the validated raw-source SQL for
   the same window. This was verified on 2026-09-28 for 2026-09-21..28:
   - `usage_events` matched agent-observability's `usage_events` exactly on
     operations, input, cached, and output tokens for all four producers with data.
   - `task_outcomes` matched its reasoning-effort `task_outcomes` exactly on
     count, interrupted, clean completion, model steps, and reasoning tokens.
2. **Partition invariants.** Cached input never exceeds input: 0 violations.
   Daily token sums equal the range totals.
3. **Sanitization.** No raw text or identity keys are stored: 0 rows.
4. **Findings log.** Anomalies are recorded as finding, evidence, impact, fix,
   and validated result. They are never silently patched.

## Data availability

The inventory was taken on 2026-09-28 over 7 days. Legend: ✅ native field,
◐ partial or inferred (labelled), ✗ absent.

| Capability                       | omp                                   | Codex                                        | Claude Code                                   |
| -------------------------------- | ------------------------------------- | -------------------------------------------- | --------------------------------------------- |
| Session identity                 | ✅ `gen_ai.conversation.id`           | ✅ logs `conversation.id`; turns `thread.id` | ✅ `session.id`                               |
| Tokens and cache                 | ✅ incl. reasoning and cache creation | ✅ incl. reasoning; cache writes always 0    | ✅ no reasoning                               |
| Reasoning effort                 | ✅                                    | ✅                                           | ◐ `effort`, rarely set                        |
| Model call latency               | ✅ span duration, first chunk         | ✅ `ttft_ms`, turn/sampling spans            | ✅ `ttft_ms`, `duration_ms`                   |
| Call errors                      | ✅ span status                        | ◐ `/responses` attempts only                 | ✅ HTTP `status_code`, `attempt`              |
| Logical request / retry identity | ✗                                     | ◐ `try_run_` vs `run_sampling_request`       | ◐ `attempt`                                   |
| Tool outcome                     | ✅ ok/error/aborted                   | ✅ via `x.exit_code` + `success`             | ✅ `success`, `error_type`                    |
| Command shape                    | ◐ intent text                         | ✅ normalized `x.*`                          | ◐ `bash_argv0`, class                         |
| Sandbox / approvals              | ✗                                     | ✅ `tool_decision`, `sandbox_outcome`        | ◐ `tool_decision`                             |
| User interrupt / steer           | ◐ aborted status                      | ✅ `turn/interrupt`, `turn/steer`            | ✗ (prompts only)                              |
| Task boundary                    | ✅ root `invoke_agent`                | ✅ `session_task.turn`                       | ✅ `claude_code.interaction` with model calls |
| Explicit task success            | ✗                                     | ✗                                            | ✗                                             |
| Project / cwd                    | ✗                                     | ◐ `cwd` on sampling spans                    | ✗                                             |

Known boundaries:

- Claude interactions without model calls (median 2 s, average prompt length 6)
  are slash commands and are excluded from tasks.
- `codex-auto-review` turns are the automated approval reviewer. They are
  excluded from tasks but count as spend.
- `codex_exec` runs are headless, so follow-up and clean completion are NULL.
- No producer emits cost comparably. Spend is measured in tokens.

## Signal alignment across producers

This is not a benchmark across harnesses. Harnesses are never ranked or
compared against each other. The goal is to surface **the same signal** from
every producer that has it, so that viewing one harness or all harnesses means
looking at the same thing. Producers reach a signal by different routes; that is
expected. agent-observability follows the same approach: align the definition as
closely as the data allows, and say in the panel's info note where a producer's
route differs.

Rules:

1. **One definition per signal, one route per producer.** Measures v3 defines
   each signal once (question, unit, formula). Per producer, it records:
   - the route (exact source fields and boundary);
   - `aligned` or `differs`, with a one-line note of how it differs;
   - or `not emitted`.
     A producer that doesn't emit a signal is shown as not emitted, never as zero.
2. **Signal support registry.** The per-signal, per-producer records live in a
   repo-owned file. `facts install` loads it into `introspection.signal_support`
   (signal, harness, route, unit, alignment, note). Panels read it to render
   their info notes and to label which harnesses contribute. Notes are never
   hand-written in the UI.
3. **Keep the aligned definition; disclose the gaps.** Where a composite signal
   depends on components some producers can't observe, keep the shared
   definition and record which components were observable. Don't drop the
   producer.
   - Example: clean completion means "no observed friction". Claude Code cannot
     report interrupts; Codex cannot report task errors.
   - Rows carry the observable components (for example
     `friction_observed = ['interrupt','follow_up']`), and the info note states them.
4. **All = the union of each.** "All harnesses" aggregates the per-harness rows
   of the same signal. Each panel shows which harnesses contributed data in the
   selected window. A parity check confirms that per-harness values recombine to
   the All value: sums for counts, weighted numerators and denominators for ratios.
5. **Coverage grid.** The Pipeline view compares the registry with the data for
   the selected window:

   | Registry says | Data shows   | Flag                                           |
   | ------------- | ------------ | ---------------------------------------------- |
   | emitted       | rows present | healthy                                        |
   | emitted       | no rows      | possible producer or loader break              |
   | not emitted   | rows present | registry is stale, or the rows are mislabelled |

   <!-- end of coverage grid table -->
   - Example of mislabelled rows: 58 Claude-style events tagged `oh-my-pi` on
     2026-09-13, which are not a live omp route.
   - The grid must distinguish a real route from stray rows, so the registry
     names routes rather than just "has data".

Known alignment notes to carry into the registry:

- **Task.** A Codex turn, an omp root agent run (includes subagents and
  advisors), and a Claude Code interaction with model calls. Codex subagent
  threads appear as separate tasks.
- **Interrupt.** Codex: the user interrupts a turn. omp: the run's aborted stop
  reason, or an aborted tool call. Claude Code: not emitted.
- **Task error.** omp: the run's error stop reason. Claude Code: any LLM request
  with an error or status ≥ 400. Codex: not emitted.
- **Input tokens.** These include cache reads for all producers, and cache creation
  for Claude Code (summed) and omp (inside `gen_ai.usage.input_tokens`, split out as
  `gen_ai.usage.cache_creation.input_tokens`).
- **Model operation outcome.** Codex: `/responses` attempt. Claude Code: LLM
  request. omp: chat span.

## Views

Each view answers one question, in outcome-first order: KPI tiles → daily
trends → breakdowns by harness and model → exemplar tables → drill-down to
session. Every panel has a harness selector (All or one harness) and an info
note generated from the signal support registry.

| #   | View                   | Questions (v2 IDs)                                                                                                                                                                                                | Data                            |
| --- | ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------- |
| V1  | Pipeline               | Is the loader fresh and complete? Freshness per harness, refresh status, rows, parity, and sanitization checks. Replaces P1–P12.                                                                                  | ✅                              |
| V2  | Cache efficiency       | Token consumption, cache utilization, uncached input by harness/model/session (M2).                                                                                                                               | ✅                              |
| V3  | Reasoning effort       | Does heavier effort earn its cost?                                                                                                                                                                                | ✅                              |
| V4  | Tool failures          | Failure rate, failure signatures, tasks affected, repeats and loops (M5, M7, M9).                                                                                                                                 | ✅                              |
| V5  | Friction               | Interrupts, steers, quick follow-ups, clean completion (M3, M4); recovery after failure (M6, inferred).                                                                                                           | ◐                               |
| V6  | Guardrails             | Sandbox denials, approvals, gate bypass, command churn (M8, M10, M11).                                                                                                                                            | ◐ mainly Codex                  |
| V7  | Provider               | Latency/TTFT, call errors, streaming disconnects, token throughput, unknown outcomes, model conformance (R1–R8, per call).                                                                                        | ◐                               |
| V8  | Recurrence             | Files and signatures recurring across tasks, actionable repeats on a 7-day Europe/London window, project concentration (M13, M14, M16).                                                                           | ✅ / ◐ project                  |
| V9  | Intent and corrections | Task type, corrected by the next prompt, correction kinds, frustrated follow-ups, effort payoff by task type, repeated corrections by project (Jev prompt labels from the activity hooks).                        | ✅ from the hooks' install date |
| V10 | Interventions          | Findings and proposals, post-intervention evaluation against each proposal's structured success metric, practice recurrence (repeated corrections), tier audit (M15, M17, M18). Rule adherence (M12) is excluded. | ✅                              |

## Phases

- **Phase 0: Archive the proof-gated work**
  - Exit condition: Commit and tag `archive/proof-gated-dashboard`
  - State: Done 2026-09-28
- **Phase 1: ClickHouse facts: tables, loaders, views, backfill**
  - Exit condition: Parity and invariants pass; loaders refresh every minute
  - State: Done 2026-09-28
- **Phase 2: Signal support registry loaded by `facts install`, with a draft entry for every signal in V1–V9; `task_outcomes` records the friction components each row could observe. Serving: Bun queries `introspection` directly; harness selector and registry-driven info notes as shared components; V1 Pipeline view with the coverage grid; remove the Python-per-request path and the registry dependency on the proof register**
  - Exit condition: Coverage grid has no unexplained cells; 90-day views < 1 s; data < 2 min old
  - State: Done 2026-09-28: 0 unexplained cells over 90 days (201 healthy, 38 not emitted, 3 no events, 3 explained strays); Pipeline 90-day 0.36 s warm (first request after a server start ≈ 1.1 s), other views 0.13–0.22 s; fact lag 0–10 s
- **Phase 3: V2 Cache efficiency, V3 Reasoning effort, V4 Tool failures**
  - Exit condition: Views show real data for every harness that emits each signal; registry-driven info notes; per-harness values recombine to All
  - State: Done 2026-09-28: all five harnesses show data; input, cached, output, sessions, reasoning, tasks, clean, tool calls, failures, and failed tasks reconcile exactly with direct view queries per harness and for All (90 days); 0.13–0.22 s per view
- **Phase 4: V5 Friction, V6 Guardrails**
  - Exit condition: Same
  - State: Done 2026-09-28: interrupts, steers, errors, follow-up denominators, approval decisions, sandbox outcomes, and shell commands reconcile exactly per harness and for All (90 days); omp and Claude Code show not emitted for Codex-only guardrails
- **Phase 5: V7 Provider**
  - Exit condition: Same
  - State: Done 2026-09-28: model calls, failures, cancellations, stream disconnects, and Codex sampling steps reconcile exactly per harness and for All (90 days); new fact view `model_calls` (invariant: recombination check `provider` in V1)
- **Phase 6: V8 Recurrence, then V9 Interventions**
  - Exit condition: Same
  - State: Done 2026-09-28: recurring signatures, recurring files, signature failures, and baseline tasks reconcile exactly per harness and for All (90 days); V9 shows the 6 workflow findings, 0 proposals, 0 applied interventions, and records rule adherence and practice recurrence as unsupported; all nine views p50 0.06–0.54 s, p95 ≤ 0.65 s except the first Pipeline request after a server start (≈ 1.3 s)
- **Phase 7: Retire the old pipeline: remove the launchd scan schedule, outbox, read-back, and `agent_introspection` ClickHouse store; delete the `pipeline_*` and proof-experiment code; archive the 6.3 GB ledger; write Measures v3 as the view catalog**
  - Exit condition: One pipeline and one set of docs
  - State: Done 2026-09-29: scan job removed; `agent_introspection` dropped (2.66 GiB); scan pipeline, outbox, detectors, SigNoz JSON dashboards, proof experiments, and their tests deleted; project capture kept and re-routed through `facts sync-projects`; ledger and both migration backups archived byte-identical to `/Volumes/UGreen-External/archive/agent-introspection-2026-09-28/`; workflow tables moved to a 94 KB store; proof docs, Measure v2, and mocks moved to `docs/retired/`, then deleted on 2026-09-29 (kept at the archive tag); [Measure v3](dashboard-measure-v3.md) generated from the registry
- **Phase 8: Closed loop and harness parity: activity hooks close native gaps; parity rule enforced by the registry; sharper failure signatures; failure-cluster and repeated-correction findings ranked by impact; evidence packs, Codex-drafted proposals with structured success metrics, and automatic evaluation; Jev prompt labels and the Intent view; skills rewritten; fresh cutover**
  - Exit condition: Every harness-scoped signal reached by every harness it can exist for, or excluded; the loop runs discover → draft → decide → apply → evaluate
  - State: See F25–F31 and the cutover record

## Findings log

Each anomaly is recorded as finding → evidence → fix → validated result.

**F1. The fact store looked deleted after an OrbStack restart (2026-09-28).**
Evidence: at 11:43Z OrbStack restarted with `data_dir ~/.orbstack-recovery-20260928`.
That snapshot's traces ended on 2026-09-05, and it had no `introspection`
database. The agent-observability project, which was normalizing SigNoz retention to
90 days, caused the switch. Fix: none in this repo. OrbStack is back on
`/Volumes/UGreen-External/Docker`, where raw data now runs from 2026-08-24; 2026-09-06
to 09-12 is unrecoverable. Validated: all databases present, traces and logs TTL 90
days, `facts backfill --days 90` re-projected 2026-08-24 onward.

**F2. Old Codex builds emitted h2/tokio frame spans.** Evidence: after the backfill,
`introspection.spans` held 37.4M rows, mostly `codex_cli_rs` and app-server
`try_reclaim_frame`, `poll_ready`, and `FramedRead::*` from 08-24 to 09-05. Fix: Codex
spans are selected by allowlist, and the stored plumbing rows were deleted (projection
rows only; all re-derivable from SigNoz). Validated: 335k spans after re-backfill;
view parity unchanged.

**F3. Raw tool output was stored in omp span status messages (D4).** Evidence: 1,084
`execute_tool` spans held `status_message` up to 51,390 characters, and 22,598 held
`pi.gen_ai.tool.call.intent` text. Fix: the span loader keeps a normalized first error
line (≤ 160 characters) and drops the intent key. Backfill, then `OPTIMIZE … FINAL`,
purged the old versions. Validated: Pipeline sanitization shows 0 dropped keys, 0
status > 160, 0 home paths in both tables.

**F4. omp does emit cache creation.** Evidence: `gen_ai.usage.cache_creation.input_tokens`
is present on every omp chat span (71 non-zero, 164K tokens); input ≥ cache read +
creation on all rows. Codex emits `cache_write_token_count`, always 0. Fix:
`usage_events.cache_creation_input` reads both; registry `usage.cache_creation` is
aligned for omp, and differs for Codex with the always-0 note. Validated: token,
cached, and output parity with the raw source unchanged.

**F5. Claude Code sessions mislabelled `oh-my-pi` on five days, not one.** Evidence:
the coverage grid flagged 11–36 unexplained stray cells for omp after the 08-24 history
arrived. Claude-style spans and logs under `oh-my-pi` appear on 2026-08-24, 08-28,
09-01, 09-02, and 09-13. Fix: the registered strays cover any Claude-style event or
`claude_code.*` span under omp, with the inferred cause (Claude Code launched from an
omp shell inherits `OTEL_SERVICE_NAME`). The rows are counted neither as omp nor as
Claude Code. Validated: 0 unexplained cells; 3 explained stray cells.

**F6. Rare-event routes read as possible breaks.** Evidence: Codex CLI and exec
emitted no `turn/interrupt` or `turn/steer` in 90 days while otherwise active. Fix:
routes for rare events carry `expect = "events"`, and the grid reports "no events"
for them; boundary routes (usage, tasks, tools) still report a possible break.
Validated: 3 "no events" cells, 0 possible breaks.

**F7. A raw-source parity recount over 90 days took 2.35 s.** Fix: parity covers the
last 7 days of the selected window, ending 10 minutes before now; the panel states the
window. Validated: operations and input, cached, and output tokens match exactly for
all five harnesses; Pipeline 90-day 0.36 s warm.

**F8. Polling tools dominated repeated attempts and loops.** Evidence: the top
repeated-attempt rows were Codex `wait_agent`, `write_stdin`, and `wait` with identical
arguments (e.g. `wait_agent` 121 calls with 8 distinct arguments); every loop was
`wait_agent`. Fix: `tools.repeats` and `tools.loops` exclude waiting and
status-polling tools, stated in the registry formula. Validated: the remaining repeats
are `exec`, `exec_command`, and similar work calls.

**F9. Claude Code started sending `effort` on 2026-09-28.** Evidence: Claude Code
`api_request` effort was `high` on 120 requests across 08-24 to 09-13, then `medium`
on 2,608 requests on 09-28; the registry said effort was rarely set. Fix: registry
notes for `effort.level` and `effort.heavy_share` state the change. Validated: the
V3 info note for Claude Code shows the dated note.

**F10. Two recurring failures worth acting on (observations, not pipeline defects).**
omp `read` failed 474 times in 13 tasks on "Memory file not found:
memory://root/memory_summary.md". Codex exec `cat` failed 98 times, once in each of
98 tasks, on ".agents/by-request/verifcation-tests/SKILL.md: No such file"; the path
misspells "verification". Both appear in V4 failure signatures and are candidates
for V8 actionable repeats.

**F11. Codex's JavaScript `exec` tool hid shell commands.** Evidence: 4,708 Codex `exec`
calls carry JavaScript code (`const …`, `text(await tools.exec_command(…))`) rather
than JSON, so no `x.command_head` was derived; 3,492 of them call `exec_command`. Gate
bypass looked only at `cmd`. Fix: the command is the `cmd` argument, or the first
nested `cmd` in `exec` code; gate bypass matches the whole argument text. Validated:
3,460 `exec` calls now have a command head, and gate bypass is still 0 across all
Codex arguments.

**F12. Command churn was structurally unobservable.** Evidence: shell commands carried
no file targets, only `apply_patch` headers and `path` arguments did, so no file could
reach three distinct commands; V6 showed 0. Fix: path-like command arguments (a slash
or a known file extension, home as `~`, at most 20) join `x.targets`, and the registry
marks `guard.command_churn` and `recur.targets` as `differs` (inferred). Validated:
5,669 Codex calls carry targets, and churn surfaces real cases (e.g. 11 distinct
commands against one app bundle in a task).

**F13. Some Codex CLI turns have no turn span.** Evidence: both `codex_cli_rs`
`turn/steer` spans point to turn `01a0cf0a-…`, which has no `session_task.turn` span,
so no task is marked steered; only 54% of CLI tool calls fall inside a turn span.
Impact: CLI task counts and task-attributed tool signals are undercounts. Fix: none in
this repo (producer behaviour); the registry notes CLI TUI turns omitting token usage,
and the coverage grid shows the route as healthy because other turns arrive.

**F14. User-aborted omp chats counted as provider failures.** Evidence: 168 of the 280
omp chat spans with an error have `error.type = aborted`, the user stopping the run.
Measure v2 R1 keeps client cancellation separate from provider failure. Fix:
`model_calls.outcome` is `cancelled` for them, and the call error rate is failed /
calls not cancelled. Validated: omp 112 failed, 168 cancelled; Codex CLI's 32 failures
are all stream disconnects; totals reconcile.

**F15. Bun stalled about one second on some new connections to OrbStack.** Evidence:
view p95 was 1.0–2.5 s while ClickHouse's query log showed no query over 900 ms. In
isolation, 8 parallel Bun `fetch` (and `node:http`) requests repeated 12 times stalled
3–11 times at 1,023–1,239 ms, by name or IP, with or without keep-alive; curl with the
same pattern never stalled. Fix: one HTTP request per view (batched `UNION ALL`), route
counts cached per window for 60 s, registry loaded at server start, snapshot refreshers
limited to 2 threads. Validated: nine views p50 0.06–0.54 s and p95 ≤ 0.65 s over 90
days, except the first Pipeline request after a restart.

**F16. V9 has little to show yet.** Evidence: the workflow store holds 6 findings
from 2026-08-06, all `emerging`, and no proposals or interventions; no producer emits
rule triggers or explicit task success. Fix: V9 shows the records that exist, a
recurrence baseline for future comparisons, and registry-sourced "unsupported" notes
for rule adherence (M12) and practice recurrence (M17). Validated: baseline task
counts reconcile; post-intervention comparison activates when a proposal is applied.

**F17. Project attribution lived in the retired pipeline's capture half.** Evidence:
the session-context hooks recorded 13.5K events (57 omp, 16 Codex CLI, 9 Claude Code
projects) into an inbox the scan ingested; the scan's correlation and re-emission
produced only 17 attributed activities. Fix (decided with the user): keep the hooks,
retire the scan, and sync the inbox into `introspection.session_projects` every minute;
V8 project concentration and the Pipeline project attribution use it for every
harness. Validated: ledger history (13,545 events), inbox (179 events, 10 rejections)
loaded with 0 home paths; 72% Claude Code, 82% Codex app-server, 97% Codex CLI, 98%
Codex exec, 54% omp of 90-day tasks attributed.

**F18. The workflow store could not survive a clean cut on the ledger schema.**
Evidence: proposals emitted outbox events and migrations imported the delivery
pipeline; the ledger held 54 tables for 6 workflow rows. Fix: a small workflow schema
(findings, proposals, proposal events, review sessions, model runs, budget ledger,
drafts) with the same immutability guards, a rollback journal so the dashboard can open
it read-only, and a proposal-only review flow (classification read retired
observations). The user's config file dropped the retired `[scheduler]`, `[pipeline]`,
and OTLP keys (backup kept). Validated: 6 findings copied; `proposal list`,
`candidates export`, and V9 read the new store.

**F19. SigNoz holds duplicate omp chat spans since 2026-09-28 about 16:00 UTC.**
Evidence: on 2026-09-29, `signoz_index_v3` held 8,332 omp chat rows for 1,447 span IDs
(up to 10 identical copies in one part); copies agree on every token field, duration,
and error flag. Impact: the facts are unaffected (one row per trace and span), but
anything summing raw SigNoz spans over-counts omp 2–7× from then on, including
agent-observability's dashboards. Fix here: the parity recount deduplicates raw spans by
span ID, like the loader. Cause (confirmed by the agent-observability session):
ClickHouse insert-path fsync enabled on 2026-09-28 at 21:50 UTC pushed trace inserts past
the collector's 9 s timeout, so the collector retried batches that had already landed;
the smaller 16:00 UTC bump matches ClickHouse restarts during recovery. Fixed there at
15:07 UTC on 2026-09-29 (insert fsync off, merge fsync kept). Validated: omp parity exact
(14,083 operations); omp spans after 15:07 have one row per span. The existing duplicates
were removed there on 2026-09-29 with the user's approval; omp spans on 2026-09-28 and
09-29 now have one row per (trace, span), and parity is exact for all five harnesses.

**F20. The log loader missed rows inserted late into SigNoz.** Evidence: 3 Claude Code
`api_request` logs from 2026-09-28 17:53–18:10 UTC were in SigNoz but not in the facts;
their observed time equals their event time, so they reached the table more than 30
minutes after observation and fell outside `load_logs`'s window. Fix: an hourly
`sweep_logs` re-reads three days by event time, like `sweep_spans`. Validated: after a
3-day backfill, Claude Code parity is exact (5,160 operations).

**F21. Nothing produced new workflow findings after the scan retired.** Evidence: V9
held the 6 findings from 2026-08-06 and `candidates export` found no actionable
candidate. Fix: `facts sync` promotes each (harness, tool, failure signature) seen in
≥ 2 tasks over the last 7 Europe/London days into a finding with a JSON `subject`:
actionable under the V8 rule, emerging otherwise, dormant once it stops recurring; V9
shows the subject and compares interventions against the finding's own harness and
signature. Validated: 55 findings created, 27 actionable (e.g. omp `bash` "Blocked: Use
the `read` tool instead of cat/head/tail" in 46 tasks); reruns report them unchanged.

**F22. Snapshot rebuild cost grew with the durable history (C3).** Fix (the user chose a
90-day bound to match retention, keeping 1-minute freshness): each snapshot refresh
applies `additional_table_filters` so every span and log read is bounded to the last
91 days; the view SQL is unchanged. The server reads live views for windows that start
more than 90 days ago and for session drill-downs. A first attempt that wrapped each
scan in a bounded subquery was 2–5× slower (filters cannot pass below `FINAL`) and was
dropped. Validated: live views and snapshots return identical row counts for all five
facts; a 30-day window reads snapshots in 236 ms, a 92-day-old window reads live views
in 604 ms.

**F23. ClickHouse load rose about fourfold from 2026-09-28 21:00 UTC.** Evidence:
snapshot refreshes averaged 0.75–1.1 s per hour until 17:00, then 2.5–4.5 s; SigNoz
trace INSERTs account for about 1,260 s per 10 minutes; VM load average about 17. This
came from the same insert-path fsync and retry storm as F19. Fix: in the SigNoz
configuration, by the agent-observability session (see F19). Validated: after 15:07 UTC
on 2026-09-29, snapshot refreshes averaged 0.66–0.81 s per minute (max 0.95 s) and the VM
load average fell to about 8.

**F24. Uncommitted 5-minute snapshot edits from an unidentified session.** Evidence: on
2026-09-29 `facts.py`, the README, the plan, Measure v3, `views.ts`, and a test in this
worktree changed to refresh snapshots every 5 minutes; the other known session on this
repository said they were not its edits. Fix: replaced by the 90-day bound (F22) at the
user's choice, keeping data under 2 minutes old.

**F25. omp renamed its attributes, and the loader stopped reading them (2026-09-29).**
Evidence: from 2026-09-29 14:48 UTC omp emits `omp.gen_ai.*` instead of `pi.gen_ai.*`
(for example 4,904 `omp.gen_ai.request.reasoning.effort` and 8,119 `omp.gen_ai.tool.status`
in the last 4 days, 0 `pi.` after the switch). Effort, interrupt and error flags, and tool
status went `unset` or `unknown` for new omp rows. Fix: the span loader stores
`omp.gen_ai.*` under the original `pi.gen_ai.*` names, so views are unchanged; the
health workflow now checks for renames. Validated: the projection maps tool status and
stop reasons for all omp spans of the last day.

**F26. omp tool intent text leaked into the facts after the rename (D4).** Evidence: the
drop list named `pi.gen_ai.tool.call.intent`; 7,602 spans since the rename carried
`omp.gen_ai.tool.call.intent` text into `introspection.spans`. Fix: intent keys are
dropped under any prefix, and the Pipeline sanitization check lists both names; the
cutover re-projects and purges old row versions. Validated at cutover (see the record).

**F27. SigNoz holds prompts only as a redacted placeholder.** Evidence: all 5,400
`user_prompt` / `codex.user_prompt` events in 30 days carry `prompt` values matching
`REDACTED`. Impact: task classification cannot read prompts from telemetry. Fix: the
activity hooks pass each prompt to Jev at submission and keep only the labels
(`prompt_label`); nothing is classified retroactively.

**F28. First-line failure signatures were often generic.** Evidence: 8 of 74 recurrence
findings were headers such as `Traceback (most recent call last):`, `ShellError`, or
`triggerUncaughtException(`, merging unrelated failures. Fix: signatures take a stack
trace's final exception line, else the first diagnostic line that is not a header or
frame; Claude Code signatures come from `PostToolUseFailure` error text instead of
`error_type`; findings group by tool family × failure class (quoted values and paths
generalized), across harnesses. Validated: on 14 days of Codex failures,
`Traceback`/`Script failed` headers resolve to lines such as `ModuleNotFoundError: No
module named '…'`; the detector dry run gave 37 clusters, 16 actionable.

**F29. Evaluation workloads became findings, and the export picked the oldest.**
Evidence: 6 findings came from `/private/tmp/luna-eval-ws/N` workspaces, and
`candidates export` ordered by `last_seen_ns` ascending. The loop had never run: 0
review sessions and 0 proposals. Fix: evaluation workspaces are excluded, findings carry
an impact score (affected tasks, unclean ones counted twice), export picks the highest
impact and attaches a 14-day evidence pack, `proposal draft` runs the review in Codex,
and `facts sync` evaluates applied proposals.

**F30. Parity gaps: 39 not-emitted cells.** Evidence: the registry had 39 ✗ cells.
Fix (the user's rule: a signal that a harness cannot produce is excluded, never shown as
"not emitted"): activity hooks closed 20 (omp and Claude Code argument identity,
targets, and gate bypass; Claude Code interrupts, steers, reasoning tokens, and
delegations; omp steers, approvals, and retries); native routes closed 6 (Codex
delegations from `codex.agent_communication` spawns, and Codex errored tasks inferred
from the turn's last response stream); 2 are `not applicable` (headless Codex exec
follow-up and clean completion); 11 belong to five excluded signals (sandbox outcomes,
stream disconnects, output throughput, model conformance, and, from a `differs` cell
that was always 0, cache creation). Rule adherence was replaced by per-proposal
evaluation. `facts install` now rejects `not emitted`.

**F31. `/models` polling looks like Codex activity.** Evidence: on 2026-09-29 Codex
app-server logged 18,104 `codex.api_request` rows for `/models` against 12 prompts; real
Codex tasks fell to 1–3 a day after 2026-09-25. No fact route reads `codex.api_request`,
so signals are unaffected, but raw log volume no longer indicates use; the health
workflow says to judge liveness from prompts and tasks.

**F32. Cutover record (2026-09-30).** Steps, in the fresh cutover workflow's order:
workflow store copied to `introspection.sqlite3.pre-2026-09-30`; CLI reinstalled;
`facts install` (59 signals, 255 support cells, 6 snapshots; every route predicate,
including the `hooks` source, validated in ClickHouse); `facts backfill --days 90`
(417,164 spans and 335,166 logs re-projected from 2026-07-02); `OPTIMIZE … FINAL` on
spans and logs; activity hooks installed for Claude Code (`~/.claude/settings.json`,
6 events, backup kept), omp (extensions reinstalled under `adapters/omp/`, and
`agent-introspection-activity.ts` registered in `~/.omp/agent/config.yml`, because
omp's ambient discovery did not load it), and Codex (`UserPromptSubmit` in both the
Orca account root and `~/.codex`, pending interactive trust). Validated: source parity
exact for all five harnesses (7 days); every recombination check passes;
sanitization 0 forbidden keys, 0 long status messages, 0 home paths in spans, logs,
and hook events (the F26 intent text is gone); coverage grid 227 healthy, 7 not
applicable, 6 no events, and 15 possible breaks, all Codex `hook.prompt_label` until
the hook is trusted (that route was later replaced, F33); Claude Code and omp hook events land and join (`tool_call` →
`tool_calls` by tool call ID; `prompt_label`, `tool_failure`, `turn_stop`); findings
51 active (18 actionable failure clusters after generic classes were held at
emerging), 81 retired findings dormant; `proposal draft --dry-run` exports the top
cluster (omp `error: command not found: python`, impact 49) with a complete evidence
pack. Two defects found and fixed during the cutover: the hook insert let ClickHouse
infer the attrs objects as tuples (now a declared structure), and the `task_labels`
snapshot recomputed `task_outcomes` (18 s per refresh; now reads the snapshot, under
1 s). The dashboard read live views for "Last 90 days" because the window started a
moment before the 90-day horizon; it now uses the snapshot's extra day, and 90-day
views answer in 36–161 ms (Pipeline 1.4–2.2 s with its raw recount).

**F33. Prompt labels moved from the hooks into the app (2026-09-30, the user's choice).**
The producers now export prompt text to SigNoz: Claude Code `OTEL_LOG_USER_PROMPTS=1`,
Codex `[otel] log_user_prompt = true` in both Codex roots, and an `omp.user_prompt`
OTLP log from the omp activity extension (omp has no prompt-only option: its content
capture keeps the first 16 messages of each chat cut to 240 characters, or the whole
conversation). `facts sync` labels new prompts with Jev (retrying a failed decision up
to three times) into `introspection.prompt_labels`; the Codex activity hook was
removed from both roots, and the hooks keep only Claude Code's `prompt_submitted`.
Consequence: prompt text is now stored in SigNoz's own tables for their 90-day
retention (the facts store still drops it). The launchd job's PATH lacked
`/opt/homebrew/bin`, so labelling was skipped for want of `omp token openrouter`; the
job's PATH now includes omp's directory. Validated: one prompt each from omp, Codex
exec, and a new Claude Code process reached SigNoz with text and was labelled
(3 labels, $0.0001); `introspection.logs` holds no `prompt` key.

**F34. Existing SigNoz installations.** The CLI reached ClickHouse only through
`docker exec` into a local container. It now has an HTTP mode (`clickhouse_url`,
`clickhouse_user`, and the password from `clickhouse_password_env` or
`clickhouse_password_command`, the latter usable by the launchd job), a read-only
`facts preflight` (ClickHouse ≥ 24.10, SigNoz table columns, grants, producer data),
and dashboard basic auth. Validated: preflight passes against this Mac's SigNoz
(ClickHouse 25.5.6.14).

**F35. Local only, with no SigNoz installer (2026-09-30, the user's decision).** Agent
Introspection runs on one machine against a self-hosted SigNoz already installed on
it. The stack bootstrap workflow, `ops/signoz/docker-compose.override.yaml`, and
`.infisical.json` were removed (the running SigNoz uses its own override under
`~/.local/share/codex-observability/signoz/deploy/docker`, so nothing it depends on was
removed). The config refuses any ClickHouse or OTLP address that is not loopback,
`localhost`, or `*.orb.local`; SigNoz Cloud and multi-node ClickHouse are unsupported
and called out in the README. The "fresh cutover" workflow is now the setup workflow:
install, or reinstall after a breaking change, against the existing local SigNoz.

**F36. Attribution from session stores, and enabled harnesses (2026-10-01, clean
cutover).** Project attribution no longer uses hooks: `facts sync` reads each session's
ID and working directory from the harness's own session store (`session_stores.toml`:
Claude Code transcripts, Codex rollouts including `archived_sessions`, omp session
files) and resolves the Git root locally; a session whose workspace is gone is matched
to a repository on this machine by its recorded Git remote (Codex). The session-context
runtime, adapters, Codex `notify` chain entry, Codex Desktop installer, and their
inbox records were removed from the repository and this machine (archived under
`~/.local/share/agent-introspection/backups/pre-cutover-20261001T105312Z`); the
activity hooks moved to `~/.local/lib/agent-introspection/activity-hooks-v1` and the
`hook-inbox`; the launchd job is `com.adamjackson.agent-introspection.sync`. Parity is
judged over `[harnesses] enabled`: a signal an enabled harness cannot produce is hidden
on this machine, and the five previously excluded signals are back for machines whose
harnesses all produce them. Clean cut: the old attribution tables, hook events of the
retired prompt routes, and the workflow store were replaced, not migrated. Validated:
4,608 sessions resolved in about 5 s (3,682 attributed, 362 of them by remote); 30-day
task attribution 99% Claude Code (was 86%), 97% Codex exec, 85% Codex CLI, 74% Codex
app-server (pruned sessions), 30% omp (batch runs without session files); parity and
recombination exact; sanitization 0 everywhere after fixing multi-path home redaction
in hook targets; coverage grid 243 healthy, 7 not applicable, 5 no events; 42 findings
(19 actionable) from the first sync.

## Operations

- **Retention:** SigNoz keeps 90 days of raw traces and logs. The `introspection`
  tables have no TTL and hold history beyond that. Trace history before
  2026-09-10 had already expired under the earlier 15-day TTL and cannot be recovered.
- **Sync and findings:** `agent-introspection facts schedule install|status|remove`
  manages the `com.adamjackson.agent-introspection.sync` LaunchAgent, which runs
  `facts sync` (hook events, session attribution, prompt labels, findings, evaluations)
  every minute from the standalone tool install at `~/.local/share/uv/tools/agent-introspection`.
  After changing the Python package, run `uv tool install --force --reinstall .` from the repo.
  Never drop `hook_events` (the only copy of activity-hook history) or `session_projects`
  (it keeps attribution for sessions the harnesses have since pruned).
- **Rebuild:** `facts install` recreates loaders and views without touching data.
  `facts backfill --days 90` rebuilds or re-projects everything still in SigNoz,
  so a projection change applies to the full 90-day window.
- **Backups:** the `introspection` tables live in the SigNoz ClickHouse volume
  under `/Volumes/UGreen-External/Docker`. Only history older than 90 days is
  irreplaceable. Back it up before any SigNoz upgrade or stack recreation once the
  store holds more than 90 days.
