# React and SigNoz Companion Implementation Plan

## Purpose

Implement the five-view Agent Introspection dashboard experience as a
repository-owned React companion application backed by the local SigNoz data
plane. The implementation must reproduce the approved visual hierarchy,
controls, calculations, states, and evidence paths without treating the visual
mock's illustrative values as data.

This plan is the route to full implementation. It depends on:

- the canonical measurement, calculation, control, and visual contract in
  [Dashboard Measure v2](dashboard-measure-v2.md);
- the proof gates and experiment results in the
  [Gap-Closure Prototype Experiment Plan](gap-closure-prototype-experiment-plan.md).

The measurement document remains authoritative when this plan, an experiment,
or the ephemeral visual mock disagrees with it.

## Target outcome

Deliver five separate routes, not tabs:

1. `01 · Pipeline health` — P1–P12;
2. `02 · Provider health` — R1–R8;
3. `03a · Model usage · Usage & interaction` — M1–M4;
4. `03b · Model usage · Tool execution` — M5–M11;
5. `03c · Model usage · Recurrence & interventions` — M12–M18.

Every visible result must come from a complete canonical remote population in
SigNoz. `Requires projection`, `Local only`, and `Deferred` measures remain
absent until the corresponding experiment and production projection gates are
complete.

## Architectural decisions

### Application boundary

- Build a React and TypeScript companion application under `dashboard/`.
- Use five URL-addressable routes with shared navigation and filter semantics.
- Keep SigNoz as the telemetry store and query data plane.
- Put query execution behind a loopback-only, typed server boundary; never send
  ClickHouse credentials, SigNoz authentication material, or unrestricted SQL
  to the browser.
- Reuse the repository's canonical reducers and identities. The React
  application must not reconstruct lifecycle intervals, logical attempts,
  ordered task outcomes, findings, or intervention state independently.
- Do not embed or fork the SigNoz frontend. Link to SigNoz evidence when a
  stable supported route exists; otherwise retain visible filters, time range,
  and short evidence identities for manual reproduction.

### Contract registry

Create one typed measure registry that records, for every P, R, and M measure:

- measure ID and exact canonical title;
- route and section;
- question, cohort, timestamp domain, dimensions, and unit;
- exact selected-range membership operator and any independent interval
  containment operator;
- evaluation-time, policy-identity, all-version-loading, and comparison-window
  requirements;
- projection gate and forward-validity boundary;
- query identifier and typed result schema;
- numerator, denominator, percentile sample, and integrity invariants;
- applicable capability predicate;
- supported filters and exact filter semantics;
- result-state renderer;
- evidence table or drill-down contract.

The registry references the canonical wording in
[Dashboard Measure v2](dashboard-measure-v2.md); it does not introduce a second
formula or label for the same measure.

### Query and state boundary

- Use allowlisted query identifiers with typed parameters rather than browser
  supplied SQL.
- Execute each measure's declared range rule exactly. Canonical activity source
  time uses `source_time > start AND source_time <= end`; lifecycle containment
  independently uses `interval_start <= source_time < interval_end`. Scan
  completion, request terminal, trace source, task source, finding evidence,
  and intervention domains retain their own registry operators.
- Test events exactly at every selected-range and lifecycle-interval endpoint;
  never route all measures through one generic half-open predicate.
- Recompute `All` from canonical identities; never aggregate displayed
  percentages or percentiles.
- Return population metadata with every result: numerator, denominator, sample
  count, time basis, exact range operator, selected dimensions, projection
  version, policy identity, evaluation time, and query time when applicable.
- Return one explicit result state: `Loading`, `Data`, `No data`,
  `Not applicable`, `Unavailable`, `Integrity failure`, or
  `Query/system error`.
- Fail closed when deterministic identity, latest-version, conservation,
  timestamp, range-boundary, interval-containment, policy, or capability
  invariants fail.
- Preserve filter state in the URL only after exact round-trip and invalidation
  behavior is browser-proven.

### Design system boundary

Promote the approved mock's reusable visual language into repository-owned
assets:

- dark surface, panel, border, text, muted, purple, green, amber, red, blue, and
  cyan design tokens;
- 12-column dashboard grid, responsive stacking, spacing, radius, shadow, and
  typography tokens;
- sidebar and mobile navigation;
- page heading, eyebrow, subtitle, filter bar, scope readout, section label,
  panel header, metric, status pill, state notice, table, legend, and evidence
  primitives;
- semantic outcome color paired with text and, where supported, icon or line
  pattern;
- visible focus, keyboard-reachable controls and rows, units, deterministic
  sorting, and 200% zoom support.

The visual mock at `/tmp/show-me-signoz-dashboard-mocks.html` is a design
reference only. No implementation may depend on that ephemeral file.

## Delivery sequence

## Phase 0: contract and proof readiness

### Phase 0 work

1. Convert Appendix A of
   [Dashboard Measure v2](dashboard-measure-v2.md#appendix-a-dashboard-mock-control-data-readiness)
   into a checked implementation coverage manifest keyed by measure or control.
2. Record the current proof state from the
   [Gap-Closure Prototype Experiment Plan](gap-closure-prototype-experiment-plan.md)
   and the [proof matrix](dashboard-prototype-proof-matrix.json): only
   E-Attribution-1 is experiment-level `Proven`; all other experiments are
   `Blocked`. The Appendix A matrix has 129 complete rows: 127 `Blocked` and
   two static `Not applicable`, with `evidence_bundle: null` on every
   `Blocked` or `Not applicable` row. E-Attribution-1 remains row-level
   `Blocked` because its retained aggregate proof has no per-row
   EvidenceBundle/event-id inputs.
3. Record a producer-by-measure row for `omp`, `codex-cli`, and
   `codex-app-server`, including normative capability reason, canonical remote
   event family, schema, forward boundary, exact query, and oracle.
4. Freeze the five routes, navigation order, exact titles, shared control
   semantics, seven result states, and omission rules.
5. Prove the server-side SigNoz query transport with one bounded read-only query
   that returns no sensitive content.
6. Define fixture-free contract tests from bounded immutable event identities;
   synthetic transport probes cannot prove semantic calculations.

### Phase 0 exit gate

- Every Appendix A row has one owner, projection gate, query owner, route, and
  result-state contract.
- No page phase can schedule a gated measure before its experiment is `Proven`
  and its production projection is remotely deployed.
- Query transport is loopback-only and browser clients receive no credentials
  or unrestricted query capability.

### Phase 6 consolidation record

The accepted implementation handoff is grouped by reducer, not panel:
`experiment_pipeline_reducer` consumes
`pipeline-20260901T213721Z`; `experiment_attribution_reducer` consumes
`attribution-20260901-phase2-2`; `experiment_request_attempt_reducer` consumes
`request-20260901-phase3-7`; `experiment_ordered_task_reducer` consumes
`task-20260902-phase4-1`; and `experiment_recurrence_reducer` consumes
`recurrence-20260902-phase5-4`. These canonical records and the proof matrix
are handoff evidence, not production authority; a reducer may be implemented
only when its row-level gate is `Proven` with its required EvidenceBundle.

The cleanup record is
[`consolidation-cleanup-20260902.json`](../experiments/dashboard_prototype/evidence/consolidation-cleanup-20260902.json):
19 selectors reconciled 1,298 unique local delivered identities, 1,281 remote
identities present, 17 historical exact-source recurrence identities absent by
retention, and 38 duplicate remote rows. Of the remote identities, 1,105
omitted the required stored namespace. The initial predicate incorrectly
deleted those unmatched rows and reduced the selected remote population to
zero; retained evidence cannot restore them safely, so that mutation is not
approved cleanup. The corrected operation requires exact stored namespace,
run ID, event family, and event IDs and fails closed on namespace omission.
The 1,298 local outbox identities remain under the production immutable
no-delete guard.

### Authoritative proof follow-on

The
[Authoritative Proof Closure Plan](authoritative-proof-closure-plan.md)
owns the work required to open individual row-level gates. It does not authorize
implementation of a blocked route, panel, projection, or production cutover.

## Phase 1: shared site foundation and Pipeline health

Pipeline health comes first because the scanner, schedule, ledger, projection,
and outbox are application-owned. It establishes the shell, data access,
calculation, state, accessibility, and visual patterns needed by every later
route.

### Phase 1A: application shell

1. Scaffold the React and TypeScript application in `dashboard/` using the
   repository's Bun toolchain.
2. Implement five URL-addressable route definitions but expose only routes with
   at least one complete remotely available measure.
3. Build the shared desktop sidebar, mobile navigation, top bar, page heading,
   filter bar, scope readout, section labels, panel grid, and evidence layout.
4. Add error containment at route and panel boundaries without translating
   query failures into empty data.
5. Add shared time-range parsing, UTC rendering, refresh, request cancellation,
   stale-response rejection, and loading stability.

### Phase 1B: design and result-state system

1. Create design tokens and primitives from the approved mock and visual
   addendum.
2. Implement all seven result states with the exact canonical wording.
3. Implement table, one-row summary, percentile table, trend, stacked bar,
   diagnostic table, and evidence table primitives.
4. Enforce units, numerator/denominator display, percentile `n`, low-sample
   behavior, top-50/latest-100 disclosure, and deterministic ordering.
5. Verify keyboard navigation, visible focus, color-independent status,
   responsive stacking, and 200% zoom.

### Phase 1C: typed query surface

1. Implement allowlisted query endpoints and typed response schemas for the
   available Pipeline health measures.
2. Centralize latest-version selection, deduplication by deterministic
   `event.id`, range predicates, capability evaluation, and integrity checks on
   the server.
3. Include query provenance and population metadata in every response.
4. Add an independent oracle for each calculation; UI tests assert rendered
   results against the oracle rather than reproducing query code.

### Phase 1D: Pipeline health route

Implement the exact manifest and section order from the visual addendum:

- scan completion/extraction-bound health: P1, P2, P3, P10, and P4;
- source-time capture and attribution: P5, P6, P7, P8, and P9;
- P6 only after lifecycle interval-containment, first-source, delay percentile,
  matched-session, and negative-skew remote/oracle proof;
- P9 transition count only after immediate-valid-predecessor and deterministic
  transition proof; its rate remains absent until the independent all-version
  denominator is proven;
- P10 snapshot and event-level delivery detail as separate widgets and gates;
- P11 only after immutable rejection/integrity projection;
- P12 only after the full redacted maintenance projection;
- Canonical scan evidence only after canonical snapshot projection.

Controls and calculations covered by Appendix A:

- Time range;
- Latest pipeline snapshot;
- Scan outcomes;
- Freshness & cadence;
- Scan workload & duration;
- Source lag by producer;
- Producer lifecycle correlation;
- Attribution coverage;
- Attribution diagnostics.

Before a projection gate opens, its panel is absent and the dashboard
description names the omitted measure and required projection. After a
recorded availability boundary, an incomplete or unqueryable deployed contract
renders `Unavailable`; it is never reclassified as `Not applicable`.

### Phase 1 exit gate

- Every remotely available Pipeline health calculation matches its independent
  bounded oracle in SigNoz.
- P4 reconciles a bounded multi-scan population with distinct extraction bounds
  and scan-completion range membership.
- P6 reconciles interval containment across selected-range boundaries,
  first-source selection, p50/p95/`n`, matched sessions, and negative skew.
- P9 reconciles immediate valid predecessor transitions by deterministic
  resolved-version event ID; the optional rate separately proves its
  selected-range-independent all-version denominator.
- P10 snapshot counts and immutable delivery-attempt detail pass separate
  schema/query/oracle gates.
- P12 reconciles check result/age, backup age, database/WAL bytes, free-page
  ratio inputs/result, migration state, database identity, check type, and
  runtime host.
- Every Appendix A Pipeline health row has a verified control, calculation,
  state, and evidence path or a recorded omission gate.
- Browser verification covers desktop, mobile, 200% zoom, loading, empty,
  not-applicable, unavailable, integrity-failure, and query-error paths.
- No panel reads local SQLite from the browser or query service.
- The shared foundation is reusable without page-specific copies of filters,
  states, calculations, or styling.

### Phase 1E: Canonical-now Model usage availability wave

Phase 6 classification holds this availability wave: its prerequisite rows are
`Blocked` in the [proof matrix](dashboard-prototype-proof-matrix.json), so no
Model usage route or panel is schedulable. E-Attribution-1 is only an
experiment-level `Proven` baseline and does not open a row-level dashboard
measure without its per-row EvidenceBundle/event-id inputs. Keep every
otherwise described panel absent rather than treating activity counts or the
baseline as production authority.

## Phase 2: Provider health

Phase 6 classification holds Provider health: the request/attempt prerequisites
from `request-20260901-phase3-7` are `Blocked` in the
[proof matrix](dashboard-prototype-proof-matrix.json). Start only after the
immutable request/attempt projection is remotely available from its recorded
forward boundary and a producer-by-measure matrix covers `omp`, `codex-cli`,
and `codex-app-server`.

### Provider health work

1. For every R1–R8 dependency and supported producer/surface, record exactly one
   state: `Proven`; static `Not applicable` with its normative capability
   reason; or `Blocked` with the missing authoritative field/boundary.
2. Keep a panel absent while any applicable supported-producer dependency is
   `Blocked`. After a deployed availability boundary, render `Unavailable` with
   the missing producer/field/boundary instead of silently narrowing the
   denominator.
3. Add exact `Provider`, `Model role`, and `Model` controls after global time.
4. Implement the canonical dependent-option behavior and its documented
   independent requested/response control form only if installed-version
   browser proof requires it.
5. Implement R1–R8 and Provider evidence in the exact manifest order.
6. Keep logical-request, attempt, latency-phase, retry, streaming, model-role,
   and accounting populations separate.
7. Preserve explicit unknowns and fail closed on missing or conflicting
   provider, producer, surface, or namespaced native-session identity.
8. Enforce final-attempt terminal-time membership and load all attempts for the
   selected logical requests.
9. Bind R8 and every unknown-outcome column to a versioned provider-keyed
   terminal-grace policy identity and explicit query evaluation time.

### Provider health Appendix A coverage

- Time range;
- Provider;
- Model role;
- Model;
- Logical request outcomes;
- Outcome summary;
- Provider error composition;
- Latency phases;
- Retry behavior;
- Requested → response model conformance.

### Provider health exit gate

- R1–R8 match independent request/attempt oracles for every applicable
  supported producer; static capability exclusions retain their normative
  reasons.
- Equal native session IDs from different producers remain separate, while
  conflicting producer/surface lineage withholds the affected aggregate.
- R8 matches immediately-before, exactly-at, and immediately-after grace
  boundary oracles using the returned policy identity and evaluation time.
- Provider/model selection, impossible combinations, unknown model, `All`, and
  low-sample behavior are browser-proven.
- No `Blocked` supported-producer field becomes `Not applicable` or disappears
  from a visible denominator.

## Phase 3: Model usage · Usage & interaction

Phase 6 classification holds this route: the request and task prerequisites
from `request-20260901-phase3-7` and `task-20260902-phase4-1` are `Blocked` in
the [proof matrix](dashboard-prototype-proof-matrix.json). Enrich the Phase 1E
route measure by measure only after its row-level authority opens; do not wait
for unrelated request-accounting or task-outcome contracts to expose an already
complete owned calculation.

### Usage and interaction work

1. Reuse the shared exact `Project` control after global time.
2. Add M1 and request-accounted M2 only after project-attributed logical
   requests and accepted accounting-record identities are remotely proven.
3. Retain trace-accounted M2 and M3 from the Canonical-now wave with their
   separate source-time and capability contracts.
4. Add M4 timing columns when complete task ordering is proven; add recovery
   columns only after explicit terminal task outcomes are proven.
5. Keep request/accounting-time, trace-source-time, and task-source-time
   sections separate.
6. Use only direct supported friction signals in the observable-task
   denominator.
7. Implement Friction evidence with approved fields and short identities.

### Usage and interaction Appendix A coverage

- Time range;
- Project;
- Model calls & provenance;
- Request-accounted token pressure;
- Explicit user-friction rate;
- Model calls over time;
- Correction timing & recovery.

### Usage and interaction exit gate

- Every visible M1–M4 calculation or individually gated column matches its
  independent request, accounting, trace, or task oracle.
- Producer, surface, namespaced native-session identity, task identity, and
  canonical project lineage reconcile between remote events and each oracle.
- Project `All`, resolved, Unresolved, empty, and conflicting-identity behavior
  is verified.
- Each deployed availability-wave manifest compacts enabled panels upward and
  matches the visual addendum.

## Phase 4: Model usage · Tool execution

Phase 6 classification holds this route: the ordered-task prerequisites from
`task-20260902-phase4-1` are `Blocked` in the
[proof matrix](dashboard-prototype-proof-matrix.json). Enrich the Phase 1E
count-only route only when each ordered-operation, denominator, recovery,
cycle, sequence, or terminal-outcome dependency is remotely proven.

### Tool execution work

1. Reuse global time and the exact shared `Project` control.
2. Promote M5 and M7–M11 columns independently from count-only to their full
   contracts; add M6 only after complete ordered calls and observable task ends
   are proven.
3. Keep call, operation group, task, target, cycle, and complete-sequence
   populations explicit.
4. Require complete task/tool ordering before recovery, loop, or bypass
   calculations.
5. Keep static unsupported producer capabilities `Not applicable`; retain
   unknown terminal task outcomes outside success/failure denominators without
   removing their tasks from applicable count populations.
6. Require producer, surface, namespaced native-session identity, task
   identity, and canonical project lineage on every task/operation projection.
7. Implement Tool evidence with allowlisted redacted operation, target,
   fingerprint, outcome, and short identity fields.

### Tool execution Appendix A coverage

- Time range;
- Project;
- Tool failure fingerprints;
- Failure recovery;
- Repeated attempts;
- Command churn;
- Tool loops;
- Sandbox friction;
- Quality-gate bypass.

### Tool execution exit gate

- Every visible M5–M11 calculation or individually gated column matches its
  independent ordered-operation oracle.
- Population conservation is proven from calls through groups and tasks.
- Equal native session IDs from different producers stay separate; conflicting
  producer/surface/task lineage withholds the affected aggregates.
- Full ordering, terminal outcome, producer capability, redaction, and table
  limits are verified with bounded remote evidence.

## Phase 5: Model usage · Recurrence & interventions

Phase 6 classification keeps this route absent: every M13/M16 row in the
[proof matrix](dashboard-prototype-proof-matrix.json) is `Blocked`. The
M13/M16 exit gate below is unchanged and unmet. Do not create, schedule,
enrich, or deploy a Phase 1E M13/M16 route or manifest until the row-level
gate opens with its required immutable evidence.

### Recurrence and interventions work

1. Reuse global time and the exact shared `Project` control after the route gate
   opens.
2. Create the Wave-A M13/M16 manifest only after both measures pass their
   individual row-level gates.
3. Add M14 only after the immutable finding projection and deterministic
   seven-day `Europe/London` calendar-window rule reconcile at threshold,
   local-midnight, and daylight-saving boundaries.
4. Add M15 only after explicit application time, equal-length windows,
   exposure-matched observable-task denominators, raw counts/window bounds,
   positive-pre-rate handling, and comparability suppression are proven.
5. Add M12, M17, and M18 only after their separate rule/applicability,
   ordered-successful-practice, and intervention/tier-audit contracts open.
6. Keep activity, finding, practice, proposal, intervention, and tier-audit
   identities and timestamp domains separate.
7. Use globally latest immutable finding/practice/intervention versions.
8. Retain explicit observability, applicability, owner, approval, application,
   comparison, and earlier-tier rejection states.
9. Never label pre/post recurrence as causal.
10. Implement Candidate/intervention evidence with redacted identity and state
    history only.

### Recurrence and interventions Appendix A coverage

- Time range;
- Project;
- Actionable repeated failures;
- Uncodified successful practices;
- Scope recurrence;
- Project concentration;
- Skill adherence;
- Enforcement-tier audit.

### Recurrence and interventions exit gate

- Every visible M12–M18 calculation matches an independent application-owned
  projection oracle.
- M14 proves exact occurrence/task/day thresholds over seven-day
  `Europe/London` windows, including local-midnight and daylight-saving cases.
- M15 proves equal pre/post duration, exposure matching, raw counts,
  observable-task denominators, bounds, positive-pre-rate handling, and
  comparability suppression.
- Findings, successful practices, rules, and interventions retain version,
  evidence window, project, state, and lineage.
- The full first viewport places actionable failures beside uncodified
  successful practices when both contracts are open; Wave A keeps M13/M16
  side-by-side before then.

## Phase 6: integrated hardening and release

### Integrated hardening work

1. Execute the complete Appendix A implementation coverage manifest.
2. Verify cross-route time and Project control consistency without merging
   distinct timestamp domains or populations.
3. Verify direct entry, refresh, stale-response rejection, deep-link behavior,
   and manual evidence reproduction.
4. Run browser visual comparisons against the approved hierarchy at desktop,
   mobile, and 200% zoom.
5. Run accessibility checks and keyboard-only journeys for navigation, filters,
   tables, evidence, and errors.
6. Exercise every result state through controlled real query conditions; do not
   hardcode UI states to pass acceptance.
7. Verify no prompt text, response text, command output, full session IDs,
   arbitrary attributes, credentials, or unrestricted SQL reach the browser.
8. Record the installed SigNoz version, query contracts, projection boundaries,
   and supported producer capability matrix.

9. Consume the accepted handoff by reducer using the Phase 6 consolidation
   record above; do not promote retained local outbox identities or
   cleanup-reconciled remote rows into production evidence.

### Release gate

- Every visible applicable producer/measure row is `Proven`, remotely projected,
  and independently reconciled; static `Not applicable` rows retain their
  normative capability reasons.
- Every Appendix A row is implemented, statically `Not applicable`, or
  intentionally gated with its unmet projection and affected producer named.
- All five routes satisfy each deployed availability-wave manifest and shared
  control contract.
- SigNoz and the companion application remain loopback-only.
- No illustrative mock value, local-only join, inferred identity, or missing
  outcome is presented as production evidence.

The current release gate is unmet: 127 Appendix A rows are `Blocked`, two are
static `Not applicable`, and E-Attribution-1 is only experiment-level
`Proven`. No production cutover or later recurrence projection is authorized
until each applicable row has authoritative remote evidence and its required
EvidenceBundle.

## Coverage and dependency matrix

| Route | Measures | Controls | Required experiment gate |
| --- | --- | --- | --- |
| `01 · Pipeline health` | P1–P12 | Time range | `experiment_pipeline_reducer`; `pipeline-20260901T213721Z` is `Blocked` |
| `02 · Provider health` | R1–R8 | Time range, Provider, Model role, Model | `experiment_request_attempt_reducer`; `request-20260901-phase3-7` is `Blocked` |
| `03a · Model usage · Usage & interaction` | M1–M4 | Time range, Project | `experiment_attribution_reducer`, `experiment_request_attempt_reducer`, and `experiment_ordered_task_reducer`; only E-Attribution-1 is experiment-level `Proven`, with no row-level proof |
| `03b · Model usage · Tool execution` | M5–M11 | Time range, Project | `experiment_ordered_task_reducer`; `task-20260902-phase4-1` is `Blocked` |
| `03c · Model usage · Recurrence & interventions` | M12–M18 | Time range, Project | `experiment_recurrence_reducer`; `recurrence-20260902-phase5-4` is `Blocked` and the M13/M16 gate remains unmet |

## Definition of complete implementation

The implementation is complete only when the companion application can execute
and render every applicable calculation and control in Appendix A across all
supported producers, using canonical immutable remote evidence, while applying
the exact route, visual, state, accessibility, identity, time, capability,
integrity, privacy, and omission contracts in
[Dashboard Measure v2](dashboard-measure-v2.md).
