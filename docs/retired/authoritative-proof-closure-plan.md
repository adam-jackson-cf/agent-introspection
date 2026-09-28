# Authoritative Proof Closure Plan — Retired

## Retirement status

**Retired as an active execution plan — 2026-09-05.** The bounded execution
record is retained below. The
[Dashboard Metric-to-Proof Register](dashboard-metric-proof-register.md)
supersedes this plan's metric table and remaining-action queue.

Retirement does not mean authoritative proof closure: this execution qualified
E-Pipeline-5, retained historical E-Attribution-1, and left 22 experiments
`Blocked`. No Appendix row was promoted. Unfinished obligations remain explicit
in the register; they have not been completed, waived, or declared impossible.

The requirements, workstream actions, and completion conditions below describe
the historical execution scope, not a second active plan. Retained evidence
remains valid for its stated population and outcome.

- [Dashboard Measure v2](dashboard-measure-v2.md) owns normative calculations.
- [Prototype Proof Matrix](dashboard-prototype-proof-matrix.json) owns
  machine-readable Appendix row classifications.
- [Dashboard Metric-to-Proof Register](dashboard-metric-proof-register.md) owns
  current human-readable proof status, missing boundaries, and next actions.
- [React and SigNoz Companion Implementation Plan](react-signoz-companion-implementation-plan.md)
  remains active and gated; production cutover is not authorized.

Supported producers remain exactly `omp`, `codex-cli`, and
`codex-app-server`. Claude Code remains outside supported-producer denominators
until fresh evidence proves
`hook session ID = local artifact session ID = OTEL session ID`. Codex Desktop
uses canonical producer `codex-app-server`; there is no separate `codex-app`
producer.

## Non-goals

- Do not redesign Appendix A calculations, controls, timestamp domains, or
  missing-data semantics.
- Do not create producer fields, identities, outcomes, or relationships that a
  native producer is not known to emit.
- Do not infer identity from project paths, process state, temporal proximity,
  or unrelated telemetry.
- Do not implement a generic query/oracle framework. Keep direct row-level SQL,
  artifact references, parameters, and result shapes until repeated proven rows
  establish a common structure.
- Do not add production dashboard routes, panels, migrations, adapters, or
  telemetry cutover before their individual gates open.
- Do not rerun unchanged request or task field audits. Reopen an audit only when
  the installed producer contract changes.

## Synthetic evidence boundary

Synthetic inputs are allowed only after a bounded real observation proves that
the field exists and is emitted. They cannot promote a row.

Synthetic evidence may verify:

- transport and parsing;
- validation and typed rejection;
- reducer arithmetic, ordering, conservation, and idempotence;
- exact range endpoints, interval containment, and clock-skew behavior;
- immutable event delivery mechanics;
- the row's direct remote SQL and independent oracle mechanics.

Synthetic evidence cannot establish:

- field existence or producer capability;
- semantic ownership;
- that independently observed fields co-occur in one authoritative event;
- exact native identity linkage;
- the complete applicable population;
- real request, task, operation, terminal, policy, or workflow state;
- row-level `Proven` classification.

Synthetic, fixture, empty-cohort, no-data, event-presence, or local-only equality
never promotes a row. Such evidence remains mechanical coverage alongside the
required bounded real proof.

## Row-level promotion gate

A row may move to `Proven` only when every applicable supported producer has a
row-bound proof that:

1. retains bounded fresh real provenance for all authoritative fields and their
   required co-occurrence;
2. joins first on exact `(producer, native_session_id)` and uses only typed,
   authoritative, fail-closed later joins;
3. preserves the row's declared selected-range operator, interval-containment
   operator, evaluation policy, version policy, and all-version requirement;
4. conserves the complete accepted population through a deterministic shared
   reducer, including typed rejection and duplicate counts;
5. delivers immutable row-identifiable events with retained event IDs;
6. executes the row's direct remote calculation against the declared bounded
   population;
