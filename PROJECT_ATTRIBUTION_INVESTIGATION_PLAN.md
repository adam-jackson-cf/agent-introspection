# Project Attribution Failure Investigation Plan

## Objective

Establish why canonical activities still fail project attribution after the canonical ingestion cutover, separate data defects from dashboard-query defects, and produce an evidence-backed remediation decision. The investigation is complete only when every unresolved activity in a frozen population has one classified cause and the temporal attribution contract has been checked at source-membership granularity.

This plan investigates the active pipeline. It does not restore direct trace-CWD attribution, infer identity from prompts or paths, add producer-specific project resolvers, or introduce compatibility behavior. The canonical contract remains: a validated native lifecycle record establishes a half-open project-context interval, and each source event is attributed by canonical producer, native correlation identifier, and source-event time.

## Observed baseline

The earlier 24-hour inspection suggested 17 canonical activities, 4 resolved activities, 13 `missing_workspace` activities, and only one producer-namespaced Codex CLI identifier shared between activity-bearing and accepted-context sets. It also suggested two resolved activities whose stored source extrema crossed their accepted context start. These population claims are provisional hypotheses: Phase 1 must re-establish them from an exact UTC range, database snapshot, remote cutoff, retained queries, namespaced identifier manifests, and checksums before they are used as denominators.

Two code-contract observations motivate focused tests. The late-context path in `scan.py` calls `reconcile_activity` with `activity.source_ended_at_ns`, which can select an interval using an aggregate end time. `_bounds` reads `source_watermarks`, while the canonical persistence path does not visibly advance that watermark. Neither observation is accepted as the root cause until controlled experiments establish its runtime effect.

## Scope and evidence rules

The investigation covers:

- installed Claude Code, Codex CLI, Codex app, Codex app-server, and OMP lifecycle surfaces;
- managed runtime configuration and the canonical session-context inbox;
- `session_context_events`, `session_context_intervals`, `canonical_rejections`, `canonical_activities`, and `canonical_activity_versions`;
- raw SigNoz producer logs and traces used by `LOG_QUERY` and `TRACE_QUERY`;
- activity source membership, authoritative detector-event timestamps, provenance-only identifiers, and source-time partitioning;
- late-context reconciliation and monotonic version insertion;
- source range and scheduler behavior where it can omit, duplicate, or delay evidence;
- the persisted Agent Introspection dashboard identity, SQL, latest-version selection, source-time predicate, and returned stable-ID population.

Evidence must use bounded time ranges and presence, count, identifier, timestamp, and project-tuple fields only. Never capture prompt text, response text, command output, environment values, or arbitrary payloads. Every comparison must record its cutoff, query, denominator, and immutable identifier set.

## Ownership and decision authority

One investigation operator owns the cutoff, probe schedule, evidence index, and cause ledger. Before work starts, record accountable owners for: local ledger backup and SQLite evidence; installed producer configuration and native probes; raw and canonical SigNoz queries; dashboard SQL validation; scan-range disposition; security review of retained evidence; and final remediation approval. Each gate names its evidence, decision owner, decision, and handoff artifact. Destructive remote correction remains outside this investigation and requires separate explicit approval.

## Investigation questions

1. Did each activity-bearing producer session invoke the managed lifecycle adapter?
2. Did the adapter receive the same native identifier emitted by source telemetry?
3. Was the lifecycle envelope accepted, rejected, quarantined, left pending, or never created?
4. Does exactly one half-open context interval contain each source member timestamp?
5. Can a canonical activity contain source members from different attribution intervals?
6. Does late context produce one higher version without changing `activity.id` or source membership?
7. Are apparent coverage gaps actually sessions with telemetry but no detector output?
8. Does repeated full-range scanning alter attribution outcomes or merely create load and delay?

## Work plan

### Phase 1 — Freeze and reconcile the current population

1. Record exact UTC source-time bounds and cutoff. Back up the local SQLite ledger using the established workflow and record local database, inbox, installed-runtime, and remote snapshot identities.
2. Export stable, redacted inventories for activity bases and all versions, context events and intervals, rejection occurrence evidence, inbox files, canonical outbox evidence, and outbox rows partitioned by delivery status.
3. Retain producer-namespaced immutable identifier manifests, the allowlisted queries that created them, row counts, and checksums. Approve this frozen population before causal classification.
4. Define three explicit canonical event populations: locally required activity-version events, corresponding outbox rows partitioned as pending/failed/delivered, and remote rows selected by the canonical source-event timestamp domain.
5. Reconcile event ID, activity ID, version, event name, payload schema version, source-event timestamp, attribution tuple, project tuple, and an allowlisted payload checksum. Classify pending, failed, missing, duplicate, and mismatched rows instead of treating unequal sets as inherently incomparable.
6. Produce one row per activity containing producer, surface, correlation ID, source membership, source extrema, all attribution versions, outbox/remote state, matching interval candidates, and a provisional data-versus-query classification.

