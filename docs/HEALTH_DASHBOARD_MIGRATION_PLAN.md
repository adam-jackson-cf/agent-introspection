# Agent Introspection Health Dashboard Migration Plan

## Objective

Move ingestion, correlation, attribution, reconciliation, and scan diagnostics from Agent Introspection to Agent Introspection Health without changing either persisted dashboard identity. Repair misleading labels, incorrect session-set arithmetic, and duplicate scan rows during the move. The resulting health dashboard must answer whether producer evidence arrived, correlated, attributed, reconciled, and completed on time; it must not present detector output as product insight.

This is a clean cutover. A panel exists on one dashboard only, no temporary duplicate remains, and no compatibility query preserves the current interpretation. Deployment is coordinated with `AGENT_INTROSPECTION_DASHBOARD_REWORK_PLAN.md`: health panels may be implemented and verified independently, but removal from the insight dashboard and deployment occur only when the replacement insight panel set is ready in the same identity-preserving release.

## Current state and target classification

Agent Introspection currently owns five operational panels:

| Current panel | Target health responsibility | Required correction |
| --- | --- | --- |
| Activity coverage | Canonical activity attribution coverage | Keep activity denominator explicit; do not call it producer telemetry coverage. |
| Attribution diagnostics | Attribution method and rejection outcomes | Preserve latest-version and source-time semantics; show producer and surface. |
| Session context coverage | Producer lifecycle correlation coverage | Replace activity-versus-context proxy with raw telemetry and lifecycle cohorts; remove empty-string sentinel counts. |
| Context-to-telemetry delay | Lifecycle correlation delay | Measure against the first authoritative raw source event, not canonical activity emission. |
| Late-context reconciliations | Materialized reconciliation transitions | Deduplicate immutable activity-version events and count only exact unresolved-to-resolved version transitions. |

Agent Introspection Health already owns Pipeline health and Recent scan runs. Recent scan runs currently renders duplicate OTLP deliveries as duplicate scans, and the latest observed scheduled scan exceeded its deadline while rereading an expanding source range. The migration must make workload, lag, and failure evidence visible rather than treating a rendered table as proof that ingestion is healthy.

## Dashboard identity and release constraints

Preserve:

- Agent Introspection Health route ID `019f7fb0-6f30-77e0-ad12-6d2e44964a7d`;
- nested UUID `0500ebd3-0d77-4294-b2b5-352ba884daa7`;
- Agent Introspection route ID `019f4da0-4a13-7c62-9ac9-fc6d850d633b`;
- nested UUID `576f5068-d183-5cab-88b7-395f65cf1094`;
- dashboard schema version and locked state unless a separately justified schema migration is required.

Back up both deployed documents immediately before update and record route ID, nested UUID, remote revision metadata when present, panel IDs, panel count, lock state, and normalized `.data.data` checksum. The supported local operation is `GET` and `PUT /api/v1/dashboards/{route_id}` with the raw dashboard document as the PUT body; lock restoration uses `PUT /api/v1/dashboards/{route_id}/lock`. Never create or import a replacement dashboard. Stop if route identity and nested document UUID cannot be distinguished, loopback authentication is not already proven, or the response cannot prove which route changed.

## Canonical query semantics

Every panel must declare its cohort and timestamp domain. The common display range is a source-event range, but context evidence used to classify a source session may have started before that range. Queries must therefore use directional cohorts:

- **Source cohort:** raw producer sessions with an authoritative source event inside the selected range. Look up matching accepted lifecycle evidence by producer/session ID without incorrectly requiring the lifecycle start event to fall inside the same display range.
- **Lifecycle cohort:** accepted lifecycle sessions whose lifecycle event occurred inside the selected range. Determine whether each has raw telemetry independently; absence of a detector finding is not absence of telemetry.
- **Activity cohort:** latest canonical activity versions whose canonical source-event timestamp is inside the selected range.
- **Scan cohort:** one logical pipeline snapshot per immutable scan/event identity inside the selected range, regardless of duplicate remote deliveries.

Missing ClickHouse map keys must be normalized with `nullIf(value, '')` or filtered before aggregation. Never rely on `coalesce` to replace empty strings. `uniqExact` and `count` must exclude empty identities explicitly. Producer and session joins are namespaced tuples; a bare session ID is never sufficient.

