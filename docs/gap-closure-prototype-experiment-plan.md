# Gap-Closure Prototype Experiment Plan

## Purpose

Prove whether the proposed dashboard gap closures are viable before committing
to their production schemas, reducers, projections, queries, and React pages.
The experiments must establish which calculations in Appendix A of
[Dashboard Measure v2](dashboard-measure-v2.md#appendix-a-dashboard-mock-control-data-readiness)
can be computed correctly across every supported producer.

This plan is the proof gate for the
[React and SigNoz Companion Implementation Plan](react-signoz-companion-implementation-plan.md).
A successful prototype authorizes design and implementation work; it is not a
production implementation or permission to display experimental data as
canonical.

## Execution progress

| Work item | Status | Matrix | Evidence | Proposal |
| --- | --- | --- | --- | --- |
| Phase 0 | Completed | [`dashboard-prototype-proof-matrix.json`](dashboard-prototype-proof-matrix.json): 129 rows; Appendix digest `8dc859867939fd206ec7b57e26807f4edbc9c977b94ceaa01939a71ff44fb1c3`; temporal-contract digest `4a1c2dc346fc86dbc0c0f50e6dfd2d20930aa65b7fa04cce3b5d55316c4a52fb` | `tests/test_prototype_experiments.py`: 23 passed; Ruff, format, mypy, and Markdown gates passed; independent reviewer approved with no actionable findings; completion judge: `COMPLETE — Phase 0 exit gate satisfied` | Freeze the schema-version-1 matrix, canonical Appendix row identity, per-domain temporal registry, typed prerequisites resolved against the full experiment registry (including experiments without decisive Appendix ownership), coherent decisive experiment/query/oracle contracts, static capability exclusions, authoritative-supported-producer boundary, computed-only `Proven` results, and deterministic per-output event identity |
| E-Pipeline-1 | Blocked | 9 rows: Blocked | [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json): `durable_population_oracle`, `snapshot.bounded_drain_id`, `snapshot.canonical_activities`, `snapshot.context_events`, `snapshot.duration_ms`, `snapshot.failed_during_drain`, `snapshot.logs`, `snapshot.payload_schema_version`, `snapshot.pending_outbox`, `snapshot.source_sessions`, `snapshot.terminal_class`, `snapshot.traces` | Block cutover until the bounded scan snapshot and durable oracle are complete. |
| E-Pipeline-2 | Blocked | 6 rows: Blocked | [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json): `authoritative schedule policy identity` | Read-only terminal cadence inventory |
| E-Pipeline-3 | Blocked | 3 rows: Blocked | [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json): 35 retained `source-observation:<producer>/<surface>/<signal>@<bound>` boundaries; accepted 10, rejected 35, population 45; 9 local cohort maps equal 9 remote cohort maps; `remote_calculation_reconciled=true`; only normative capability absence is `Not applicable` | Bound each source-lag reading to the scan extraction boundary. |
| E-Pipeline-4 | Blocked | No decisive Appendix ownership | [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json): `durable-integrity-failure population`; remote counts reconciled | Project only redacted P11 integrity incidents after reconciliation. |
| E-Pipeline-5 | Blocked | No decisive Appendix ownership | [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json): `attempts`, `final drain id`, `scan completed at` | Await the exact bounded outbox and final-drain evidence. |
| E-Pipeline-6 | Blocked | No decisive Appendix ownership | [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json): `maintenance_observation` | Allowlisted maintenance-ledger reconciliation only; no database access or writes. |
| E-Attribution-1 | Completed — Proven | 6 rows: Blocked | [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json): fresh-real retained authority; P5 directional capabilities and P5/P7 population conservation; P8 uses P7 population; exact remote calculation equality | Use exact producer/native-session authority and a common latest-version population for P7/P8. |
| E-Attribution-2 | Completed — Blocked | 3 rows: Blocked | [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json): `startup-scenario-source-authority`, `resume-scenario-source-authority`, `clear-scenario-source-authority`, `compact-scenario-source-authority`; accepted=0/4 scenarios | Canonical `codex-app-server` attribution requires exact lifecycle and source tuples. |
| E-Attribution-3 | Completed — Blocked | No decisive Appendix ownership | [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json): `three-way-identity-authority`; retained redacted evidence `a2665d12a395a375`; ingestion disabled | `hook session ID = local artifact session ID = OTEL session ID` |
| E-Attribution-4 | Completed — Blocked | No decisive Appendix ownership | [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json): `authoritative-linkage` and `authoritative-source-session`; selected=matched=`n`=negative-skew=0 | Measure first authoritative source delay from its accepted containing lifecycle interval. |
| E-Attribution-5 | Completed — Blocked | No decisive Appendix ownership | [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json): `late-context.remote-ever-unresolved-denominator`; candidate=transition=ever-unresolved=0 | Count only unsuperseded immediate unresolved-to-resolved activity versions. |
| E-Request-1 | Completed — Blocked | No decisive Appendix ownership | [`request-20260901-phase3-7.json`](../experiments/dashboard_prototype/evidence/request-20260901-phase3-7.json): 84 absent, 8 ambiguous, and 4 authoritative field classifications; exact remote classification equality | Add authoritative producer request and attempt lifecycle fields. |
| E-Request-2 | Completed — Blocked | No decisive Appendix ownership | [`request-20260901-phase3-7.json`](../experiments/dashboard_prototype/evidence/request-20260901-phase3-7.json): `authoritative_candidate_count=0`; audit proof `510eed1ae8deb415` | Do not construct request candidates without authoritative request, attempt, and accounting identity. |
| E-Request-3 | Completed — Blocked | 45 rows: Blocked | [`request-20260901-phase3-7.json`](../experiments/dashboard_prototype/evidence/request-20260901-phase3-7.json): `authoritative_candidate_count=0`; audit proof `510eed1ae8deb415` | Preserve E-Request-1 and do not calculate request records without authority. |
| E-Task-0 | Completed — Blocked | No decisive Appendix ownership | [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json): 33 routes; 4 authoritative, 20 ambiguous, 7 unavailable, 2 unsupported; route population 30; remote reconciled | Add an authoritative task operation route manifest. |
| E-Task-1 | Completed — Blocked | No decisive Appendix ownership | [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json): 60 fields; 7 authoritative, 14 ambiguous, 12 partial, 27 absent, 0 unsupported; remote reconciled | Add authoritative task-operation lifecycle fields. |
| E-Task-2 | Completed — Blocked | No decisive Appendix ownership | [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json): `authoritative_operation_candidate_population=0`; field-audit proof `57c662d6540ebd28` | Do not construct task-operation candidates from activity aggregates. |
| E-Task-3 | Completed — Blocked | No decisive Appendix ownership | [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json): canonical source authority present; three-producer audit; `terminal_candidate_population=0`; all three blocked | Add a separate task identity plus authoritative terminal task outcome hook or telemetry boundary. |
| E-Task-4 | Completed — Blocked | 33 rows: Blocked | [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json): `authoritative_operation_candidate_population=0`, `terminal_candidate_population=0`; E-Task-0/E-Task-1 classifications reconciled; ordered/terminal proofs bound | Do not calculate task outcomes without authoritative operation and terminal candidates. |
| E-Recurrence-0 | Completed — Blocked | 6 rows: Blocked | [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json): 3 M13 audit primitives, 0 eligible recent M16 activity rows, three ambiguous durable-task routes, exact remote/oracle equality; retained phase5-2 delivered 59 corrected primitives but failed closed before results when 17 historical source-timestamp events were outside remote retention | Add durable canonical task identity and a retention-compatible immutable canonical-activity projection before calculating M13/M16 remotely. |
| E-Recurrence-1 | Completed — Blocked | 3 rows: Blocked | [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json): 6 findings, 17 immutable memberships, 17 latest activity versions, no contradictions; durable evidence-window bounds and canonical task membership absent; remote reconciled | Emit application-owned immutable finding versions with explicit evidence-window bounds and exact canonical-task membership. |
| E-Recurrence-2 | Completed — Blocked | 3 rows: Blocked | [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json): 12 producer/count-kind authority-gap primitives; successful-practice candidate population unavailable and not encoded as zero; remote reconciled | Add immutable ordered-operation, explicit terminal-outcome, canonical task-class, comparable-population, and versioned enforcement-owner projections before promoting a successful-practice candidate. |
| E-Recurrence-3 | Completed — Blocked | 3 rows: Blocked | [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json): 12 producer/registry audit primitives; 0 complete registry schemas and 3 schema gaps; remote reconciled | Add an application-owned versioned machine rule, applicability, required-action observation, and violation registry. |
| E-Recurrence-4 | Completed — Blocked | 9 rows: Blocked | [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json): 9 primitives over 6 audited population labels; exact-integer bounded finding count 0; canonical practice, intervention-application, and recurrence-result projections absent; remote reconciled | Add immutable practice, intervention-application, and recurrence-result projections linked to validated proposal history. |
| Phase 6 | Completed with cleanup incident | [`dashboard-prototype-proof-matrix.json`](dashboard-prototype-proof-matrix.json): 129 complete rows — 127 `Blocked`, 2 static `Not applicable`, and no row-level `Proven` classification because E-Attribution-1 lacks per-row `EvidenceBundle`/event-id inputs | [`consolidation-cleanup-20260902.json`](../experiments/dashboard_prototype/evidence/consolidation-cleanup-20260902.json): 19 run selectors; immutable initial summary of 1,298 unique local delivered identities, 1,281 remote identities present, 17 historical exact-source recurrence IDs absent under retention, 38 duplicate remote rows, and 1,105 namespace-omitting remote rows; the initial predicate incorrectly deleted those unmatched rows and cannot be approved; exact stored namespace is now mandatory; all local evidence remains immutable | Accept only the E-Attribution-1 experiment-level proof; retain its row-level matrix status as unproven pending bound inputs. All other experiments are Blocked. No production cutover or later recurrence projection is authorized. |

Experiment cleanup equality-matches
`agent-introspection.dashboard-prototype.v1` and the immutable
`evidence_bundle.run_id`; wildcard, prefix, producer-wide, and time-only cleanup
are prohibited.

## Questions this plan must answer

1. Does each supported producer expose the authoritative native identities,
   timestamps, outcomes, and relationships required by the proposed closure?
2. Can one centralized reducer conserve the complete population without
   producer-specific dashboard logic?
3. Can the reduced result be emitted as immutable deterministic events and
   delivered to the local SigNoz stack?
4. Can the exact Appendix A calculation be executed remotely and reconciled to
   an independent oracle?
5. Does the closure preserve capability, time, identity, integrity, privacy,
   and missing-data semantics?
6. Is the closure ready for production implementation, not applicable to the
   producer, or blocked by a missing authoritative boundary?

## Producer scope

| Producer | Current project-attribution standing | Experiment treatment |
| --- | --- | --- |
| `omp` | Supported; fresh, resume, end, concurrent-project, and non-Git proof retained | Include in every relevant raw-field and reducer experiment. Workspace change is not exposed and remains `Not applicable`. |
| `codex-cli` | Supported; fresh, resume, concurrent-project, and non-Git proof retained | Include in every relevant raw-field and reducer experiment. Notify exposes neither end nor workspace change; those boundaries remain `Not applicable`. |
| `codex-app-server` | Supported for fresh and end lifecycle/source-session attribution | Include in every relevant experiment. Prove accepted `resume`, `clear`, and `compact` starts, concurrent-project and non-Git behavior, and any activity-producing telemetry surface before claiming complete attribution. Mid-thread workspace change remains unsupported. |
| `claude-code` | Unsupported by the retained identity proof | Do not include in supported-producer denominators. Reopen only after a fresh proof establishes hook session ID = local artifact session ID = OTEL session ID and source ingestion can be enabled without inference. |
| `codex-app` | Unsupported separate identity | Do not prototype. Desktop uses canonical producer `codex-app-server`. |

Historical attributed rows do not override the retained capability proof. A
missing field is not `Not applicable` unless the normative capability matrix
proves that the producer cannot expose that measurement.

## Prototype boundary

### Allowed

- bounded fresh real producer sessions created solely for the experiment;
- allowlisted raw metadata and canonical identities required by the measure;
- disposable reducer and projection code isolated from production execution;
- isolated local tables or files that preserve the proposed schema and
  deterministic identity behavior;
- immutable experiment events delivered to the loopback-only local SigNoz
  stack under an explicit experiment namespace;
- independent local oracles and exact remote queries;
- transport-only synthetic telemetry when clearly separated from semantic
  proof.

### Prohibited

- prompts, responses, transcripts, command output, arbitrary payloads, secrets,
  or unredacted high-cardinality attributes;
- inferred identity from CWD, process state, transcript paths, temporal
  proximity, or unrelated telemetry;
- overloading lifecycle adapters with provider requests, model accounting,
  tool calls, or terminal task outcomes;
- hardcoded semantic events, mocked producer facts, or fabricated successful
  outcomes used as viability evidence;
- dashboard calculations that join local SQLite at query time;
- producer-specific formulas that diverge from the canonical Appendix A
  calculation;
- production schema or migration cutover before the experiment report is
  accepted.

## Evidence standard

Each experiment produces one immutable evidence bundle containing:

- experiment ID, exact hypothesis, experiment namespace, and exact bounded run ID;
- producer, surface, installed version, and capability state;
- bounded start/end timestamps and source extraction boundary;
- exact selected-range membership operator, independent interval-containment
  operator, evaluation time, all-version requirement, and evaluation/version
  policy identifiers equal to the row's `evaluation_policy_id` and
  `version_policy_id`;
- allowlisted raw field inventory with presence, type, cardinality, and native
  semantic owner;
- native identity tuple followed first by the exact join step
  `native_identity_tuple` / `producer,native_session_id`; every remaining join
  step is typed and authoritative;
- canonical schema limited to matrix-allowlisted field names mapped only to
  allowlisted schema type descriptors, plus deterministic event-ID inputs
  exactly `experiment_id`, `producer`, `native_session_id`, and
  nonnegative-integer `event_id_ordinal`;
- reducer input count, accepted count, rejected count by reason, output count,
  duplicate count, and conservation equation;
- remote outbox event IDs and matching SigNoz/ClickHouse event IDs;
- exact row-bound Appendix calculation query and independent oracle result,
  with equal scalar values and exact scalar types;
- endpoint, negative, missing, conflicting, duplicate, out-of-order, version,
  and clock-skew cases;
- privacy review showing that no prohibited content was retained;
- result: `Proven`, `Not applicable`, `Blocked`, or `Failed`;
- production recommendation and unresolved dependency.

### Temporal selection registry

Every row selects only its declared timestamp domain:

| Domain | Operator |
| --- | --- |
| Canonical source, task, or activity | `source_time > start AND source_time <= end` |
| Scan completion | `scan_completed_at > start AND scan_completed_at <= end` |
| Source-session | `source_session_time > start AND source_session_time <= end` |
| Request terminal | `final_attempt_terminal_time > start AND final_attempt_terminal_time <= end` |
| Request start for grace-eligible unknowns | `request_start_time > start AND request_start_time <= end` |
| Trace source | `trace_source_time > start AND trace_source_time <= end` |
| Finding evidence | `finding_evidence_time > start AND finding_evidence_time <= end` |
| Intervention evidence | `intervention_evidence_time > start AND intervention_evidence_time <= end` |

Lifecycle containment is independent of selected-range membership and is exactly
`interval_start <= source_time < interval_end`; it is not applicable to rows
that do not use lifecycle intervals.

Cleanup selector authority requires the exact stored namespace
`agent-introspection.dashboard-prototype.v1`, exact immutable evidence-bundle
`run_id`, exact event family, and exact event IDs. Every local and remote field
must match the selector; an absent remote namespace is unmatched and blocks
deletion. Never use a wildcard, prefix, time-only, producer-wide, or
production-event selector.

`Proven` requires a bounded fresh real population for every applicable supported
producer, not merely schema inspection or historical rows. `Blocked` names the
missing authoritative field or boundary and the native surface that must own
it. `Failed` records a tested closure that violated an invariant or could not
conserve its population.

## Experiment harness

Create an isolated experiment harness with these responsibilities:

1. Capture only allowlisted fields from bounded source populations.
2. Validate producer-specific raw contracts at the edge.
3. Convert accepted inputs into one producer-neutral candidate schema.
4. Run the proposed centralized reducer twice in different input orders and
   require identical deterministic output.
5. Replay duplicate inputs and require idempotent results.
6. Inject malformed, conflicting, missing, out-of-order, version-gap,
   equal-native-ID/different-producer, and exact range/interval endpoint cases
   only at the reducer test boundary; these cases prove rejection and boundary
   behavior but never substitute for real positive evidence.
7. Deliver accepted immutable events through the existing OTLP outbox.
8. Query the local SigNoz ClickHouse store with the exact Appendix formula and
   the measure's declared range, containment, policy, and all-version rules.
9. Compare the remote result to an independently implemented local oracle.
10. Emit a machine-readable coverage matrix keyed by Appendix row and producer.

The harness must call the same shared canonical reducer for every producer.
Only edge normalization may differ by producer.

## Phase 0: freeze the proof matrix and experiment contracts

### Proof-matrix work

Phase 0 is **Completed**. The frozen contract retains source-session time for
A07, trace-source time for trace-accounted A20/M2, decisive typed query/oracle
references (E-Attribution-1/2 and E-Recurrence-4), static OMP
`Not applicable` exclusions for A24/A34, and the request boundary: Claude Code
remains excluded until E-Attribution-3 freshly proves
`hook session ID = local artifact session ID = OTEL session ID`; current
request closure uses only authoritative supported producers. Every matrix row
carries deterministic `Axx.evaluation-policy-v1` and
`Axx.version-policy-v1` identifiers. Future experiments remain responsible for
executing each row's exact Appendix query and comparing it with an independent
local oracle; `event_id_ordinal` is allowlisted for deterministic event identity.
Blocked rows carry the exact audited missing boundary, accountable owner and
surface, and decisive experiment; `Not applicable` rows carry a null boundary.
The proof contract permits `Proven` only for fresh-real, computed, exactly
reconciled evidence with population-conserving deterministic per-output event
identities.

1. Copy every Appendix A row into a machine-readable experiment matrix without
   changing its canonical control, fields, calculation, or gap-closure wording.
2. Add columns for producer, surface, normative capability reason, current field
   audit, experiment ID, reducer, proposed event family, projection boundary,
   exact selected-range operator, interval-containment operator, evaluation
   time/policy, all-version requirement, remote query, oracle, and result.
3. Require rows for `omp`, `codex-cli`, and `codex-app-server`; no supported
   producer may disappear because another producer is `Proven`.
4. Mark existing retained proof that remains authoritative; do not rerun it
   solely to increase counts.
5. Record the exact unsupported boundaries for OMP, Codex CLI,
   `codex-app-server`, Claude Code, and separate `codex-app`.
6. Define one experiment namespace and cleanup boundary that cannot be confused
   with production canonical events.
7. Define the privacy allowlist before capturing any fresh event.
8. Fix each matrix row identity to canonical `A01`–`A43`, with `producer` as a
   separate field and exactly one row per supported producer.
9. Register every timestamp domain named by a row's required fields with its
   explicit selected-range operator; never substitute generic range language or
   lifecycle containment.
10. Encode each remote query and independent oracle as a typed proof reference
    with exactly `reference_id`, `experiment_id`,
    `prerequisite_experiment_ids`, and `description`; derive its deterministic
    reference ID from row ID, producer, and kind, bind it to the row's decisive
    experiment, and list only strict prerequisite experiments. Future experiments
    define and execute the exact Appendix query and oracle comparison.
    Source-session and trace-source timestamp domains must have their own exact
    range operators; `Not applicable` rows retain only the capability-absence
    audit reference and execute neither query nor oracle.
11. Exclude Claude Code from current request closure and ingestion until
    E-Attribution-3 freshly proves
    `hook session ID = local artifact session ID = OTEL session ID`.

### Proof-matrix exit gate

- Every Appendix A row belongs to exactly one closure stream below.
- Every applicable supported producer has an explicit proof obligation.
- No experiment depends on prohibited content or inferred identity.
- The implementation plan can read the matrix without interpreting free-form
  notes.

## Phase 1: pipeline snapshot, schedule, lag, integrity, and ledger closure

This phase starts first because the pipeline is application-owned and can be
prototyped without changing producer hooks.

### Pipeline hypotheses

1. Existing scan, scheduler, source-boundary, outbox, ledger, and migration data
   can produce complete canonical snapshots and maintenance events.
2. Per-producer/surface/signal lag can be reduced at scan time from existing
   authoritative timestamps.
3. The exact Pipeline health calculations can run remotely without local SQLite
   joins.

### Pipeline experiments

#### E-Pipeline-1: canonical snapshot completeness

- **Status:** Blocked.
- **Evidence:** [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json) retained fresh-real run `pipeline-20260901T213721Z`; selected=delivered: 46 primitive and 6 result IDs; exact blockers: `durable_population_oracle`, `snapshot.bounded_drain_id`, `snapshot.canonical_activities`, `snapshot.context_events`, `snapshot.duration_ms`, `snapshot.failed_during_drain`, `snapshot.logs`, `snapshot.payload_schema_version`, `snapshot.pending_outbox`, `snapshot.source_sessions`, `snapshot.terminal_class`, `snapshot.traces`.
- **Proposal:** Block cutover until the bounded scan snapshot and durable oracle are complete.

- Extend a disposable snapshot candidate with payload schema version, terminal
  class, duration, error class, rows, logs, traces, context events, canonical
  activities, source sessions, pending outbox, and failed-during-drain counts.
- Reconcile every count to the completed scan's durable local populations.
- Prove that failed-during-drain is a selected event population, not a current
  outbox-state alias.
- Deliver the candidate and execute Latest pipeline snapshot, Scan workload &
  duration, and P10 snapshot calculations remotely.

#### E-Pipeline-2: terminal-state and cadence contract

- **Status:** Blocked.
- **Evidence:** [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json) retained fresh-real run `pipeline-20260901T213721Z`; exact blocker: `authoritative schedule policy identity`.
- **Proposal:** Read-only terminal cadence inventory

- Inventory every durable scan terminal state and determine whether partial and
  cancelled states exist authoritatively.
- Prototype the smallest explicit terminal-state contract that covers observed
  states without inventing outcomes.
- Emit authoritative schedule interval and policy identity from scanner
  configuration.
- Execute Scan outcomes and Freshness & cadence, including stale and missed
  cadence boundaries, against a controlled real schedule window.

#### E-Pipeline-3: source lag reducer

- **Status:** Blocked.
- **Evidence:** [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json) retains 35 exact `source-observation:<producer>/<surface>/<signal>@<bound>` boundaries; accepted 10, rejected 35, population 45; 9 local cohort maps equal 9 remote cohort maps; `remote_calculation_reconciled=true`; only normative capability absence renders `Not applicable`.
- **Proposal:** Bound each source-lag reading to the scan extraction boundary.

- Select a bounded population of successful scans with distinct extraction
  upper bounds and completion timestamps spanning selected-range endpoints.
- For every bound and supported `(producer, surface, signal kind)`, select the
  latest authoritative source timestamp at or before that bound.
- Calculate each lag against its own bound, then prove selected-current,
  scan-completion range membership, p50, p95, sample count, and negative
  clock-skew count against an independent oracle.
- Prove that producer/signal cohorts conserve the eligible per-scan population
  and that only normative capability absence renders `Not applicable`.
- Deliver and query the immutable multi-scan lag projection remotely.

#### E-Pipeline-4: integrity and rejection projection

- **Status:** Blocked.
- **Evidence:** [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json) retained fresh-real run `pipeline-20260901T213721Z`; exact blocker: `durable-integrity-failure population`; remote counts reconciled.
- **Proposal:** Project only redacted P11 integrity incidents after reconciliation.

- Inventory durable integrity failures, rejected lifecycle/context events,
  deterministic-ID conflicts, version gaps, and conflicting identities.
- Define redacted immutable P11 events carrying invariant, projection/event
  name, time, producer/runtime, and affected short identity.
- Execute Integrity incidents remotely and require corrupt cohorts to be
  withheld rather than partially aggregated.

#### E-Pipeline-5: immutable outbox delivery detail

- **Status:** Blocked.
- **Evidence:** [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json) retained fresh-real run `pipeline-20260901T213721Z`; exact blockers: `attempts`, `final drain id`, `scan completed at`.
- **Proposal:** Await the exact bounded outbox and final-drain evidence.

- Project immutable event creation identity/time, destination, event type,
  bounded final-drain identity, delivery-attempt identity/time, attempt status,
  and redacted error class.
- Reconcile pending age to the oldest pending event creation time at scan
  completion.
- Reconcile failed and attempted distinct-event populations for the bounded
  final drain, preserving failed events inside pending rather than splitting
  overlapping totals.
- Execute P10 delivery detail, pending age, and drain-failure percentage
  remotely; keep this gate independent from P10 snapshot counts.

#### E-Pipeline-6: redacted ledger maintenance projection

- **Status:** Blocked.
- **Evidence:** [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json) retained fresh-real run `pipeline-20260901T213721Z`; exact blocker: `maintenance_observation`.
- **Proposal:** Allowlisted maintenance-ledger reconciliation only; no database access or writes.

- Project database identity, check type, completed time, check result, backup
  time, database bytes, WAL bytes, `freelist_count`, `page_count`, migration
  state, and runtime host without paths or sensitive payloads.
- Select the latest completed observation per database identity and calculate
  check age, backup age, and
  `free-page % = 100 × freelist_count / page_count` only for positive page
  count.
- Prove zero/nonpositive page-count behavior, stale checks/backups, migration
  state, and database separation against local durable state.
- Execute the complete P12 Ledger health calculation remotely.

### Pipeline Appendix A rows covered

- Latest pipeline snapshot;
- Scan outcomes;
- Freshness & cadence;
- Scan workload & duration;
- Source lag by producer;
- Time range support for Pipeline health.

Producer lifecycle correlation, Attribution coverage, and Attribution
diagnostics are completed in Phase 2 because they depend on the producer proof
matrix.

### Pipeline exit gate

- Pipeline experiment events reconcile exactly to durable scan state.
- P1–P3 and Canonical scan evidence have a proven remote source or an explicit
  blocked gate.
- P4 passes the bounded multi-scan current/p50/p95/`n` and negative-skew oracle.
- P10 snapshot and immutable delivery-detail contracts pass separate gates.
- P11 corrupt-cohort suppression and immutable incident counts reconcile.
- P12's complete redacted maintenance fields and free-page calculation
  reconcile per database identity.
- No producer adapter change is proposed for pipeline-owned facts.

## Phase 2: project and lifecycle attribution closure

### Attribution hypotheses

1. The shared session-context runtime and ledger remain the only canonical
   project resolver.
2. Directional lifecycle/source coverage can be computed from exact
   `(producer, native_session_id)` identities and capability state.
3. Every supported producer can either produce an attributable canonical
   activity or be explicitly classified as lifecycle/source-session only.

### Attribution experiments

Attribution execution is **Completed**: all five experiments have recorded
results, but Phase 2 remains **Blocked** because E-Attribution-2 through
E-Attribution-5 retain their typed result states.

#### E-Attribution-1: retained supported-producer baseline

- **Status:** Proven.
- **Evidence:** [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json) fresh-real retained run `attribution-20260901-phase2-2`; `fresh_real_provenance=true`, `retained_authority_valid=true`, `p5_directional_capabilities_complete=true`, `p5_population_conserves=true`, `p7_population_conserves=true`, `p8_uses_p7_population=true`, and `remote_calculation_reconciled=true`. The retained run selected/delivered 35/35 primitive and 5/5 result events; its exact 40-ID cleanup selector is recorded in [`attribution-cleanup-manifest-20260901.json`](../experiments/dashboard_prototype/evidence/attribution-cleanup-manifest-20260901.json). The failed `attribution-20260901-phase2-1` run retains its separate exact 35 primitive selectors in that manifest.
- **Proposal:** Use exact producer/native-session authority and a common latest-version population for P7/P8.

- Load the retained OMP and Codex CLI proof populations and validate their
  immutable evidence IDs, native identities, accepted project tuples, and
  capability boundaries.
- Execute Producer lifecycle correlation for both directional cohorts.
- Execute Attribution coverage and Attribution diagnostics over the matching
  latest canonical activity population.
- Do not require unsupported OMP workspace-change or Codex CLI end/workspace
  boundaries.

#### E-Attribution-2: Codex Desktop start-source matrix

- **Status:** Blocked.
- **Evidence:** [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json) fresh-real retained run `attribution-20260901-phase2-2`; `startup_authoritative=false`, `resume_authoritative=false`, `clear_authoritative=false`, `compact_authoritative=false`, `accepted_count=0`, and `scenario_count=4`. Exact blockers: `startup-scenario-source-authority`, `resume-scenario-source-authority`, `clear-scenario-source-authority`, and `compact-scenario-source-authority`.
- **Proposal:** Canonical `codex-app-server` attribution requires exact lifecycle and source tuples.

Create bounded fresh `codex-app-server` sessions for each documented
`SessionStart` source:

- `startup`;
- `resume`;
- `clear`;
- `compact`.

For each session, prove:

- hook session ID equals source OTEL thread/session correlation;
- lifecycle start and end are accepted by the shared runtime;
- project tuple is exact and stable;
- source session is directionally correlated;
- concurrent projects remain isolated;
- a non-Git workspace is classified canonically;
- a qualifying source observation, when the installed surface can emit one,
  produces a latest canonical activity version with the same project.

If the surface emits only `session_task.turn` or `startup_prewarm`, classify the
canonical-activity path as `Blocked` by missing qualifying source evidence; do
not fabricate detector/tool activity.

#### E-Attribution-3: Claude identity blocker confirmation boundary

- **Status:** Blocked.
- **Evidence:** [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json) retained evidence `a2665d12a395a375`; `fresh_installed_three_way_authority=false`, `ingestion_disabled=true`, `reopening_condition_recorded=true`, `retained_evidence_redacted=true`, `remote_primitive_count=0`, and `supported_denominator_population=0`. Exact blocker: `three-way-identity-authority`.
- **Proposal:** `hook session ID = local artifact session ID = OTEL session ID`

Do not enable Claude ingestion. Record the retained mismatch and define the
single future reopening condition:

```text
hook session ID = local artifact session ID = OTEL session ID
```

Only a newly installed native contract that can prove this equality may start a
separate follow-on experiment. Historical attributed rows remain excluded.

#### E-Attribution-4: P6 lifecycle-to-source delay

- **Status:** Blocked.
- **Evidence:** [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json) fresh-real retained run `attribution-20260901-phase2-2`; `containing_lifecycle_matched=true`, `first_source_selected=true`, `no_contradictory_authority=true`, and `population_conserved=true`, but `selected_sessions=0`, `matched_sessions=0`, `n=0`, and `negative_skew_sessions=0`. Exact blockers: `authoritative-linkage` and `authoritative-source-session`.
- **Proposal:** Measure first authoritative source delay from its accepted containing lifecycle interval.

- Select bounded source sessions whose first source event is inside, exactly on,
  and outside selected-range endpoints.
- Load accepted containing lifecycle intervals even when their start precedes
  the selected range; use ordered `(timestamp, event.id)` boundaries and
  `interval_start <= source_time < interval_end`.
- Select the first source event in range per source session, calculate delay
  from the containing interval start, and report p50/p95/`n`, matched sessions,
  and negative-skew sessions.
- Exclude negative delays from percentiles while retaining their skew count.
- Reconcile every remote result to an independent containment and percentile
  oracle.

#### E-Attribution-5: P9 late-context reconciliation

- **Status:** Blocked.
- **Evidence:** [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json) fresh-real retained run `attribution-20260901-phase2-2`; `globally_valid_histories=true`, `immediate_predecessor=true`, and `transition_count_independent=true`, but `denominator_reconciled=false`, `candidate_count=0`, `transition_count=0`, and `ever_unresolved_count=0`. Exact blocker: `late-context.remote-ever-unresolved-denominator`.
- **Proposal:** Count only unsuperseded immediate unresolved-to-resolved activity versions.

- Load globally valid activity version histories for candidate stable activity
  IDs selected by immutable source time.
- Require resolved version `n` and its immediate valid predecessor `n-1` to be
  unresolved; reject version gaps, superseded versions, and non-immediate
  transitions.
- Count each transition once by the resolved version's deterministic event ID
  and retain producer, surface, original rejection reason, method, project, and
  short activity identity.
- Prove transition count and evidence remotely across exact source-range
  endpoints.
- Separately load all versions for candidate activity IDs independent of the
  selected delivery range and prove the `activities ever observed unresolved`
  denominator before enabling the reconciliation rate.

### Attribution Appendix A rows covered

- Producer lifecycle correlation;
- Lifecycle-to-source delay;
- Attribution coverage;
- Attribution diagnostics;
- Late-context reconciliation;
- Project controls and project identity dependencies used by later routes.

### Attribution exit gate

- OMP, Codex CLI, and `codex-app-server` have a producer-by-scenario result
  matrix with no inferred identity.
- P5 directional cohorts and capability states reconcile.
- P6 interval containment, first-source selection, p50/p95/`n`, matched-session,
  and negative-skew results reconcile.
- P7/P8 latest-version populations conserve their common eligible cohort.
- P9 transition count reconciles immediate predecessor/version/event identity;
  its rate remains blocked until its independent all-version denominator
  reconciles.
- Claude Code and separate `codex-app` remain outside supported denominators.

## Phase 3: logical request, attempt, provider, model, latency, and token closure

### Request and attempt hypothesis

Edge-specific raw mappings can feed one canonical logical request/attempt
reducer carrying producer, surface, `(producer, native_session_id)`, stable
request/attempt/accounting identities, complete ordered attempts, explicit
terminal outcomes, role-separated models, latency phases, errors, retry
reasons, token accounting, project identity, and versioned terminal grace.

### Request and attempt experiment sequence

Request and attempt execution is **Completed**: retained fresh-real run
`request-20260901-phase3-7` recorded all three typed `Blocked` results, so
Phase 3 remains **Blocked**. The retained run selected and delivered 96
primitive and 3 result events; its exact 99-ID cleanup selector is recorded in
[`request-cleanup-manifest-20260901.json`](../experiments/dashboard_prototype/evidence/request-cleanup-manifest-20260901.json).
The cleanup manifest records phase3-1 failed after 96 delivered primitive IDs,
phase3-2 and phase3-3 failed after 99 delivered IDs each, and phase3-4,
phase3-5, and phase3-6 were superseded after 99 delivered IDs each; phase3-7
is retained with 99 exact IDs.
Cleanup is limited to namespace, exact run ID, and exact event IDs.

#### E-Request-1: authoritative raw-field audit

- **Status:** Blocked.
- **Evidence:** [`request-20260901-phase3-7.json`](../experiments/dashboard_prototype/evidence/request-20260901-phase3-7.json) retained fresh-real run `request-20260901-phase3-7` classified 96 field observations across all three producers: 84 absent, 8 ambiguous, 4 authoritative, and 0 unsupported. `complete_three_producer_matrix=true`, `request_identity_not_inferred=true`, and the remote classification exactly equals the local oracle; that equality does not make request measures `Proven`. Exact missing authority: `producer`, `surface`, `native_session_id`, provider, logical-request, attempt, accounting-record, deterministic-event, timing, model, terminal, provider-status/error, retry, token-accounting, and project-state fields.
- **Proposal:** Add authoritative producer request and attempt lifecycle fields.

For each supported producer, capture a bounded fresh set containing, where
natively exposed:

- producer and surface;
- namespaced `(producer, native_session_id)`;
- provider identity;
- logical request identity;
- attempt identity or immutable attempt index;
- request start, provider accepted, first token, stream end, and terminal times;
- requested and response model identities;
- explicit success, failure, timeout, cancellation, and unknown terminal state;
- provider status/error class and retryability;
- retry membership, order, and reason;
- input, cached-input, output, and reasoning token accounting with accounting
  record identity;
- exact canonical project identity or unresolved state.

Classify every required field as authoritative, absent, ambiguous, or
unsupported. Temporal proximity and turn totals cannot substitute for request
identity.

#### E-Request-2: canonical request/attempt reducer

- **Status:** Blocked.
- **Evidence:** [`request-20260901-phase3-7.json`](../experiments/dashboard_prototype/evidence/request-20260901-phase3-7.json) retained fresh-real run `request-20260901-phase3-7`; audit proof `510eed1ae8deb415` is bound and `authoritative_candidate_count=0`. No authoritative logical-request, attempt, or accounting identity candidate exists.
- **Proposal:** Do not construct request candidates without authoritative request, attempt, and accounting identity.

- Make producer, surface, `(producer, native_session_id)`, provider,
  logical-request, attempt, accounting-record, and deterministic event
  identities mandatory on candidate and output records.
- Reduce complete attempt sets into one validated final request outcome.
- Preserve every attempt for attempt-level calculations.
- Attach canonical project identity through the existing session-context
  boundary before event emission.
- Define a versioned provider-keyed terminal-grace policy with validity
  boundary and bind unknown-outcome evaluation to explicit query time.
- Validate retry ordering, timestamp ordering, model roles, provider taxonomy,
  token accounting, duplicates, and conflicting identities.
- Test equal native session IDs from different producers as separate records;
  reject conflicting producer/surface lineage and withhold affected aggregates.
- Require conservation between accepted raw attempts, projected attempts,
  accounting records, and logical requests.

#### E-Request-3: remote projection and calculation proof

- **Status:** Blocked.
- **Evidence:** [`request-20260901-phase3-7.json`](../experiments/dashboard_prototype/evidence/request-20260901-phase3-7.json) retained fresh-real run `request-20260901-phase3-7`; audit proof `510eed1ae8deb415` is bound and `authoritative_candidate_count=0`. The authoritative request-attempt projection boundary is absent, so no remote request calculation can be proved.
- **Proposal:** Preserve E-Request-1 and do not calculate request records without authority.

Deliver immutable request, attempt, accounting, and terminal-grace policy
candidates to SigNoz and execute:

- Provider, Model role, and Model controls;
- Logical request outcomes and Outcome summary;
- Provider error composition;
- Latency phases;
- Retry behavior;
- Requested → response model conformance;
- Model calls & provenance;
- Request-accounted token pressure;
- Model calls over time.

Compare every count, percentage, percentile, sample count, unknown population,
identity dimension, and invalid-timestamp count to an independent oracle.
For unknown terminal outcomes, prove immediately-before, exactly-at, and
immediately-after grace behavior with the same policy identity and evaluation
time returned by the remote query.

### Decision rule

- `Proven`: every required field is authoritative and the centralized reducer
  and remote formulas reconcile for the producer.
- `Not applicable`: the normative capability matrix excludes the population.
- `Blocked`: at least one required native identity, boundary, or accounting
  field is absent or ambiguous. Name the required native instrumentation point;
  do not extend lifecycle adapters.

### Request and attempt exit gate

- R1–R8 and request-accounted M1/M2 have a producer-by-measure matrix covering
  `omp`, `codex-cli`, and `codex-app-server`.
- Each dependency is `Proven`, static `Not applicable` with normative reason,
  or `Blocked` with the missing native boundary; blocked rows are never omitted.
- One canonical reducer contract works for every producer classified `Proven`.
- Producer/surface/namespaced-session lineage and terminal-grace policy/time
  reconcile in every remote query and oracle.
- Mixed provider/model/request populations cannot silently enter denominators.

## Phase 4: ordered task, tool, friction, and terminal-outcome closure

### Task and tool hypothesis

Existing source identities, timestamps, hydrated operations, and explicit tool
outcomes can feed one complete ordered task/tool projection, while a separate
authoritative task boundary supplies terminal task outcome where currently
absent.

### Task and tool experiment sequence

Task and tool execution is **Completed**: retained fresh-real run
`task-20260902-phase4-1` recorded all five typed `Blocked` results, so Phase 4
remains **Blocked**. The retained run selected and delivered 93 primitive and 5
result events; its exact 98-ID cleanup selector is recorded in
[`task-cleanup-manifest-20260902.json`](../experiments/dashboard_prototype/evidence/task-cleanup-manifest-20260902.json).
Cleanup is limited to namespace, exact run ID, and exact event IDs.

#### E-Task-0: Canonical-now Model usage availability

- **Status:** Blocked.
- **Evidence:** [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json) retained fresh-real run `task-20260902-phase4-1` classified 33 routes across all three producers: 4 authoritative, 20 ambiguous, 7 unavailable, and 2 unsupported, with route population 30. `remote_calculation_reconciled=true`; the remote classification exactly equals the local oracle, and that equality does not make task measures `Proven`.
- **Proposal:** Add an authoritative task operation route manifest.

- Query existing canonical trace/task/activity populations for trace-accounted
  M2, M3, M4 timing, count-only M5 and M7–M11, plus M13/M16 dependencies.
- For each producer and measure, prove the exact complete fields, capability
  state, range operator, population, and redaction boundary already available.
- Emit only the remote canonical projections required to avoid local SQLite
  joins; do not add deferred rates, recovery, distributions, sequences, or
  terminal outcomes.
- Produce exact compacted manifests for every route with at least one complete
  owned calculation.

#### E-Task-1: task and operation field audit

- **Status:** Blocked.
- **Evidence:** [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json) retained fresh-real run `task-20260902-phase4-1` classified 60 field observations across all three producers: 27 absent, 14 ambiguous, 12 partial, 7 authoritative, and 0 unsupported. `complete_three_producer_matrix=true`, `task_identity_not_inferred=true`, `terminal_outcome_not_inferred=true`, and the remote classification exactly equals the local oracle; that equality does not make task measures `Proven`. Exact missing authority is recorded for producer, surface, native session, canonical task, source-session relationship, immutable call/event, source ordering/timestamp, normalized tool/operation, target/failure, explicit tool/sandbox outcomes, mutation/quality-command relationships, user friction, terminal task outcome/timestamp, and project identity state.
- **Proposal:** Add authoritative task-operation lifecycle fields.

For each supported producer, capture bounded real tasks and inventory:

- producer and surface;
- namespaced `(producer, native_session_id)`;
- canonical task identity and exact source session relationship;
- immutable call/event identity;
- complete source ordering and timestamps;
- normalized tool and operation;
- allowlisted redacted target and failure fingerprint;
- explicit tool outcome/status;
- structured sandbox outcome;
- mutation and quality-command relationships;
- explicit user friction signals;
- explicit terminal task outcome and terminal timestamp;
- canonical project identity or unresolved state.

Prove completeness of the observable tool-using task denominator. Missing task
end is `Unknown`, not failure or success.

#### E-Task-2: ordered task/tool reducer

- **Status:** Blocked.
- **Evidence:** [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json) retained fresh-real run `task-20260902-phase4-1`; field-audit proof `57c662d6540ebd28` is bound and `authoritative_operation_candidate_population=0`. Synthetic reducer tests validate reducer logic but cannot establish live authority.
- **Proposal:** Do not construct task-operation candidates from activity aggregates.

- Produce one deterministic ordered operation sequence per task.
- Make producer, surface, `(producer, native_session_id)`, canonical task,
  call/event, and project lineage mandatory on candidate and output records.
- Preserve duplicate delivery, normalized operation, target, failure, outcome,
  and source ordering.
- Materialize failure groups, later same-operation recovery, repeated attempts,
  command churn, contiguous cycles, sandbox friction, and complete
  fail-mutate-pass sequences.
- Reject incomplete ordering, conflicting call identity, missing required
  outcome, impossible timestamp order, and conflicting producer/surface/task
  lineage.
- Test equal native session IDs from different producers as separate records
  and require affected aggregates to be withheld on lineage conflicts.
- Keep lifecycle and provider reducers separate.

#### E-Task-3: terminal task-outcome boundary

- **Status:** Blocked.
- **Evidence:** [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json) retained fresh-real run `task-20260902-phase4-1`; canonical source authority is present, the three-producer audit is complete, `terminal_candidate_population=0`, and all three producers are blocked. No terminal task outcome is inferred.
- **Proposal:** Add a separate task identity plus authoritative terminal task outcome hook or telemetry boundary.

- Audit installed native producer surfaces for an explicit authoritative task
  completion outcome.
- Where present, prove exact correlation to the ordered task identity.
- Where absent, identify a separate authoritative task hook or native telemetry
  boundary; do not infer outcome from session end, final tool success, absence of
  errors, or textual response.
- Prototype the boundary only for producers where the native identity and
  terminal outcome can both be proven.

#### E-Task-4: remote calculation proof

- **Status:** Blocked.
- **Evidence:** [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json) retained fresh-real run `task-20260902-phase4-1`; E-Task-0/E-Task-1 classification primitives reconciled, ordered proof `25b49ac6e8d0c27b` and terminal proof `42fd32783f1eb9e0` are bound, and both `authoritative_operation_candidate_population=0` and `terminal_candidate_population=0`.
- **Proposal:** Do not calculate task outcomes without authoritative operation and terminal candidates.

Deliver immutable task/tool candidates and execute:

- Explicit user-friction rate;
- Correction timing & recovery;
- Tool failure fingerprints;
- Failure recovery;
- Repeated attempts;
- Command churn;
- Tool loops;
- Sandbox friction;
- Quality-gate bypass.

Compare task, call, group, target, cycle, sequence, recovery, percentile, and
unknown counts against independent oracles.

### Task and tool exit gate

- Canonical-now trace/task/activity calculations have individual remote/oracle
  proofs for the Phase 1E availability wave.
- M3–M11 have a producer-by-measure matrix covering `omp`, `codex-cli`, and
  `codex-app-server`.
- Producer, surface, namespaced session, task, operation, and project lineage
  reconcile in every remote result.
- Complete ordering and observable denominators conserve their source
  populations.
- Every recovery or success value depends on an explicit outcome; otherwise the
  corresponding field remains gated.

## Phase 5: finding, practice, rule, proposal, and intervention closure

This phase is application-owned. It consumes proven canonical activity,
ordered-task, and terminal-outcome projections; it does not add producer facts
to lifecycle adapters.

### Recurrence and intervention experiments

#### E-Recurrence-0: Canonical-now M13/M16 Wave A

- Project current canonical activity identity, source time, producer, project,
  detector/fingerprint, and allowlisted normalized target fields.
- Prove Scope recurrence (M13) distinct tasks/days/projects/producers for
  targets occurring across at least two canonical tasks.
- Prove Project concentration (M16) activity distribution, attributed
  denominator, top-project concentration, and unresolved population.
- Deliver and query the exact compacted two-panel Wave-A manifest without local
  SQLite joins.

- **Status:** Completed — Blocked.
- **Evidence:** [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json) retained the canonical fresh-real run with three privacy-allowlisted M13 audit primitives, no eligible canonical activity in the retention-safe two-day window, three ambiguous durable-task routes, and exact remote/oracle equality. The retained phase5-2 attempt stamped all 17 historical M16 activities at their exact integer source nanoseconds and delivered all 59 corrected primitives, but only 42 remained queryable: remote retention removed the 17 historical source-timestamp events. Exact-ID verification failed and no result events were emitted. Latest-version ties now produce typed `Failed` evidence without an M16 calculation. The aggregate experiment remains `Blocked`; neither an empty current population nor the privacy-invalid phase5-1 audit establishes M13/M16.
- **Proposal:** Add durable canonical task identity and a retention-compatible immutable canonical-activity projection before calculating M13/M16 remotely.

#### E-Recurrence-1: immutable finding projection

- Project the existing finding ID, version, detector, fingerprint, project,
  occurrence count, canonical task count, `Europe/London` calendar-day count,
  first/last seen, explicit seven-day evidence-window bounds, state, and
  deterministic event ID.
- Reconcile globally latest finding versions to durable local state.
- Prove both M14 promotion alternatives at exact occurrence/task/day
  thresholds.
- Prove `Europe/London` local-midnight, seven-day boundary, spring-forward, and
  fall-back cases against an independent calendar oracle.
- Execute Actionable repeated failures remotely.

- **Status:** Completed — Blocked.
- **Evidence:** [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json) retained three privacy-allowlisted E-Recurrence-1 primitives and exactly reconciled the remote result to the local oracle. The live audit found 6 durable findings, 17 immutable membership rows, 17 globally latest activity-version rows, and no duplicate, orphan, supersession-cycle, or ambiguous-latest contradiction. It also proved that durable finding rows lack explicit evidence-window bounds and immutable membership lacks canonical task identity. Pure reducer coverage established both exact M14 promotion alternatives and seven `Europe/London` local-midnight days across ordinary, spring-forward, and fall-back windows; that reducer coverage cannot make the live projection `Proven`.
- **Proposal:** Emit application-owned immutable finding versions with explicit evidence-window bounds and exact canonical-task membership.

#### E-Recurrence-2: successful-practice candidate

- Use complete ordered operations and explicit successful terminal outcomes.
- Define a canonical task-class contract and versioned enforcement-owner
  registry.
- Fingerprint sequences only after target normalization and ordering are proven.
- Apply the exact recurrence thresholds and calculate support/success over
  comparable observable tasks.
- Execute Uncodified successful practices remotely.

- **Status:** Completed — Blocked.
- **Evidence:** [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json) retained 12 privacy-allowlisted producer/count-kind E-Recurrence-2 authority-gap primitives and exactly reconciled the remote result to the local oracle. The authoritative successful-practice candidate population is unavailable and is not encoded as numeric zero. Canonical activity and finding aggregates were not treated as complete ordered operations, explicit terminal success, canonical task class, or versioned enforcement-owner authority.
- **Proposal:** Add immutable ordered-operation, explicit terminal-outcome, canonical task-class, comparable-population, and versioned enforcement-owner projections before promoting a successful-practice candidate.

#### E-Recurrence-3: rule applicability and skill adherence

- Define a versioned rule with deterministic trigger, required action,
  observability condition, and applicability population.
- Prove required-action observability from authoritative events; mentioned or
  selected skill counts alone are insufficient.
- Project applicable, satisfied, and unknown-observability states.
- Execute Skill adherence with numerator/denominator conservation.

- **Status:** Completed — Blocked.
- **Evidence:** [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json) retained 12 privacy-allowlisted producer/registry E-Recurrence-3 audit primitives and exactly reconciled the remote result to the local oracle. Exact SQLite schema inspection found 0 complete registry schemas and 3 schema gaps; it did not assert unseen zero rule, applicability, or violation populations. Static guidance and detector findings were not treated as machine authority. The reducer conserves satisfied, unsatisfied, and unknown-observability populations only when exact required-action or required-action-absence observations bind to globally latest versioned rules, but no live authoritative registry exists.
- **Proposal:** Add an application-owned versioned machine rule, applicability, required-action observation, and violation registry.

#### E-Recurrence-4: intervention and enforcement-tier audit

- Project practice/finding identity and version, ordered tier evaluation,
  selected tier, rejection reason for every earlier tier, approval/application
  state, project, evidence time, explicit application time, and recurrence
  result.
- Require exactly one selected tier per candidate.
- Reconcile the event to validated proposal history and established-tool audit
  state.
- For M15, select equal-length pre/post windows around application time, match
  comparable observable-task exposure by finding fingerprint/project/producer/
  model/task class, and retain raw occurrence counts, task denominators, and
  window bounds.
- Calculate relative change only when the pre-rate is positive; otherwise
  return the canonical non-calculable state.
- Suppress comparative interpretation when exposure or material task mix is
  not comparable and never use causal language.
- Execute Enforcement-tier audit and pre/post recurrence remotely against
  independent window, exposure, and arithmetic oracles.

- **Status:** Completed — Blocked.
- **Evidence:**
  [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json)
  retained nine privacy-allowlisted E-Recurrence-4 primitives over 6 audited
  population labels and exactly reconciled the remote result to the local
  oracle. Exact integer UTC epoch arithmetic produced a bounded two-day finding
  count of 0; it was not reported as the number of audited population labels.
  All 3 required projections remained absent: canonical practices, intervention
  applications, and recurrence results. Existing proposal workflow tables were
  not treated as a complete intervention projection. Pure reducer coverage
  enforces ordered-tier rejection, exact application-time window anchoring,
  equal duration, full exposure-identity matching, raw counts and denominators,
  nonpositive-pre-rate handling, comparability suppression, and immutable
  finding/practice lineage; no live candidate satisfied those authorities.
- **Proposal:** Add immutable practice, intervention-application, and recurrence-result projections linked to validated proposal history.

### Recurrence and intervention exit gate

- The Canonical-now M13/M16 Wave-A calculations reconcile remotely before later
  recurrence projections.
- M12–M18 have application-owned immutable schemas and exact remote calculation
  proofs or explicit blockers.
- M14 proves seven-day `Europe/London` window conversion, DST/local-midnight
  boundaries, and both exact occurrence/task/day promotion alternatives.
- M15 proves explicit application time, equal duration, exposure matching, raw
  counts/denominators/bounds, nonpositive-pre-rate behavior, and comparability
  suppression.
- No intervention result loses finding/practice lineage, project, version,
  evidence window, approval, application, or comparison state.
- No producer adapter is changed for application-owned policy or workflow facts.

## Phase 6: consolidate viability decisions

**Status: Completed with a cleanup incident.** Phase 6 records the completed
consolidation and cleanup correction; it does not authorize a production
cutover or any later recurrence projection.

### Consolidation outputs

1. **Complete Appendix matrix.** [`dashboard-prototype-proof-matrix.json`](dashboard-prototype-proof-matrix.json)
   contains all 129 Appendix rows: 127 are `Blocked` and two are static `Not
   applicable` with normative capability reasons. `Blocked` and `Not
   applicable` rows retain `evidence_bundle: null`. There is no row-level
   `Proven` classification: E-Attribution-1 is experiment-level `Proven`, but
   its retained evidence does not provide the per-row `EvidenceBundle` and
   event-id inputs needed to promote an Appendix row.
2. **Experiment status and evidence closure.** Every experiment has a recorded
   status, immutable evidence reference, and implementation proposal in
   [Execution progress](#execution-progress). E-Attribution-1 is the sole
   `Proven` experiment; every other experiment is `Blocked`.
3. **Identity reconciliation.** The cleanup evidence records 1,298 unique local
   delivered identities and 1,281 remote identities before cleanup. Seventeen
   historical exact-source recurrence IDs were unavailable because of remote
   retention; this is a retained proof boundary, not a population match.
   Thirty-eight duplicate remote rows were coalesced by immutable identity.
4. **Proof-validity boundary.** Reconciliation and cleanup do not convert an
   experiment result into an Appendix proof. A row is proven only with its
   required bounded source, reducer, immutable delivery, remote calculation,
   independent oracle, and row-bound `EvidenceBundle`/event IDs. The canonical
   experiment evidence remains
   [`pipeline-20260901T213721Z.json`](../experiments/dashboard_prototype/evidence/pipeline-20260901T213721Z.json),
   [`attribution-20260901-phase2-2.json`](../experiments/dashboard_prototype/evidence/attribution-20260901-phase2-2.json),
   [`request-20260901-phase3-7.json`](../experiments/dashboard_prototype/evidence/request-20260901-phase3-7.json),
   [`task-20260902-phase4-1.json`](../experiments/dashboard_prototype/evidence/task-20260902-phase4-1.json), and
   [`recurrence-20260902-phase5-4.json`](../experiments/dashboard_prototype/evidence/recurrence-20260902-phase5-4.json).
5. **Cleanup correction and retained incident evidence.**
   [`consolidation-cleanup-20260902.json`](../experiments/dashboard_prototype/evidence/consolidation-cleanup-20260902.json)
   records the immutable initial reconciliation summary: 19 selectors, 1,298
   unique local delivered identities, 1,281 remote identities present, 17
   historical exact-source recurrence identities absent by retention, 38
   duplicate remote rows, and 1,105 remote rows without the required stored
   namespace. The initial cleanup predicate incorrectly treated the omitted
   namespace as deletable and the resulting mutation reduced the selected
   remote population to zero. Those 1,105 unmatched rows cannot be restored
   safely from retained evidence and the mutation is not approved cleanup.
   The corrected operation now requires the exact stored namespace and fails
   closed when omitted-namespace rows remain.
6. **Immutable local evidence retained.** All 1,298 local delivered identities
   remain. The production immutable no-delete guard prohibits their deletion
   and was not bypassed.
7. **Reducer-grouped implementation handoff.** Any future implementation must
   be organized by canonical reducer, not dashboard panel:

   | Canonical reducer | Accepted handoff | Blocked handoff |
   | --- | --- | --- |
   | Producer-native lifecycle and latest-version attribution | E-Attribution-1 proves experiment-level exact producer/native-session authority and common latest-version P7/P8 population. | Bind its retained `EvidenceBundle` and event IDs to each Appendix row; E-Attribution-2 through E-Attribution-5 remain blocked. |
   | Pipeline snapshot, schedule, lag, integrity, ledger, and outbox | None. | Supply each pipeline experiment's exact durable source, bounded scan/reconciliation, and required remote/oracle proof. |
   | Logical request, attempt, provider, model, and token | None. | Add authoritative request/attempt lifecycle fields and a conserving reducer with required namespaced lineage. |
   | Ordered task, tool, and terminal outcome | None. | Add authoritative operation routes, task identity, lifecycle fields, and terminal outcome boundary before calculation. |
   | Finding, practice, rule, proposal, and intervention | None. | Add durable task/activity identity and the application-owned immutable policy/workflow projections required by E-Recurrence-0 through E-Recurrence-4. |

### Final decision table

| Closure stream | Accepted outcome | Blocked outcome / required result before full implementation |
| --- | --- | --- |
| Pipeline snapshot, schedule, lag, integrity, ledger | None | Exact durable-state reconciliation plus separate remote/oracle gates for P1–P3, multi-scan P4, P10 snapshot/detail, P11, and full P12 |
| Project and lifecycle attribution | E-Attribution-1 is proven at experiment level: exact producer-native identity and common latest-version P7/P8 population. | Per-row `EvidenceBundle`/event-id binding, plus separate P5, P6, P7/P8, and P9 transition/all-version-rate gates; E-Attribution-2 through E-Attribution-5 remain blocked. |
| Logical request/attempt/provider/model/token | None | All-supported-producer field matrix, mandatory producer/surface/namespaced-session lineage, one conserving reducer, terminal-grace policy, immutable projection, and exact R1–R8/M1–M2 proof |
| Ordered task/tool/terminal outcome | None | Canonical-now availability proof followed by complete per-producer ordering/outcome, mandatory lineage, one conserving reducer, immutable projection, and exact M3–M11 proof |
| Finding/practice/rule/intervention | None | Canonical-now M13/M16 Wave A followed by immutable lineage, M14 London-calendar proof, M15 exposure/window proof, and exact M12–M18 results |

The original Phase 5 exit gate remains unchanged and unmet: The Canonical-now
M13/M16 Wave-A calculations reconcile remotely before later recurrence
projections.

## Authoritative proof follow-on

The
[Authoritative Proof Closure Plan](authoritative-proof-closure-plan.md)
owns the recommended actions for the 23 `Blocked` experiments and the blocked
row-level obligations attached to E-Attribution-1. It applies the recorded
unblock proposals without changing any Phase 0–6 classification, retained
evidence, or exit gate.

## Definition of prototype completion

The prototype program is complete when every Appendix A calculation and control
has been classified across all supported producers as:

- `Proven` with bounded real source evidence, deterministic centralized
  reduction, immutable remote delivery, and matching SigNoz and independent
  oracle results;
- `Not applicable` with a normative capability reason; or
- `Blocked` with the exact missing authoritative native or application-owned
  boundary and its implementation owner.

No production dashboard page or panel may treat `Blocked`, `Failed`, `Not run`,
historical-only, synthetic-only, or local-only evidence as proven.
