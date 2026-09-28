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
                      └─> views: usage_events · task_outcomes · tool_calls · user_signals
                            └─> Bun server runs small aggregate queries → React views
SQLite keeps only app workflow state (findings, proposals, interventions).
```

The source is [`facts.py`](../src/agent_introspection/facts.py) and
[`facts_sql/`](../src/agent_introspection/facts_sql/). Operate it with
`agent-introspection facts install | backfill --days N | status`.

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
| Tokens and cache | ✅ incl. reasoning | ✅ incl. reasoning | ✅ no reasoning |
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

## Views

Each view answers one question, in outcome-first order: KPI tiles → daily
trends → breakdowns by harness and model → exemplar tables → drill-down to
session. Every panel carries an info note with producer boundaries.

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
| 2 | Serving: Bun queries `introspection` directly; V1 Pipeline view; remove the Python-per-request path and registry dependency on the proof register | 90-day views < 1 s; data < 2 min old | Next |
| 3 | V2 Cache efficiency, V3 Reasoning effort, V4 Tool failures | Views show real data with info notes | |
| 4 | V5 Friction, V6 Guardrails | Same | |
| 5 | V7 Provider | Same | |
| 6 | V8 Recurrence, then V9 Interventions | Same | |
| 7 | Retire the old pipeline: remove the launchd scan schedule, outbox, read-back, and `agent_introspection` ClickHouse store; delete the `pipeline_*` and proof-experiment code; archive the 6.3 GB ledger; write Measures v3 as the view catalog | One pipeline and one set of docs | |

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