### Normative producer and lifecycle cohorts

| Canonical row | Raw source | Service names | Native session ID | Source timestamp | Lifecycle producer/surface | Capability |
| --- | --- | --- | --- | --- | --- | --- |
| Codex CLI | `signoz_traces.distributed_signoz_index_v3` | `codex_exec`, `codex_cli_rs` | sole non-empty `thread.id` or `thread_id` per trace, then producer/ID session grouping | first trace timestamp for the valid producer/ID tuple | `codex-cli` / `codex-cli` | lifecycle, trace, and Codex log detectors |
| Codex app and app-server | `signoz_traces.distributed_signoz_index_v3` | `codex-app-server` | sole non-empty `thread.id` or `thread_id`, with app/app-server namespace safety proven | first trace timestamp for the valid producer/ID tuple | `codex-app-server` / `codex-app-server` | lifecycle, trace, and Codex log detectors |
| OMP | `signoz_traces.distributed_signoz_index_v3` | `oh-my-pi` | sole non-empty `gen_ai.conversation.id` per trace, then producer/ID session grouping | first trace timestamp for the valid producer/ID tuple | `omp` / `omp` | lifecycle and trace/token episodes; no Codex log detector surface |
| Claude Code | none in the current source contract | none | not applicable | not applicable | `claude-code` / `lifecycle-only` | lifecycle only; source and detector coverage render `Not applicable` |

The raw session denominator is trace-based because the canonical correlation ID is established there; Codex logs remain detector evidence and do not create a second session cohort. Traces with zero or conflicting native IDs are integrity/rejection cohorts, never source sessions.

Reconstruct accepted lifecycle intervals by producer/session from ordered `session_start`, `workspace_changed`, and `session_end` events, using `(timestamp, event.id)` ordering and half-open `start <= source_time < end` membership. `workspace_changed` closes one interval and opens the next; `session_end` closes the active interval; an unclosed interval remains open. Future context, ended context, and a different project interval do not match. Multiple accepted events never multiply a session row.

For source coverage, select source sessions by first raw source event in the display range and look up the interval containing that source time even when its start predates the range. For lifecycle coverage, select distinct sessions with an accepted lifecycle event in the range and report whether a valid raw source event in the same display range falls inside one of their intervals. Percentages with no applicable denominator render `Not applicable`; applicable zero denominators render an explicit no-data state. Delay uses the containing interval start and first source time, groups by producer and the approved dashboard bucket, reports sample count and p50/p95, and places negative values in a separate clock-skew count rather than the latency aggregates.

## Required operational telemetry contract

The current pipeline snapshot does not remotely expose all target fields and timestamps the event with the scan’s extraction upper bound rather than completion time. Before dashboard SQL changes, cut over `introspection.pipeline.snapshot` to one canonical projection carrying `pipeline.payload_schema_version = 2`, `scan.started_at_ns`, `scan.completed_at_ns`, `scan.duration_ms`, `scan.terminal_status`, `pipeline.error_class` when present, `rows.processed`, `logs.row_count`, `traces.row_count`, `hydration.row_count`, `session_context.event_count`, `canonical_activity.count`, `outbox.pending_after_drain_excluding_terminal_event`, `outbox.failed_after_drain_excluding_terminal_event`, and existing query/data/lag fields. Its OTLP timestamp is `scan.completed_at_ns`; `entity.id` remains the scan-run ID and event identity remains deterministic for that new scan.

`outbox.failed_after_drain_excluding_terminal_event` is the number of selected non-terminal events whose delivery attempt failed during that scan’s bounded final drain. Those events remain included in the pending population; this field does not create or imply a permanent failed outbox state and is not inferred from total pending.

This is a forward clean cutover: emit only the canonical pipeline snapshot for new scans, filter health panels to `pipeline.payload_schema_version = 2`, and do not coerce absent historical fields to zero or query superseded projections. Record the first remotely delivered canonical pipeline snapshot carrying that metadata as the dashboard availability boundary. No backfill is required for the initial release. Gate 1 approves the field contract; Gate 2 proves every listed field, identity, timestamp, outbox payload, and remote value before dependent panels proceed.

Rejection and quarantine telemetry is not remotely projected and is out of this release. No rejection panel appears in the final manifest. Adding one later requires a separate immutable rejection event contract and exact identity/occurrence semantics.

