# Project Attribution Failure Investigation Plan

## Objective

Establish the remaining project-attribution defects, agree the canonical attribution contract, and only then approve a file-specific remediation plan. The work is complete when every activity in a reproducible frozen population has one evidence-backed outcome, every supported producer passes native identity and project-context probes, and source-member attribution remains correct under replay, late context, workspace transitions, and scan failure.

This plan does not restore direct trace-CWD attribution, infer project identity from prompts, paths, CWD aliases, process state, or request content, add producer-specific project resolvers, or introduce compatibility behavior. Missing evidence is `Blocked`, not proof that a producer is unsupported.

## Current evidence and remaining issues

The following results come from a read-only inspection of the current local ledger. They establish the investigation targets, but they are not Gate 1 denominators because the database and remote cutoff were not frozen for this inspection.

| ID | Current evidence | Required verdict |
|---|---|---|
| I1 | 17 canonical activities exist: 4 resolved and 13 unresolved with `missing_workspace`. | Re-establish from a frozen cutoff and classify every activity. |
| I2 | 9 activities have a producer-and-correlation-ID match in `session_context_intervals`; only 4 have an interval containing the activity end time. The 13 unresolved activities therefore divide provisionally into 8 without any matching lifecycle identity and 5 with matching identity but no containing interval. | Distinguish missing lifecycle capture from late or temporally invalid lifecycle capture. |
| I3 | 2 of the 4 resolved activities start before the interval used as attribution evidence. | Recover every authoritative source-member timestamp and determine whether these activities contain mixed pre-context and post-context members. Aggregate end-time attribution is not accepted as proof. |
| I4 | The Codex CLI adapter maps native `agent-turn-complete` notifications to `session_start`. Retained producer proof shows the first OTEL timestamp can precede the resulting context timestamp. | Decide whether an authoritative pre-source Codex lifecycle surface exists. A completion callback must not be relabelled as a start event. |
| I5 | `_partition_observations_by_context` partitions newly detected observations by source-member timestamps, but `_persist_canonical_activities` and `_reconcile_late_context` resolve stored activities using `source_ended_at_ns`. | Prove the late-context behavior with mixed-interval membership and replace aggregate endpoint resolution with source-member reconciliation. |
| I6 | The `signoz_logs` watermark remains at the original cutover point while each scan extends the end bound. Recent scans processed 546,795 and 595,635 rows and ended with `ScanDeadlineExceeded`. | Establish whether the non-advancing range delays or prevents canonical persistence, then approve separate range semantics that preserve late-source handling. |
| I7 | Existing attribution, scan, Codex-adapter, and source tests pass, including a synthetic workspace-transition split and a simple late-context version bump. They do not exercise the observed post-turn timing, late arrival across a transition, or an end-to-end supported-producer project assertion. | Add behavioral coverage that fails on the observed mechanisms rather than on the current example values. |
| I8 | Local activity versions, canonical outbox rows, and remote canonical records have not yet been reconciled at immutable event-tuple granularity for one frozen cutoff. | Separate storage defects from delivery and query defects before mutation. |

The current rejection population is not the attribution denominator. Raw traces without a canonical native correlation ID are source-capability evidence; they become attribution cases only if they produce a canonical activity. This prevents high-volume source diagnostics from obscuring the 17 activity outcomes.

## Why OpenTelemetry logs appear in an agent-trace investigation

“Logs” in this repository means structured OpenTelemetry log records emitted by the agent runtime, not application log scraping, shell history, or project inference from command text.

The two signals have different roles:

- trace spans provide the native producer correlation identity and trace-episode timing;
- selected OTEL log records provide detector events such as tool failure, repeated attempt, tool loop, and transport instability;
- project identity comes only from validated native project-context evidence;
- source members are joined to project context by canonical producer, native correlation ID, and authoritative source-member time.

All 17 current activities are detector activities whose 26 authoritative event members are log-backed. Omitting those records would remove the activities being diagnosed, not narrow their attribution. The investigation must therefore retain the minimum log fields needed to identify each canonical source member and its timestamp.

The following are outside this attribution investigation:

- redesigning detectors or deciding which tool events should become activities;
- capturing prompts, responses, command output, arbitrary tool arguments, environment values, or secrets;
- using log attributes, span CWD, targets, or request content to derive project identity;
- treating every raw trace or log rejection as a failed activity;
- dashboard layout or insight redesign.

Dashboard SQL is checked only after canonical local and remote data agree. The separate dashboard plans remain responsible for dashboard migration and presentation changes.

## Canonical contract to approve

The recommended contract is strict because it makes unsupported coverage visible instead of manufacturing project attribution:

1. **Truthful native context evidence.** A context event records the native event that actually occurred. `session_start`, `workspace_changed`, and `session_end` cannot be synthesized from a post-turn completion callback.
2. **Producer-neutral join.** A source member joins context only by canonical producer, native correlation ID, and source-member time. Project fields in traces, logs, prompts, requests, filesystem paths, process state, or aliases are never attribution inputs.
3. **Half-open intervals.** A validated start or workspace-change opens `[started_at, ended_at)`; the next workspace-change or session end closes it. Exact start is included and exact end is excluded.
4. **Source-member granularity.** Each authoritative detector event resolves independently. An aggregate endpoint is never an attribution oracle.
5. **Single-project activities.** All authoritative members of one canonical activity must resolve to one interval and one project. Members across intervals are deterministically partitioned before canonical identity is created.
6. **Late evidence replays the same rule.** Late context re-evaluates retained source members. It must not promote an existing mixed or temporally invalid activity from its end timestamp.
7. **Immutable evidence.** Existing activity bases and event payload identities are not rewritten. A valid attribution-only change creates exactly version `N+1`; changed source partitioning creates new canonical activity identities under an approved bounded reconstruction disposition.
8. **Explicit capability.** A producer is supported only if an installed-version native proof shows the lifecycle identity equals the source identity and the context event occurs early enough to cover source members. If Codex CLI exposes no authoritative pre-source lifecycle surface, its interval-based attribution is `Blocked` or unsupported; the completion callback is not backdated.
9. **Signal neutrality.** The rule is identical for trace-episode and log-detector members. Signal type determines the authoritative timestamp field, not the project-resolution algorithm.
10. **Denominator conservation.** Partitioning, replay, and reconstruction preserve the exact authoritative source-member set. No valid member is dropped or counted twice.

No remediation implementation begins until Gate 4 approves this contract and the Codex CLI lifecycle capability verdict.

## Ownership and evidence rules

One investigation operator owns the cutoff, evidence index, probe schedule, and cause ledger. Record accountable owners for local SQLite backup, installed producer probes, raw and canonical SigNoz queries, scan-range disposition, evidence-security review, bounded reconstruction, and final remediation approval.

Retained evidence is limited to allowlisted identifiers, timestamps, counts, project tuples, statuses, checksums, installed versions, and presence checks. Never retain prompts, responses, command output, arbitrary payloads, environment values, or secrets. Every query records exact UTC bounds, cutoff, denominator, immutable identifier set, and checksum.

Fresh probes occur only after the baseline cutoff is frozen. Probe IDs and source times must be isolated after that cutoff, and probe evidence is retained rather than deleted.

## Work plan

### Phase 1 — Freeze the activity population

1. Record exact UTC source bounds, local cutoff, remote ingestion cutoff, database identity, installed runtime identity, inbox identity, and scheduler state.
2. Create an online SQLite backup using the repository workflow and verify its integrity.
3. Export redacted inventories for canonical activity bases and every version, source membership, context events and intervals, rejection occurrences relevant to activity members, inbox rows, recomputation rows, and canonical outbox rows by status.
4. Export corresponding remote canonical activity-version events selected by the canonical source timestamp.
5. Retain the exact queries, producer-namespaced identifier manifests, row counts, and checksums.

**Gate 1 — Frozen population.** Approve reproducibility, exact denominators, and the probe-exclusion cutoff. An unbounded or inaccessible population is `Blocked`.

### Phase 2 — Reconcile storage, delivery, and source membership

1. Reconcile locally required activity-version events, outbox rows, and remote rows by event ID, activity ID, version, event name, payload schema version, source timestamp, attribution tuple, project tuple, checksum, status, and duplicate count.
2. Produce one row per activity with producer, surface, correlation ID, detector ID, source-member IDs, source extrema, all attribution versions, outbox and remote state, and candidate context intervals.
3. Recover the authoritative timestamp for every detector event member. A log member uses its retained log timestamp; a trace episode uses its canonical trace-episode timestamp. Provenance-only log and span IDs do not become additional attribution members.
4. Partition raw source diagnostics from activity-bearing cases. Record correlation coverage separately without adding non-activity traces to the attribution denominator.

**Gate 2 — Comparable evidence.** Approve local/outbox/remote equality or classify each difference as storage, pending delivery, failed delivery, missing remote row, duplicate, or payload mismatch.

### Phase 3 — Establish causes and producer capability