7. reconciles the exact remote result and scalar type to an independently
   implemented oracle; and
8. binds the source, reducer, delivery, remote calculation, oracle, and event
   IDs into the row's `EvidenceBundle`.

An experiment-level or workstream-level success cannot promote related rows.
Missing durable authority remains `Blocked`; contradictory evidence is
`Failed`; normative capability absence is `Not applicable` only when the
canonical capability matrix proves it.

## Execution model

Use five reducer-aligned workstreams. Share a bounded capture only where the
same authoritative facts are genuinely common; retain every experiment and
Appendix row's separate exit gate.

| Workstream | Experiments | Appendix rows | Authoritative owner | Start condition |
| --- | --- | --- | --- | --- |
| Pipeline | E-Pipeline-1–6 | A01–A06 | Application scanner, scheduler, ledger, and outbox | Start now. Application-owned boundaries are available to implement and observe. |
| Attribution | E-Attribution-1–5 | A07–A09 | Native lifecycle/source surfaces and shared session-context runtime | Start row binding and supported-producer scenarios now; reopen Claude only after its native contract changes. |
| Request/attempt | E-Request-1–3 | A10–A23 and A25 | Native producer request, attempt, and accounting surface | Wait for an installed producer contract that emits the missing authority. |
| Task/terminal outcome | E-Task-0–4 | A24 and A26–A35 | Native producer task, operation, and terminal-outcome surface | Wait for an installed producer contract that emits the missing authority. |
| Recurrence/policy | E-Recurrence-0–4 | A36–A43 | Application-owned task/activity, finding, practice, rule, proposal, and intervention projections | Follow the per-experiment dependency graph below; there is no workstream-wide task or M13/M16 prerequisite for evidence work. |

Pipeline and Attribution may execute in parallel. Request and Task do not depend
on Pipeline completion, but neither should resume without a changed native
contract. Recurrence evidence work follows each experiment's proof-matrix
dependencies; no shared workstream gate may replace them.

## Metric-to-proof register

The metric-to-proof table has moved to the
[Dashboard Metric-to-Proof Register](dashboard-metric-proof-register.md).
It covers P1–P12, R1–R8, M1–M18, all A01–A43 controls for each supported
producer, and the independent widget obligations not represented in Appendix A.
Use that register for current findings, evidence links, and reopen conditions.

### Native contract change ownership

The retained Request and Task audits remain the evidence, not an invitation to
repeat unchanged field searches. Their native owners must change as follows:

- OMP provider request execution and its retry/accounting boundary must expose
  one native session/request/attempt/accounting lineage with timing, separate
  requested/response models, tokens, terminal state, error and retry semantics.
  The OMP lifecycle extension does not own those facts.
- Codex CLI and Codex app-server native request execution/OTEL must expose the
  same co-occurring request/attempt/accounting authority. CLI `notify` and
  app-server `SessionStart`/`SessionEnd` are attribution boundaries, not provider
  request adapters.
- Each supported producer's native operation dispatcher must retain immutable
  operation identity, route, task membership, complete order, explicit outcome
  and source time. A separate native task terminal boundary must identify the
  same durable task with explicit success, failure, timeout, cancellation or
  unknown outcome. A session/thread identifier, last successful tool, response
  text, or absent error is not that boundary.
- The application may normalize observed authority and attach project identity
  only after these native contracts exist. A new reader, synthesized identifier,
  inferred terminal outcome, or adapter-generated request grouping is not the
  required contract change.

## Workstream actions

### Pipeline

Use one bounded application-owned capture where facts overlap, then preserve
separate proof gates for snapshot/detail, cadence, lag, integrity, outbox, and
maintenance.