## Final health panel manifest

| Panel ID | Title | Type | Layout `(x,y,w,h)` | Identity decision |
| --- | --- | --- | --- | --- |
| `pipeline-health` | Pipeline health | table | `(0,0,6,4)` | Retained responsibility and ID; canonical pipeline snapshot row contract is explicit. |
| `scan-workload` | Scan workload | graph | `(6,0,6,4)` | New canonical pipeline snapshot rows-processed series; current lag remains in Pipeline health. |
| `recent-scan-runs` | Recent scan runs | table | `(0,4,12,6)` | Retained responsibility and ID; deduplicated canonical pipeline snapshot contract. |
| `activity-coverage` | Canonical activity attribution coverage | table | `(0,10,12,5)` | Retained responsibility and ID; title clarifies its existing denominator. |
| `attribution-diagnostics` | Attribution diagnostics | table | `(0,15,12,6)` | Retained responsibility and ID; added producer/surface dimensions do not change its diagnostic identity. |
| `producer-lifecycle-correlation` | Producer lifecycle correlation coverage | table | `(0,21,12,7)` | New ID because raw source/lifecycle cohorts replace activity/context proxy semantics. |
| `lifecycle-to-source-delay` | Lifecycle-to-source delay | table | `(0,28,12,5)` | New ID and table because the source/interval oracle replaces activity-event delay and latency/count units require separate columns. |
| `late-context-reconciliations` | Late-context reconciliations | table | `(0,33,12,6)` | Retained responsibility and ID; exact transition validation tightens integrity. |

The Agent Introspection manifest is defined in `AGENT_INTROSPECTION_DASHBOARD_REWORK_PLAN.md`: `behavior-summary`, `observed-behaviors-by-detector`, `behavior-mix`, `behaviors-by-project`, and `recent-observed-behaviors`, with exact layouts and the existing insight asset. The joint release gate requires both manifests to be approved and disjoint.

### 1. Pipeline health

Select the latest completed canonical pipeline snapshot carrying `pipeline.payload_schema_version = 2`, deduplicated by non-empty `event.id` before `argMax`. Show pipeline state, completed time, duration, error class, rows processed, raw log and trace counts, and pending/failed outbox counts. Separate local service reachability from pipeline success: a healthy SigNoz endpoint does not override a failed scan.

### 2. Recent scan runs

Render one row per canonical pipeline snapshot carrying `pipeline.payload_schema_version = 2`. Deduplicate by non-empty `event.id` only after asserting that repeated rows agree on entity ID, payload schema, timestamps, status, counts, and error fields; divergent duplicates fail the query oracle and deployment gate. Show started/completed time, duration, outcome, error class, rows processed, logs, traces, context events, and canonical activities. The independent oracle is the local `scan_runs` cohort plus its canonical snapshot outbox event reconciled to remote event IDs and allowlisted payload checksums.

### 3. Scan workload

Plot canonical pipeline snapshot `rows.processed` by completion time with one numeric `value` series and the approved dashboard time bucket. Pipeline health retains current log/trace lag state and values. The graph must reveal an expanding replay population without mixing incompatible row, duration, and lag units on one axis. Cursor remediation remains outside this dashboard plan.

### 4. Canonical activity attribution coverage

Move Activity coverage as `activity-coverage` and title it Canonical activity attribution coverage. Group latest canonical activity versions by producer and surface. Show attributed, unresolved, eligible, and attribution percentage. Enforce `eligible = attributed + unresolved`, exact stable activity-ID deduplication, non-empty producer/surface fields, and source-event range selection.

### 5. Attribution diagnostics

Move Attribution diagnostics. Group by producer, surface, attribution method, state, and rejection reason. A resolved activity displays no rejection reason; an unresolved activity must retain its canonical reason. The panel is an activity-pipeline diagnostic, not a producer capability score.

### 6. Producer lifecycle correlation coverage

Replace Session context coverage with `producer-lifecycle-correlation` and the normative cohorts above. Report by canonical producer and surface:

- raw source sessions in range;
- source sessions with accepted containing context;
- source sessions without containing context;
- accepted lifecycle sessions in range;
- lifecycle sessions with valid raw source telemetry in range;
- lifecycle sessions without valid raw source telemetry in range;
- activity-bearing sessions as a separate detector-yield column, never as the source denominator.