1. For each activity correlation ID, trace native lifecycle invocation, adapter normalization, shared runtime validation, Git project resolution, inbox spool, ordered replay, rejection or quarantine, and remote lifecycle delivery.
2. Classify each unresolved activity as exactly one of: no authoritative lifecycle capability, installed hook absent or stale, hook not invoked, malformed native envelope, producer/native-ID mismatch, non-Git workspace, context occurred after all source members, ordered replay rejection, inbox failure, or canonical delivery failure.
3. Run isolated installed-version probes for Codex CLI, Codex app, Codex app-server, OMP, and Claude Code. Record lifecycle capability, source capability, detector capability, native ID equality, and timing independently.
4. For Codex CLI, explicitly prove or disprove an authoritative native event that provides the source correlation ID and project context before attributable source members. `agent-turn-complete` timing is measured as completion evidence only.
5. Prove Codex app/app-server namespace safety, concurrent same-producer sessions in different Git projects, resume behavior, non-Git rejection, workspace change where exposed, and session end where exposed.

**Gate 3 — Cause ledger.** Approve one evidence-backed cause for every activity and a complete installed-surface capability matrix. Unknown or unavailable evidence remains `Blocked`.

### Phase 4 — Approve canonical behavior

1. Review the recommended canonical contract against the frozen cause ledger and installed producer capabilities.
2. Resolve the Codex CLI contradiction: select a truthful pre-source native lifecycle acquisition surface or classify interval attribution for that surface as unsupported. Do not retain the current completion-to-start mapping.
3. Resolve every source member independently against half-open intervals and compare the result with stored activity versions.
4. Identify activities with mixed projects, mixed resolved and unresolved members, no interval, or a stored attribution selected only by the aggregate end.
5. Approve stable-ID and immutable-history consequences for attribution-only versioning versus changed source partitioning.

**Gate 4 — Canonical contract.** The final decision owner approves the attribution rule, producer capability verdicts, and mutation semantics. No code change is planned against an unapproved contract.

### Phase 5 — Design the remediation

After Gate 4, produce a file-specific implementation plan grouped by proven mechanism:

1. **Lifecycle acquisition:** remove false lifecycle mappings and implement only the approved native producer surfaces in `.agents/skills/introspection-onboarding/scripts/adapters/` and `session-context-runtime.sh`.
2. **Temporal attribution:** centralize one source-member resolver in `src/agent_introspection/attribution.py`; make initial and late paths use the same result.
3. **Canonical construction:** update `src/agent_introspection/scan.py` so partitioning occurs from authoritative members before persistence and late context triggers deterministic re-evaluation rather than aggregate end-time promotion.
4. **Persistence and versioning:** update `database.py` and `telemetry.py` only where the approved immutable reconstruction or `N+1` version transaction requires it.
5. **Range processing:** separately correct `source_watermarks`, boundary semantics, and failure behavior in `scan.py`, `database.py`, and `scheduler.py` after the controlled range experiment proves the cursor contract.
6. **Bounded historical disposition:** inventory affected rows and choose no mutation, higher attribution version, deterministic reconstructed activities, or canonical rejection. Never rewrite activity bases or reuse an event ID for different content.
7. **Remote disposition:** any destructive remote correction requires a separate backup, exact predicate, dry-run count, rollback or stop condition, and explicit approval.

**Gate 5 — Remediation design.** Approve exact files, contracts, migration implications, bounded reconstruction rules, remote disposition, and rollback or stop conditions.

### Phase 6 — Approve the robust testing plan

The implementation plan must contain all of the following behavioral layers.

#### A. Contract tests

- exact-start inclusion and exact-end exclusion;
- no interval, one interval, and conflicting interval outcomes;
- producer namespace isolation for identical native IDs;
- truthful lifecycle normalization: a completion event cannot become `session_start`;
- nanosecond source ordering without float-derived boundary drift;
- identical resolution for log-detector and trace-episode source members.

#### B. Source-member and partition tests

- one activity whose members all resolve to one project;
- members on opposite sides of a workspace transition partition into separate stable activities;
- mixed resolved and unresolved members do not inherit the aggregate end-time project;
- late context produces the same partition and attribution as context present before the scan;
- exact source-member denominator conservation across partitioning;
- replay preserves stable IDs and membership checksums.

#### C. Lifecycle integration tests

- fresh, resume, concurrent projects, workspace change, end, fork, and non-Git scenarios, marked required only where the installed native surface exposes them;
- lifecycle ID equals the source telemetry ID for every supported producer;
- context evidence precedes or truthfully covers every attributed source member;
- Codex app and app-server shared thread namespaces cannot cross-attribute;
- hook absence, stale installation, malformed input, and quarantine fail closed.

#### D. Late-context transaction tests