| Experiment | Required action | Completion evidence |
| --- | --- | --- |
| E-Pipeline-1 | Persist the complete bounded scan snapshot and durable population oracle named by the retained blocker. | Real completed-scan population conservation plus direct remote/oracle equality for its owned A01, A02, and A05 rows. |
| E-Pipeline-2 | Persist authoritative schedule interval and policy identity and cover every real terminal state. | Real cadence and outcome populations plus separate A03 and A04 remote/oracle proofs. |
| E-Pipeline-3 | Bind every source observation to its scan extraction upper bound. | Multi-scan real cohorts conserve per producer/surface/signal; A06 p50, p95, `n`, and skew count reconcile remotely. |
| E-Pipeline-4 | Persist the durable integrity-failure and rejection population with redacted immutable identities. | Real integrity populations conserve; corrupt cohorts are withheld rather than partially aggregated. |
| E-Pipeline-5 | Persist creation, attempt, final-drain, delivery, status, and redacted error authority for immutable outbox events. | Bounded final-drain population reconciles locally and remotely without deleting immutable outbox evidence. |
| E-Pipeline-6 | Persist an allowlisted maintenance observation without unrestricted database access or writes. | Real maintenance-ledger population reconciles under the privacy allowlist. |

### Attribution

The retained E-Attribution-1 aggregate proof remains unchanged, but canonical
evidence has already established that it lacks the per-row `EvidenceBundle`,
`native_session_id`, `event_id_inputs`, and row-specific query/oracle evidence.
Run one bounded replacement capture for those missing row inputs; do not
re-evaluate or overwrite the historical aggregate result.

| Experiment | Required action | Completion evidence |
| --- | --- | --- |
| E-Attribution-1 | Capture replacement row-bound evidence for its six A07–A09 `omp` and `codex-cli` obligations. | Each row has exact producer/native-session lineage, deterministic event-ID inputs, direct remote/oracle equality, and its own `EvidenceBundle`; the historical aggregate proof remains unchanged. |
| E-Attribution-2 | Execute the named `codex-app-server` scenario matrix below. | Each A07–A09 `codex-app-server` row satisfies the full scenario acceptance gate and has row-specific event IDs, direct query/oracle equality, and an `EvidenceBundle`. |
| E-Attribution-3 | Keep Claude ingestion closed until the installed native contract changes. | Fresh real proof of `hook session ID = local artifact session ID = OTEL session ID`; otherwise remain `Blocked`. |
| E-Attribution-4 | Persist authoritative source-session linkage to the accepted containing lifecycle interval. | A real non-empty bounded cohort reconciles delay percentiles, `n`, matched sessions, and conserved negative-skew count. |
| E-Attribution-5 | Persist complete authoritative activity version histories and the remote ever-unresolved denominator. | Real immediate unresolved-to-resolved transitions and the complete denominator reconcile remotely. |

For each `codex-app-server` startup, resume, clear, and compact capture,
E-Attribution-2 requires:

- accepted lifecycle start and end;
- exact native identity and source correlation;
- an exact, stable project tuple;
- directional source correlation;
- concurrent-project isolation;
- canonical non-Git classification; and
- a qualifying same-project source activity when the installed surface can emit
  one.

If the surface emits only `session_task.turn` or `startup_prewarm`, the
qualifying canonical-activity path remains `Blocked`; synthetic detector or tool
activity cannot replace it.

### Request and attempt

The retained audit already found the missing and ambiguous fields. Do no new
implementation work until a supported native producer exposes an authoritative
request contract.

| Experiment | Required action after a native contract change | Completion evidence |
| --- | --- | --- |
| E-Request-1 | Run one bounded audit of the changed producer contract for request, attempt, accounting, timing, model, terminal, retry, token, and project authority. | Required fields are authoritative and co-occur under exact producer/native-session lineage for every applicable producer. |
| E-Request-2 | Reduce complete authoritative attempt and accounting sets without inventing candidates. | Accepted, rejected, attempt, accounting, and logical-request populations conserve under deterministic ordering and terminal-grace policy. |
| E-Request-3 | Emit immutable request/attempt/accounting projections and execute each owned row directly. | A10–A23 and A25 each have bounded real row-level remote/oracle equality and an `EvidenceBundle`. |

