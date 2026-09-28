# Dashboard v3 Plan

Status: **active** from 2026-09-28. This plan replaces the proof-gated approach
recorded in [`retired/`](retired/) and tagged in git as
`archive/proof-gated-dashboard`.

## Objective

Surface every measure the producers actually support, so that one person can
explore model usage and agent process on this machine and find problems and
improvements. The questions come from [Dashboard Measure v2](dashboard-measure-v2.md)
and are extended with cache efficiency and reasoning-effort effectiveness.

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

| ID | Decision |
| --- | --- |
| D1 | Facts are materialized inside ClickHouse (database `introspection`), not in SQLite. |
| D2 | Retire the SigNoz re-emission (outbox), read-back, and proof register. |
| D3 | Revise the measures document to v3. Measures are per call where no logical request ID exists, and inferred values are allowed when labelled. |
| D4 | Store only normalized forms of prompts, commands, and tool output. Raw text is never copied. |
| D5 | Keep the React companion, rebuilt as question-led views. |

## Architecture

```text
Producers: omp · Codex (app-server, CLI, exec) · Claude Code
  └─> SigNoz ClickHouse raw tables (90-day retention from 2026-09-28; was 15 days for traces)
        └─> refreshable materialized views in `introspection`
              load_spans   every 1 min  spans that ended in the last 30 min (1-day lookback)
              sweep_spans  every 1 h    all spans from the last 3 days (late exports)
              load_logs    every 1 min  logs ingested in the last 30 min (by observed_timestamp)
                └─> introspection.spans / introspection.logs
                    (ReplacingMergeTree, no TTL, Codex plumbing spans dropped,
                     raw text and identity keys removed, normalized x.* fields added)
                      └─> views: usage_events · task_outcomes · tool_calls · user_signals · model_calls
                            └─> <view>_snapshot tables, refreshed right after each minute load
                                  └─> Bun server: small aggregate queries over ClickHouse HTTP → React views
signal_support.toml ─ facts install ─> introspection.signals · signal_routes · signal_support · signal_strays
SQLite keeps only app workflow state (findings, proposals, interventions).
```

The source is [`facts.py`](../src/agent_introspection/facts.py) and
[`facts_sql/`](../src/agent_introspection/facts_sql/). Operate it with
`agent-introspection facts install | backfill --days N | status`.

Serving (phase 2):

- **Transport.** ClickHouse HTTP at `http://signoz-clickhouse.orb.local:8123`, the
  address OrbStack gives the container on this Mac only. No port is published and the
  SigNoz stack is untouched. A call costs about 20 ms, against about 400 ms for
  `docker exec`. Statements run with `readonly=2`, and the window and harness are bound
  as query parameters.
- **Snapshots.** Each fact view has a `<view>_snapshot` table. A non-append
  refreshable view rebuilds it every minute, `DEPENDS ON` both minute loaders. The
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

| Field | Meaning |
| --- | --- |
| `x.command_head`, `x.command_sub` | First command token (env assignments skipped) and a lowercase subcommand, e.g. `git` `commit`. |
| `x.gate_bypass` | `1` when the command contains `--no-verify`, `HUSKY=0`, `SKIP=`, or `--no-gpg-sign`. |
| `x.targets` | Files from `apply_patch` headers and `path` arguments, with the home directory shown as `~`. |
| `x.workdir` | Working directory, with the home directory shown as `~`. |
| `x.exit_code` | Parsed from `Process exited with code N` / `Exit code: N`. Codex reports `success=true` for non-zero exits. |
| `x.failure_signature` | For failed calls: the first error-like output line, with digits and hex runs replaced by `N`, capped at 160 characters. |
| `x.arguments_hash`, `x.arguments_length`, `x.output_length` | For repeat detection and size. |

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

| Capability | omp | Codex | Claude Code |
| --- | --- | --- | --- |
| Session identity | ✅ `gen_ai.conversation.id` | ✅ logs `conversation.id`; turns `thread.id` | ✅ `session.id` |
| Tokens and cache | ✅ incl. reasoning and cache creation | ✅ incl. reasoning; cache writes always 0 | ✅ no reasoning |
| Reasoning effort | ✅ | ✅ | ◐ `effort`, rarely set |
| Model call latency | ✅ span duration, first chunk | ✅ `ttft_ms`, turn/sampling spans | ✅ `ttft_ms`, `duration_ms` |
| Call errors | ✅ span status | ◐ `/responses` attempts only | ✅ HTTP `status_code`, `attempt` |
| Logical request / retry identity | ✗ | ◐ `try_run_` vs `run_sampling_request` | ◐ `attempt` |
| Tool outcome | ✅ ok/error/aborted | ✅ via `x.exit_code` + `success` | ✅ `success`, `error_type` |
| Command shape | ◐ intent text | ✅ normalized `x.*` | ◐ `bash_argv0`, class |
| Sandbox / approvals | ✗ | ✅ `tool_decision`, `sandbox_outcome` | ◐ `tool_decision` |
| User interrupt / steer | ◐ aborted status | ✅ `turn/interrupt`, `turn/steer` | ✗ (prompts only) |
| Task boundary | ✅ root `invoke_agent` | ✅ `session_task.turn` | ✅ `claude_code.interaction` with model calls |
| Explicit task success | ✗ | ✗ | ✗ |
| Project / cwd | ✗ | ◐ `cwd` on sampling spans | ✗ |

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

   | Registry says | Data shows | Flag |
   | --- | --- | --- |
   | emitted | rows present | healthy |
   | emitted | no rows | possible producer or loader break |
   | not emitted | rows present | registry is stale, or the rows are mislabelled |

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

