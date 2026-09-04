# Authoritative Proof Closure Plan

## Status and purpose

This is the follow-on execution plan for the 23 experiments classified as
`Blocked` by the completed
[Gap-Closure Prototype Experiment Plan](gap-closure-prototype-experiment-plan.md)
and for the blocked row-level obligations attached to E-Attribution-1.
E-Attribution-1 remains experiment-level `Proven`; this plan does not change
that result, any other Phase 0–6 result, the production-cutover prohibition, or
the unmet M13/M16 exit gate.

The objective is to obtain row-level authoritative proof for every applicable
supported producer without inventing producer capability or allowing synthetic
success to replace bounded real evidence.

Canonical inputs remain:

- [Dashboard Measure Design](dashboard-measure-v2.md), including Appendix A;
- [Prototype Proof Matrix](dashboard-prototype-proof-matrix.json), which owns
  row classifications and exact proof obligations;
- [Gap-Closure Prototype Experiment Plan](gap-closure-prototype-experiment-plan.md),
  which records the executed experiments and retained blockers;
- [React + SigNoz Companion Implementation Plan](react-signoz-companion-implementation-plan.md),
  which remains gated by row-level proof.

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

## Stop and completion conditions

Use the proof matrix as the single live source for row status and prerequisites.
Run any available independent experiment or row-bound capture.

Stop an experiment when its next required authoritative owner or native
contract is unavailable. Record the exact missing boundary and do not
substitute synthetic authority. Do not repeat an unchanged contract audit;
resume only when that boundary changes.

Each of the 23 `Blocked` experiments remains an explicit plan item and remains
open until it produces its stated completion evidence. Experiments without
decisive Appendix ownership remain mandatory; an existing or continuing
`Blocked` row does not complete them.

Promote only rows whose individual gates pass and preserve every still-`Blocked`
classification. The plan's authoritative-proof objective is complete only when
all 23 experiment actions are complete and every applicable affected row is
`Proven` or is `Not applicable` with a normative capability reason. Production
implementation remains separately gated by the companion implementation plan.