If the installed producer still does not emit logical-request, attempt,
accounting-record, or terminal authority, retain `Blocked`; synthetic records or
adapter-generated identities are not an unblock path.

### Task and terminal outcome

The retained route and field audits remain authoritative until a supported
native producer contract changes.

| Experiment | Required action after a native contract change | Completion evidence |
| --- | --- | --- |
| E-Task-0 | Record the authoritative producer operation route and its native identity fields. | A real route manifest identifies every supported producer/surface without inference. |
| E-Task-1 | Audit the changed contract for task, operation, ordering, timing, tool, outcome, and project authority. | Required fields are authoritative and co-occur under exact producer/native-session lineage. |
| E-Task-2 | Reduce real authoritative operations in deterministic order. | Accepted, rejected, duplicate, ordered-operation, task, and tool populations conserve. |
| E-Task-3 | Bind explicit terminal task outcomes to durable task identity. | Real success, failure, timeout, cancellation, and unknown outcomes come from an authoritative terminal boundary. |
| E-Task-4 | Emit immutable ordered-task, operation, tool, and terminal projections and execute each owned row directly. | A24 and A26–A35 each have bounded real row-level remote/oracle equality and an `EvidenceBundle`. |

Do not infer a terminal task outcome from session end, final tool success,
absence of errors, or response text.

### Recurrence and policy

Application-owned projections may be implemented only after their upstream
identity and semantic owners exist. Synthetic records cannot create real
practices, rules, outcomes, applications, or recurrence populations.

| Experiment | Proof-matrix prerequisites | Required action | Completion evidence |
| --- | --- | --- | --- |
| E-Recurrence-0 | E-Attribution-1 for `omp` and `codex-cli`; E-Attribution-2 for `codex-app-server`; durable canonical task identity; retention-compatible activity projection | Reopen Wave A without substituting task terminal-outcome authority that A40/A41 do not require. | Populated M13/M16 real cohorts reconcile remotely without losing source-time membership. |
| E-Recurrence-1 | No experiment prerequisite; requires its own finding and membership authority | Emit immutable finding versions with explicit evidence-window bounds and exact canonical-task membership. | A38 real bounded finding membership and denominator reconcile remotely. |
| E-Recurrence-2 | E-Task-1, E-Task-2, and E-Task-3 | Emit immutable successful-practice candidates only from authoritative ordered operations, terminal outcomes, task class, comparable population, and enforcement owner. | A39 has a real eligible candidate population and exact remote/oracle equality. |
| E-Recurrence-3 | E-Task-1 and E-Task-2 | Emit a versioned machine rule, applicability, required-action observation, and violation registry. | A42 has a real applicable population and exact adherence/violation reconciliation. |
| E-Recurrence-4 | E-Recurrence-1 and E-Recurrence-2 | Emit immutable practice, intervention-application, and recurrence-result projections linked to validated proposal history. | A36, A37, and A43 reconcile real lineage, tier, window, exposure, and arithmetic populations remotely. |

Evidence work may follow these dependencies independently. Do not schedule,
enrich, or deploy any production recurrence route or projection until its
individual row gate and the original M13/M16 exit gate permit it.

## Evidence retention and matrix updates

For each execution:

- retain the privacy allowlist and prove prohibited content was not captured;
- retain direct row-level SQL, independent oracle implementation, parameters,
  scalar values and types, and immutable event IDs;
- retain failed and blocked attempts rather than overwriting them;
- update only the affected proof-matrix rows after their individual promotion
  gates pass; and
- preserve `evidence_bundle: null` for every row that remains `Blocked` or
  `Not applicable`.

## Execution record: 2026-09-05

### Qualified experiment and retained native boundaries