| # | View | Questions (v2 IDs) | Data |
| --- | --- | --- | --- |
| V1 | Pipeline | Is the loader fresh and complete? Freshness per harness, refresh status, rows, parity, and sanitization checks. Replaces P1–P12. | ✅ |
| V2 | Cache efficiency | Token consumption, cache utilization, uncached input by harness/model/session (M2). | ✅ |
| V3 | Reasoning effort | Does heavier effort earn its cost? | ✅ |
| V4 | Tool failures | Failure rate, failure signatures, tasks affected, repeats and loops (M5, M7, M9). | ✅ |
| V5 | Friction | Interrupts, steers, quick follow-ups, clean completion (M3, M4); recovery after failure (M6, inferred). | ◐ |
| V6 | Guardrails | Sandbox denials, approvals, gate bypass, command churn (M8, M10, M11). | ◐ mainly Codex |
| V7 | Provider | Latency/TTFT, call errors, streaming disconnects, token throughput, unknown outcomes, model conformance (R1–R8, per call). | ◐ |
| V8 | Recurrence | Files and signatures recurring across tasks, actionable repeats on a 7-day Europe/London window, project concentration (M13, M14, M16). | ✅ / ◐ project |
| V9 | Interventions | Rule adherence, practice recurrence, post-intervention comparison, tier audit (M12, M15, M17, M18). | Needs rule and intervention records |

## Phases

| # | Phase | Exit condition | State |
| --- | --- | --- | --- |
| 0 | Archive the proof-gated work | Commit and tag `archive/proof-gated-dashboard` | Done 2026-09-28 |
| 1 | ClickHouse facts: tables, loaders, views, backfill | Parity and invariants pass; loaders refresh every minute | Done 2026-09-28 |
| 2 | Signal support registry loaded by `facts install`, with a draft entry for every signal in V1–V9; `task_outcomes` records the friction components each row could observe. Serving: Bun queries `introspection` directly; harness selector and registry-driven info notes as shared components; V1 Pipeline view with the coverage grid; remove the Python-per-request path and the registry dependency on the proof register | Coverage grid has no unexplained cells; 90-day views < 1 s; data < 2 min old | Done 2026-09-28: 0 unexplained cells over 90 days (201 healthy, 38 not emitted, 3 no events, 3 explained strays); Pipeline 90-day 0.36 s warm (first request after a server start ≈ 1.1 s), other views 0.13–0.22 s; fact lag 0–10 s |
| 3 | V2 Cache efficiency, V3 Reasoning effort, V4 Tool failures | Views show real data for every harness that emits each signal; registry-driven info notes; per-harness values recombine to All | Done 2026-09-28: all five harnesses show data; input, cached, output, sessions, reasoning, tasks, clean, tool calls, failures, and failed tasks reconcile exactly with direct view queries per harness and for All (90 days); 0.13–0.22 s per view |
| 4 | V5 Friction, V6 Guardrails | Same | Next |
| 5 | V7 Provider | Same | |
| 6 | V8 Recurrence, then V9 Interventions | Same | |
| 7 | Retire the old pipeline: remove the launchd scan schedule, outbox, read-back, and `agent_introspection` ClickHouse store; delete the `pipeline_*` and proof-experiment code; archive the 6.3 GB ledger; write Measures v3 as the view catalog | One pipeline and one set of docs | |

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

## Operations

- **Retention:** SigNoz keeps 90 days of raw traces and logs. The `introspection`
  tables have no TTL and hold history beyond that. Trace history before
  2026-09-10 had already expired under the earlier 15-day TTL and cannot be recovered.
- **Rebuild:** `facts install` recreates loaders and views without touching data.
  `facts backfill --days 90` rebuilds or re-projects everything still in SigNoz,
  so a projection change applies to the full 90-day window.
- **Backups:** the `introspection` tables live in the SigNoz ClickHouse volume
  under `/Volumes/UGreen-External/Docker`. Only history older than 90 days is
  irreplaceable. Back it up before any SigNoz upgrade or stack recreation once the
  store holds more than 90 days.