- unresolved to resolved creates exactly version `N+1` for the same activity ID when membership is unchanged;
- exactly one deterministic outbox event and one schedule row per aggregate kind are inserted in the caller-owned transaction;
- identical replay inserts no version, outbox row, or schedule row;
- an injected failure commits none of those rows;
- changed partitioning never mutates the original activity base.

#### E. Scan-range tests

- identical repeat, adjacent windows with boundary rows, late source arrival, late context arrival, separate log and trace timestamp semantics, deadline interruption, and restart;
- no boundary omission or duplication;
- watermark advances only after atomic persistence of the corresponding source set;
- failed scans do not advance the watermark or expose partial canonical state;
- replay does not inflate rejection, activity, version, outbox, or recomputation identities.

#### F. Frozen-ledger reconstruction tests

- run the approved remediation against a redacted copy of the frozen ledger;
- compare pre- and post-state manifests by immutable source-member IDs and checksums;
- require every formerly unresolved activity to retain its classified outcome: resolved, validly partitioned, canonically rejected, unsupported, or `Blocked`;
- require immutable base rows and historical versions to remain unchanged;
- require local/outbox/remote event-tuple equality for newly created versions or activities.

#### G. Live end-to-end acceptance

For every supported producer, create isolated post-cutoff activity-bearing probes in two Git projects and one non-Git directory. Query raw source telemetry, accepted context, local canonical rows, outbox state, and remote canonical events. Require exact native ID equality, exact project tuple, one containing interval per source member, stable replay identity, and no cross-project attribution under concurrency.

The smoke test must exercise the real scheduled scan and remote SigNoz query path. Dashboard rendering is not the oracle; a persisted dashboard query may be compared only after the canonical expected set is independently established.

#### H. Quality gates

Run the focused behavioral scenarios first, then the repository’s existing `uv` test, type, lint, and pre-commit gates without suppressions or skips. A test passes only when it defends an observable contract and fails against the corresponding plausible defect.

**Gate 6 — Test design.** Approve scenario coverage, expected sets, failure injection, producer probe matrix, and reconstruction oracle before implementation.

### Phase 7 — Implement, reconstruct, and accept

1. Implement the Gate 5 plan in causal order: lifecycle evidence, shared temporal resolver, canonical partitioning and late reconciliation, then separately approved scan-range behavior.
2. Run focused tests after each contract change and the complete approved test matrix after integration.
3. Apply bounded local reconstruction only after backup, inventory, dry-run manifest, and explicit mutation approval.
4. Reconcile new local, outbox, and remote events at tuple and checksum granularity.
5. Independently compute the stable activity-ID and project-tuple set for the acceptance range. Only then compare persisted dashboard SQL to separate data correctness from query correctness.

**Gate 7 — Mutation approval.** Approve the exact bounded local and remote mutation manifests after dry-run counts and backups.

**Gate 8 — Final acceptance.** Approve the post-remediation cause ledger, supported-producer live proofs, source-member containment report, scan-range invariants, reconstruction reconciliation, and independent expected set.

## Acceptance criteria

- Every frozen activity has one evidence-backed outcome; no unknown row enters `Complete`.
- Every supported producer proves native lifecycle/source identity equality and truthful temporal coverage on its installed version.
- The Codex CLI completion callback is not represented as `session_start`.
- Every attributed authoritative source member is contained by exactly one half-open interval and one project.
- No activity is attributed from `source_ended_at_ns` alone.
- Valid members across a workspace transition are deterministically partitioned with exact denominator conservation.
- Late context yields the same canonical result as context available before detection.
- Attribution-only reconciliation creates exactly `N+1`, one outbox identity, and one schedule row per aggregate kind atomically; replay adds nothing and failure commits nothing.
- Scan repeats, boundaries, late arrivals, deadlines, and restarts preserve stable identity and atomic watermark semantics.
- Frozen-ledger reconstruction preserves immutable evidence and reconciles every new local, outbox, and remote event.
- Independent canonical expected sets, not dashboard rendering, determine data correctness.
- Completion produces approved file-specific changes, robust behavioral proof, and a cause ledger with no unsupported claim based only on missing evidence.

## Terminal states and stop conditions

`Complete` requires all eight gates and every acceptance criterion. `Blocked` records affected activity or producer IDs, missing evidence, acquisition attempts, accountable owner, and unblock action.

Stop before mutation if the population is not reproducible, any authoritative source-member timestamp is unavailable, the Codex lifecycle surface cannot satisfy the approved contract, reconstruction would rewrite immutable evidence, or bounded replay cannot be distinguished from duplicate delivery. Destructive remote correction remains a separately approved operation.