Seed output from the approved static capability matrix so lifecycle-only Claude Code renders `Not applicable` for source metrics even when no raw row exists. Empty IDs, missing/conflicting trace correlations, future context, and ended intervals cannot enter matched counts.

### 7. Lifecycle-to-source delay

Replace Context-to-telemetry delay with `lifecycle-to-source-delay` using the normative interval and first-source-time rules. Render one table row per producer and approved time bucket with `P50 delay (ms)`, `P95 delay (ms)`, `Matched sessions`, and `Clock-skew sessions`. Delay columns contain non-negative durations only; count columns are labeled integers and never share a graph axis with latency. Context that starts before the display range remains eligible when it contains a selected source event. Applicable zero samples render no latency percentile rather than numeric zero; unsupported capability renders `Not applicable`.

### 8. Late-context reconciliation outcomes

Move Late-context reconciliations. A reconciliation is a resolved version `n` whose immediate predecessor `n-1` for the same activity is unresolved; version gaps or divergent duplicate activity/version events are integrity failures. Count each non-empty canonical event ID once after asserting duplicate field equality. Current remote evidence does not expose idempotent no-op or failed reconciliation outcomes, so the panel claims only materialized unresolved-to-resolved transitions. Adding other outcomes requires a separate immutable telemetry contract.

## Implementation phases

### Phase 1 — Approve remote data and frozen oracles

1. Enumerate the canonical pipeline snapshot contract, field types, semantic timestamps, immutable identity, `pipeline.payload_schema_version = 2` metadata, forward-only availability boundary, and behavioral scenarios before implementation.
2. Record exact UTC ranges with non-empty activity, lifecycle, raw source, current scan, and transition populations.
3. Write the redacted joint live oracle to `~/.local/share/agent-introspection/backups/dashboard-query-oracle-<cutoff>.json`, recording UTC bounds, SQLite snapshot path/checksum, remote export checksums, capability matrix, immutable IDs, and exact typed expected rows for all thirteen panels.
4. Add deterministic executable cases to `tests/fixtures/dashboard_health_query_cases.json` for empty IDs, identical and divergent duplicates, context before range, future/ended context, workspace transition, unsupported and zero denominators, clock skew, version gaps, and exact reconciliation.
5. Capture current deployed SQL and reproduce the empty-string and duplicate-row defects with bounded evidence.

Gate 1 approves the telemetry contract, rejection-panel exclusion, producer/surface capability matrix, interval predicate, timestamp domains, deduplication keys, exact expected rows, and graph bucket expression before implementation.

### Phase 2 — Cut over operational telemetry

Add the canonical pipeline snapshot fields in `scan.py`/`telemetry.py`, preserve deterministic scan-run identity for new events, persist started/completed time correctly, and include the exact local detail counts in the remote projection. Compute excluding-terminal pending and failed-attempt values after the bounded non-terminal drain, construct and enqueue the immutable snapshot, then deliver that exact terminal event through a bounded explicit-event-ID delivery operation. If delivery fails, the terminal event remains pending and Gate 2 stays closed until its event ID and allowlisted payload checksum reconcile remotely. Expand scan and telemetry tests for success, selected-zero, backoff, delivery failure, no-data, deadline failure, pending/failed overlap, payload immutability, remote equality, and the availability boundary.

### Phase 3 — Refactor dashboard definitions and verifiers

1. Replace the universal query verifier with strict per-panel contracts for source table, service predicate, timestamp domain, schema/event predicate, non-empty identities, deduplication key, and cohort invariant.
2. Move the five operational definitions into `HEALTH_PANELS` using the exact eight-panel manifest and remove them from `INSIGHT_PANELS` as part of the coordinated builder change.
3. Implement trace-session extraction and ordered lifecycle-interval reconstruction once for correlation coverage and delay.
4. Make duplicate contract equality a precondition; never hide divergent payloads with `argMax`.
5. Validate exact IDs, titles, types, semantic ownership, and `(x,y,w,h)` coordinates; add layout non-overlap and complete ownership checks.
6. Replace stale `codex` tagging with purpose-accurate tags on both generated dashboard documents.