[Final Pipeline replay](../experiments/dashboard_prototype/evidence/metric-proof-pipeline-final-20260905T104111835Z-1aa77342-037.json)
and its
[frozen inputs](../experiments/dashboard_prototype/evidence/metric-proof-pipeline-final-20260905T104111835Z-1aa77342-037-inputs.json)
retain the original source window and independently observed subject drain.
All 31 primitives and six results were delivered. E-Pipeline-5 is `Proven`:
10 selected events, 10 attempted events, 10 final-drain attempts, zero failed
events, zero pending events, null oldest pending age, and `0.0` drain-failure
percentage. The native query retains `Nullable(Float64)` evidence, one exact
matching completion, and all 21 immutable calculation primitives.

The
[native replay audit](../experiments/dashboard_prototype/evidence/metric-proof-native-audit-20260905T102950599646Z-aa9a68ca45b1.json)
observed 105 physical rows for 21 immutable primitives. Identical physical
copies collapse by timestamp and complete sorted typed attribute maps;
conflicting payloads for one immutable ID fail qualification. This preserves
the canonical idempotent-replay contract rather than counting transport copies
as new logical events.

The
[pending diagnostic](../experiments/dashboard_prototype/evidence/metric-proof-pending-native-20260905T102258487Z-5785ea48-5ba.json)
uses all 31 events from the retained real failed drain. The old string-map query
counted zero pending events; the Boolean-map query counted 31. This is
nonqualifying diagnostic evidence: native failed-attempt instants are absent,
and canonical normalization rejects the missing authoritative completion.
Neither failed-attempt times nor retry history were backfilled. The fresh
pre-state capture guard remains intact.

| Experiment | Remaining native boundary |
| --- | --- |
| E-Pipeline-1 | `durable_population_oracle`, `snapshot.bounded_drain_id`, `snapshot.error_class`, `snapshot.failed_during_drain`, `snapshot.payload_schema_version`, `snapshot.pending_outbox`, `snapshot.terminal_class` |
| E-Pipeline-2 | `authoritative schedule policy identity` |
| E-Pipeline-3 | Seven unavailable producer/surface/signal cohorts; complete deterministic count summaries remain in the final envelope. |
| E-Pipeline-4 | `bounded remote calculation counts`, `durable-integrity-failure population`; canonical rejections do not establish the full durable integrity-failure population. |
| E-Pipeline-6 | `maintenance_observation` |

E-Pipeline-5 has no decisive Appendix ownership. The proof matrix is unchanged:
127 rows remain `Blocked`, OMP A24/A34 remain `Not applicable`, and all 129
`evidence_bundle` values remain null.

### Attribution row assessment

The
[frozen independent Attribution inputs](../experiments/dashboard_prototype/evidence/metric-proof-attribution-individual-20260905T091325779Z-c6afecca-9d7-inputs.json)
retain uncapped native row identities, primitive IDs, and local oracles before
delivery. M21 counts native sessions, not repeated source records; its two
directional containment populations remain independent.

| Row / producer | Members | Unbound native identities | Outside observed 15-day retention | Frozen local oracle |
| --- | ---: | ---: | ---: | --- |
| A07 `codex-cli` | 189 | 0 | 67 | source 52; source-with-lifecycle 0; lifecycle 137; lifecycle-with-source 1 |
| A07 `omp` | 5695 | 0 | 396 | source 2639; source-with-lifecycle 2479; lifecycle 3056; lifecycle-with-source 2481 |
| A08 `codex-cli` | 17 | 8 | 17 | eligible 17; attributed 4; unresolved 13; distinct projects 1 |
| A09 `codex-cli` | 17 | 8 | 17 | eligible 17; unresolved 13; diagnostic count 13 |

All 17 native activity timestamps retain their exact submicrosecond remainder
through primitive construction. Public numeric attributes do not carry
nanosecond integers above the exact Float64 range. This is extraction and
serialization evidence, not full-population remote proof.