Gate 1 approves the reproducible frozen population. Gate 2 approves local/outbox/remote comparability. Inaccessible evidence or an unbounded population produces a blocked result with affected IDs, attempted acquisition, owner, and unblock action; it is not completion.

Deliverable: a frozen attribution ledger with exact denominators, tuple-level remote reconciliation, and probe-exclusion cutoff.

### Phase 2 — Trace lifecycle evidence by producer

For every activity-bearing correlation identifier, trace the lifecycle path in order:

1. Native producer lifecycle invocation.
2. Thin adapter normalization.
3. Shared runtime validation and Git project resolution.
4. Inbox spool identity.
5. Ordered replay into `session_context_events` and `session_context_intervals`.
6. Rejection or quarantine when acceptance did not occur.
7. OTLP acceptance event and remote delivery identity.

Classify each missing context as one of: producer capability absent, installed hook absent or stale, hook not invoked, malformed native envelope, producer/native-ID mismatch, non-Git workspace, ordered replay rejection, inbox delivery failure, or remote delivery-only failure. “No matching context” is an observation, not a root-cause category.

Before live probes, prove isolation from the frozen cohort: keep the scheduler state explicit, select test Git roots, record every probe identifier, ensure probe source times are after the frozen cutoff, and partition all local and remote queries by exact allowlisted IDs. Probe evidence is durable and must not be deleted after the run. Abort if baseline and probe populations cannot be separated.

Run the surface-specific matrix through documented native lifecycle mechanisms. The matrix covers Codex CLI, Codex app, Codex app-server, OMP, and Claude Code; records installed version, native lifecycle field, local artifact field, OTEL service/field, and lifecycle/source/detector capability; and marks fresh, resume, concurrent-project, workspace-change, end, fork, and non-Git scenarios as required, conditional, or not applicable. Prove Codex app/app-server shared-namespace collision safety. Claude Code hook equality is assessed without requiring a canonical activity while its source ingestion remains unsupported. Unsupported capability requires retained failed live-proof evidence; inability to run or observe a probe is blocked.

### Phase 3 — Verify source correlation semantics

1. Recompute producer/session sets directly from raw SigNoz logs and traces using the extraction rules in `source.py`.
2. Separate lifecycle-capable, source-capable, detector-capable, telemetry-bearing, and activity-bearing surfaces; detector yield must not be treated as ingestion coverage.
3. Count missing, single, and conflicting native identifiers by canonical producer and surface.
4. Verify Codex CLI service aliases, Codex app/app-server shared thread namespace, and OMP conversation fields against retained producer proofs.
5. Record Claude Code as lifecycle-only under the current source contract unless a separately approved capability change is made. Cover native fork behavior and the conditional availability of workspace-change and end lifecycle signals.
6. Attribute each canonical rejection to exact raw source evidence and confirm repeated scans update bounded occurrence evidence rather than create new identities.

Gate 3 approves the complete per-surface capability matrix and native identifier equality results. A surface is “unsupported” only when its installed-version live identity proof fails with retained evidence.

Deliverable: a producer capability and identifier matrix tied to installed versions, conditional scenarios, and isolated probe evidence.

### Phase 4 — Audit temporal attribution integrity

1. Define the authoritative temporal unit before checking containment. Detector source-event IDs carry attribution time: a log detector event uses its retained log timestamp, and a trace episode uses the canonical trace episode timestamp. Log and span IDs included only as source provenance do not independently determine attribution.
2. For each temporal source-event ID, define the exact bounded raw SigNoz lookup, timestamp field, identity cardinality, and expected join to retained canonical membership. Record timestamp provenance and lookup cardinality.
3. Run a retention gate. If any authoritative event cannot be recovered, mark the affected activity temporally unverified and block completion until the evidence is acquired or the canonical contract confirms that the missing identifier is provenance-only.
4. Resolve every authoritative detector event independently against half-open intervals. Fail any activity whose events resolve to different projects, mixed resolved/unresolved states, or different evidence intervals.
5. Trace failures through `_partition_observations_by_context`, `_canonical_activity`, `resolve_attribution`, `_reconcile_late_context`, and `reconcile_activity`.
6. Reproduce an observation whose valid source events fall on opposite sides of a workspace transition. The only valid oracle is deterministic partitioning into canonical activities whose complete authoritative membership falls within one interval. Rejection is allowed only for an individual malformed, ambiguously correlated, or unsupported source event.
7. Verify exact-start inclusion, exact-end exclusion, ordered lifecycle replay, clock-skew bounds, resume, workspace transition, duplicate delivery, and transaction rollback.
8. Approve the stable-ID consequences of partitioning and prove source-event denominator preservation before recommending reconstruction of existing rows.

Gate 4 approves the authoritative temporal-member model. Gate 5 approves event-granularity partitioning, stable-ID effects, and denominator conservation.

Deliverable: a timestamp-provenance manifest, source-event containment report, and behavioral reproduction for every temporal defect.