No panel remains in both builders. Raw trace and lifecycle queries are not forced through `COMMON_FILTER`; each verifier enforces its declared table and time predicate instead. No moved panel leaves an alias, deprecated definition, or explanatory breadcrumb.

### Phase 4 — Behavioral verification and executable SQL oracles

Deterministic fixture verification provisions a disposable isolated ClickHouse instance, creates the canonical `signoz_logs.distributed_logs_v2` and `signoz_traces.distributed_signoz_index_v3` schemas inside that instance, loads checked-in typed input rows, substitutes only the four validated dashboard time macros, executes all rendered SQL otherwise unchanged, compares exact typed rows, and destroys the instance on success, assertion failure, or interruption. It never creates, drops, or writes canonical databases on the configured live SigNoz server. Inputs and expected rows live in `tests/fixtures/dashboard_health_query_cases.json` and `tests/fixtures/dashboard_insight_query_cases.json`; their fixture schemas cover every referenced map/scalar column and all eight Health plus five Insight queries.

`./scripts/run-dashboard-query-contracts.sh` owns isolated-instance start, schema load, thirteen-query execution, comparison, and teardown. Rendered SQL may change only the four time macros; table names, event predicates, identity predicates, and semantic filters remain byte-identical.

Live verification is separate: `uv run agent-introspection dashboard verify-data --oracle <path>` requires the timestamped joint oracle under `~/.local/share/agent-introspection/backups/`, validates its presence and recorded checksums, substitutes its frozen UTC bounds, executes all thirteen queries read-only against configured SigNoz, and compares exact typed rows. Missing live evidence closes deployment but does not make deterministic pytest or repository quality gates depend on workstation files.

Tests defend:

- exact two-dashboard panel manifests, disjoint ownership, and non-overlapping layouts;
- stable nested UUIDs, schema, lock state, and generated asset equality;
- canonical pipeline snapshot field types, completion-time semantics, and endpoint-health versus pipeline-state separation;
- exact source, lifecycle, activity, scan, and reconciliation timestamp domains;
- non-empty producer/session identities and namespaced joins;
- trace-based session denominators distinct from detector activity;
- interval handling for pre-range, future, ended, transition, and repeated lifecycle events;
- explicit `Not applicable`, applicable zero, and no-data behavior;
- duplicate invariance and fail-closed divergent duplicates;
- table-typed p50/p95 latency, matched-session counts, separate negative-skew counts, and no-percentile zero-sample behavior;
- exact predecessor reconciliation and version-gap failure;
- activity denominator conservation.

Run `uv run pytest -q tests/test_dashboard.py tests/test_scan.py tests/test_telemetry.py`, `./scripts/run-dashboard-query-contracts.sh`, then `uv run ./scripts/run-ci-quality-gates.sh`. SQL token assertions may supplement but never replace the isolated thirteen-query result comparison. Before deployment, run the separate live `dashboard verify-data` command and use its exact expected values for populated, no-data, unsupported, lag, failure, and skew browser ranges.

### Phase 5 — Coordinated identity-preserving deployment

1. Require both reviewed plan manifests, builders, assets, executable query oracles, layouts, browser expectations, and raw PUT documents to pass the joint release gate.
2. `GET /api/v1/dashboards/{route_id}` for both routes into timestamped files under `~/.local/share/agent-introspection/backups/`; validate response wrappers, re-read files, and checksum normalized sorted `.data.data`.
3. Immediately before each PUT, GET again and require the normalized checksum to equal the approved pre-update backup. This is the concurrency precondition when the local API supplies no ETag; any difference aborts.
4. Assert the intended Insight raw document’s nested UUID, schema, lock state, and exact five-panel manifest, then PUT the Insight raw document to its existing route. Validate the response route ID and re-fetch structural equality before proceeding. This document replaces the five operational panels with the complete five-panel behavior manifest; the Insight dashboard is never empty.
5. Assert the intended Health raw document’s nested UUID, schema, lock state, and exact eight-panel manifest, then PUT the Health raw document to its existing route. Validate the response route ID and re-fetch structural equality.
6. If the Insight update or its post-fetch check fails, restore Insight if it changed and prove its backed-up content on the same route; Health remains untouched. If the Health update or either final post-fetch check fails, restore Health if it changed, then restore Insight, using the same route IDs and backed-up raw `.data.data` documents. Re-fetch both and prove nested UUID, lock state, panel ownership, and normalized content equality; revision progression is expected, content drift is not.
7. Relock through `/lock` if required, assert the dashboard count did not increase, assert no panel responsibility exists on both dashboards, execute all final live query oracles, and exercise the recorded browser scenarios.