The
[full Attribution attempt](../experiments/dashboard_prototype/evidence/metric-proof-attribution-20260905T080550620Z-9c9356c8-d8e.json)
delivered 5902 selected primitives but failed remote visibility. The
[visibility record](../experiments/dashboard_prototype/evidence/metric-proof-visibility-20260905T081003380049Z-572d8795b80c.json)
retains all 474 missing IDs; the
[native retention observation](../experiments/dashboard_prototype/evidence/metric-proof-retention-20260905T085441455Z-37edc7a6-d44.json)
establishes that all were outside the observed 15-day retention window:
473 E-Attribution-1 IDs and one E-Attribution-4 ID. No population was trimmed or
rebased to obtain delivery. Historical E-Attribution-1 remains `Proven`; no
current row-bound proof qualifies.

Independent E-Attribution-2, E-Attribution-3, and E-Attribution-5 runs each
delivered their single `Blocked` result without inventing calculation inputs.
Their missing boundaries remain native Codex app-server scenario source
authority, Claude three-way identity authority, and late-context lifecycle
authority plus the remote ever-unresolved denominator.

### Recurrence and unchanged producer contracts

[Final Recurrence replay](../experiments/dashboard_prototype/evidence/metric-proof-recurrence-final-20260905T104833748Z-7b8b7293-c8e.json)
uses the retained real scanner finding capture and the original selected
window. E-Recurrence-0–4 remain `Blocked`. The capture preserves exact private
`thread:<id>` lineage, explicit window definition, and native Float64
`reducer_counts`. Exact table and trigger definitions are validated.

The observed scanner policy is `rolling_utc_7d`, not
`europe_london_calendar_7d`; equal-looking dates do not establish the required
policy. Global latest activity-version authority and immutable canonical-task
membership remain unqualified. Practice, rule, intervention, and recurrence
projections retain their separate native prerequisites.

Request and Task contracts were not re-audited or simulated. The exact missing
native fields and owners are now in the standalone metric-to-proof register;
the workstream actions above retain the execution's historical obligations.
Claude ingestion and supported-producer
denominators remain closed.

All failed, blocked, and successful evidence and database clones are retained.
The isolated collector was stopped; installed producer configuration,
production `ClickHouseClient`, production telemetry, and production dashboard
cutover were not changed.

### Verification

[Retained verification record](../experiments/dashboard_prototype/evidence/metric-proof-verification-20260905T111648419Z-baaa9327-db7.json)
records the successful maintained gate: Ruff, mypy across 27 source files,
995 pytest tests, TypeScript lint, and Markdown lint. All four new maintained
Python files separately passed Ruff and formatting checks. The earlier
994-pass/one-failure run is retained in the record; its missing durable-integrity
authority marker was corrected in source without weakening the behavioral
assertion.

The record inventories 59 runtime artifacts with SHA-256 checksums and retains
all three application database clones. It also records the resolved independent
reviews and the isolated collector's successful exit and automatic removal.

## Historical stop and completion conditions

Use the proof matrix as the single live source for row status and prerequisites.
Run any available independent experiment or row-bound capture.

Stop an experiment when its next required authoritative owner or native
contract is unavailable. Record the exact missing boundary and do not
substitute synthetic authority. Do not repeat an unchanged contract audit;
resume only when that boundary changes.

Each of the original 23 experiment actions remains an explicit plan item.
E-Pipeline-5 has produced its stated completion evidence; the remaining 22
experiments remain `Blocked` and open. Experiments without decisive Appendix
ownership remain mandatory; an existing or continuing `Blocked` row does not
complete them.

Promote only rows whose individual gates pass and preserve every still-`Blocked`
classification. The plan's authoritative-proof objective is complete only when
all 23 experiment actions are complete and every applicable affected row is
`Proven` or is `Not applicable` with a normative capability reason. Production
implementation remains separately gated by the companion implementation plan.