### Phase 5 — Diagnose scan-range effects

Run a controlled matrix with frozen bounds: identical repeat; adjacent windows with boundary rows; late source arrival; late context arrival; partial or deadline failure; and separate log-versus-trace time semantics. For every run record the exact query bounds, returned raw IDs, activity and version IDs, rejection occurrences, recomputation rows, outbox status and latency, and watermark before and after.

The required invariants are no boundary omission, immutable stable identity across replay, idempotent repeat results, explicit late-arrival handling, atomic failure behavior, and no inflation of rejection or recomputation identities. Gate 6 records the causal verdict before any cursor or scheduler change is proposed. Cursor remediation remains a separate decision because it must preserve late source and context semantics.

### Phase 6 — Separate canonical data defects from dashboard-query defects

After the local/outbox/remote population is reconciled, capture the deployed Agent Introspection route ID, nested UUID, revision, panel SQL, selected source-time range, latest-version rule, and deduplication rule. Independently compute the expected stable activity-ID and project-tuple population for that identical range, including per-producer denominators and `eligible = attributed + unresolved`.

Execute the persisted SQL and require exact stable-ID, attribution tuple, project tuple, and denominator equality. Classify each discrepancy as storage, delivery, query selection, aggregation, or rendering. Browser checks prove range interaction and rendering only; they are not the data oracle.

Gate 7 approves the data-versus-query verdict. Deliverable: an independent expected-set manifest and dashboard SQL comparison.

### Phase 7 — Root-cause decision and remediation handoff

Create a cause table with one row per failed activity and columns for retained evidence, responsible boundary, data-versus-query classification, reproducibility, blast radius, accountable owner, and required change. Group remediation only where activities share the same mechanism. For each proposed change, identify exact files, behavioral contracts, migration implications, remote replay requirements, and rollback or stop conditions.

Gate 8 requires the final decision owner to approve the cause ledger and mutation disposition. The decision must state whether existing canonical rows require bounded reconstruction, higher attribution versions, valid source-event partitioning, canonical rejection, or no mutation. Never rewrite immutable activity bases or reuse an event ID for different payload content.

## Likely code and test surfaces

- `.agents/skills/introspection-onboarding/scripts/session-context-runtime.sh`
- `.agents/skills/introspection-onboarding/scripts/adapters/*.py` and `omp.ts`
- `src/agent_introspection/source.py`
- `src/agent_introspection/session_context.py`
- `src/agent_introspection/attribution.py`
- `src/agent_introspection/scan.py`
- `src/agent_introspection/database.py`
- `src/agent_introspection/telemetry.py`
- `src/agent_introspection/scheduler.py`
- `src/agent_introspection/dashboard.py`
- `src/agent_introspection/assets/agent-introspection.json`
- producer adapter, source, session-context, attribution, scan, telemetry, migration, scheduler, and dashboard tests

## Acceptance criteria

- Every frozen unresolved activity has one evidence-backed cause; no unknown or evidence-unavailable row enters a complete verdict.
- Raw telemetry sessions, accepted lifecycle sessions, activity-bearing sessions, and matched sessions have separate producer-namespaced exact denominators.
- The full surface matrix records lifecycle, source, and detector capability independently, including Codex app/app-server namespace safety and Claude Code conditional scenarios.
- Every resolved activity’s authoritative detector-event membership is recoverable and contained by exactly one accepted interval and one project.
- Valid events across a workspace transition are deterministically partitioned with denominator preservation; no activity is promoted using only its end timestamp.
- An unresolved-to-resolved late-context transition inserts exactly version N+1 for the same `activity.id`, one deterministic outbox event, and one schedule row per aggregate kind in one transaction; identical replay adds zero versions, outbox rows, or schedules; failure commits none.
- Local required events, outbox rows by status, and remote rows reconcile by immutable identity, version, timestamp, attribution tuple, project tuple, checksum, occurrence evidence, and duplicate count.
- Controlled scan experiments establish boundary, replay, late-arrival, failure, and watermark behavior.
- Persisted dashboard SQL returns the exact independently computed stable-ID and project-tuple set, and `eligible = attributed + unresolved`.
- Completion produces an approved, file-specific remediation handoff. Unsupported capability is accepted only with retained failed live-proof evidence.

## Terminal states and stop conditions

`Complete` requires every acceptance criterion and all eight gates. `Blocked` is not acceptance: record affected IDs, missing evidence, acquisition attempts, accountable owner, and unblock action. An unsupported producer capability is a classified result only after its installed-version native proof fails with retained evidence; an unavailable probe remains blocked.

Stop before mutation if the bounded population cannot be established, authoritative source-event timestamps are inaccessible, a proposed repair would rewrite immutable evidence, or bounded replay cannot be distinguished from duplicate delivery. Any destructive remote correction requires a separate inventory, backup, exact predicate, dry-run count, and explicit approval.