The Insight route is updated and verified first. The Health route is updated only after the complete replacement Insight manifest is live. No deployment step permits duplicate operational panels. Any observed duplicate ownership is an immediate stop and paired-rollback condition. A failed second update cannot leave the first route deployed alone.

## Files and operational surfaces

- `src/agent_introspection/dashboard.py`
- `src/agent_introspection/assets/agent-introspection.json`
- `src/agent_introspection/assets/agent-introspection-health.json`
- `src/agent_introspection/source.py` for canonical producer/identifier semantics
- `src/agent_introspection/telemetry.py` and `scan.py` for the canonical operational projection
- `src/agent_introspection/cli.py` for the read-only `dashboard verify-data` command
- `tests/test_dashboard.py` and focused scan/telemetry tests
- `tests/fixtures/dashboard_health_query_cases.json`
- `tests/fixtures/dashboard_insight_query_cases.json`
- `scripts/run-dashboard-query-contracts.sh`
- local `GET`/`PUT /api/v1/dashboards/{route_id}` and `/lock` endpoints
- deployed SigNoz routes and timestamped backups under `~/.local/share/agent-introspection/backups/`

## Acceptance criteria

- Both existing route IDs and nested UUIDs are unchanged; the dashboard count does not increase.
- The Health panel set exactly matches the eight-panel manifest and the Insight set exactly matches its five-panel manifest; ownership is disjoint and layouts do not overlap.
- Canonical pipeline snapshots carrying `pipeline.payload_schema_version = 2` remotely expose every specified field with completion-time semantics; superseded snapshots are outside the explicit availability boundary rather than coerced to zero.
- Pipeline health distinguishes endpoint reachability from pipeline outcome and reports source lag; Scan workload and Recent scan runs match local scan/outbox/remote identities and counts exactly.
- Correlation coverage uses the normative trace and lifecycle interval cohorts, excludes empty/conflicting identifiers, handles pre-range/future/ended/transition context, and renders unsupported capability explicitly.
- Scan, activity, and reconciliation duplicates are invariant only when contract fields agree; divergent duplicates and version gaps fail closed.
- Activity coverage satisfies `eligible = attributed + unresolved` and exact stable-ID equality.
- Delay uses a table with p50/p95 milliseconds, matched-session and clock-skew counts in separate typed columns, and no numeric percentile for zero samples.
- Reconciliation counts only exact immediate unresolved-to-resolved transitions; rejection outcomes are explicitly out of this release.
- Every rendered query matches the frozen typed fixtures and retained live oracle through executable comparison.
- Builders, generated assets, intended raw documents, and post-fetch route content agree structurally.
- `uv run pytest -q tests/test_dashboard.py tests/test_scan.py tests/test_telemetry.py`, `./scripts/run-dashboard-query-contracts.sh`, and `uv run ./scripts/run-ci-quality-gates.sh` pass before deployment.
- All thirteen rendered queries pass `./scripts/run-dashboard-query-contracts.sh` against canonical isolated schemas, and the instance is destroyed on every exit path.
- The read-only live `dashboard verify-data` comparison passes before deployment without becoming a workstation-dependent pytest prerequisite.
- Partial-update rollback is rehearsed, Agent Introspection is never empty, no intermediate or final observed state contains duplicate ownership, and both backed-up documents are restored on their original routes after a simulated second-update failure.
- Browser verification matches recorded populated, no-data, unsupported, lag, failure, and skew values; both dashboards remain locked.

## Stop conditions

Stop if a target field is not remotely queryable, the canonical pipeline snapshot cannot preserve immutable identity, its terminal event cannot be delivered and reconciled, producer interval cohorts are ambiguous, duplicate payloads diverge, isolated cleanup fails, the frozen oracle cannot be reproduced, the insight plan is not approved, either pre-PUT checksum changes, or paired rollback cannot be proven. Do not ship a partial telemetry contract, conditional rejection panel, temporary duplicate ownership, temporary empty insight dashboard, or weakened verifier/query to make a panel populate.
