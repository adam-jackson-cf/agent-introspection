# Dashboard Performance Improvement Plan

## Purpose and authority

Improve performance only by removing redundant execution work from existing dashboard-serving processes. Calculation semantics, canonical signals, qualification, and dashboard output and presentation remain fixed.

The scope constraint is:

> we are improving the performance of the processes that server the dash, not the changing what the dash should show and how its showing it.

Current authorization covers performance-only implementation changes and genuine
renewal of the twelve central application bindings. It does not authorize changes
to installed production packages, deployments, native proof rows, or proof policy.

The [canonical measurement contract](dashboard-measure-v2.md),
[proof register](dashboard-metric-proof-register.md), and
[proof matrix](dashboard-prototype-proof-matrix.json) remain authoritative.
Their measurement definitions and qualification requirements remain unchanged.
Only renewed central application evidence may be recorded under this plan;
excluded loading proposals below are not implementation tasks.

## Target outcome

For identical captured inputs, requested filters, evaluation time, and valid
qualification, an optimized implementation must return the same complete canonical
report and produce the same dashboard presentation. It may reduce only the time
and resources required to produce and deliver that result; it must not change a
formula, signal, population, or metric definition.

The existing independent `dashboard/` Bun package and Python calculation
ownership remain. Shared improvements apply to all five routes where their
request paths overlap. The expensive report queries currently belong to
Pipeline; do not claim equivalent query savings for the other four routes,
which currently have no attached measurements.

## Execution prerequisites

Every change to a Python file under `src/agent_introspection/` changes the
calculator identity because
[`pipeline_runtime.py`](../src/agent_introspection/pipeline_runtime.py) hashes
the entire package. The earlier **Keep Python unchanged** decision was therefore
a historical blocker: it prohibited Python optimization because existing central
bindings could not admit a new hash.

Current authorization permits Python implementation changes solely to remove
demonstrated redundant execution work, provided exact captured-input outcome
equivalence is established and genuine central application-binding qualification
evidence is renewed for the changed fingerprint before it serves Pipeline.
That authorization is limited to the twelve existing central bindings. It does
not permit native-row promotion, gate weakening, installed production-emitter
changes, metric-definition edits, or any other proof-policy change.

## Non-negotiable invariants

- Preserve all five routes, 55 widget positions, 23 Additional measurements,
  canonical labels, formatting, controls, information-button details, table
  ordering, existing pagination, chart rendering, and complete-panel canonical
  JSON access.
- Preserve formulas, canonical signals, populations, units, numeric kinds, exact
  values, numerators, denominators, sample counts, nulls, nanosecond identities,
  immutable payload validation, and calculation ownership. Python changes may
  remove redundant execution work only; no approximate replacement or changed
  numeric operation/order is allowed for an existing exact calculation.
- Preserve selected-range operators, interval boundaries, evaluation-time
  policies, measurement cutoff, all-version histories, latest-per-owner
  selection, chart bucket definitions, and existing display caps. Required
  history may extend outside the displayed report timeframe.
- Preserve twelve central application bindings for P1–P4, P10–P12, and
  Canonical scan evidence. P5–P9 and the other routes retain native qualification.
  All 129 native rows remain unchanged: 127 Blocked, 2 Not applicable, 0 Proven.
  Native evidence bundles remain null.
- Preserve canonical Appendix/temporal validation before application-binding
  validation, `.every(...)` deployment admission, and empty-deployment rejection.
  Never omit a required ownership or integrity check because its panel is hidden
  or blocked.
- Preserve the existing public response shape, complete report validation,
  exact expected panel inventory, and atomic application of a response. A
  partially received report is not a successful report.
- Preserve current loading, missing-data, transport-error, panel-error, and
  failed-refresh behavior. Clear the old response only for the current,
  non-aborted failed request; retain `Query failed — outcome unknown`.
  Superseded responses must not overwrite current state.
- Preserve loopback host/origin restrictions, allowlisted requests, supported
  range limits, output limits, and `no-store` API responses.

## Explicitly out of scope

- Visual redesign, CSS changes, new skeletons, progressive panel rendering,
  viewport-triggered loading, collapsed sections, new loading indicators,
  changed error wording, new visible diagnostics, or changes to how controls
  behave. Changes to `dashboard/src/App.tsx`, `dashboard/src/pipeline.tsx`,
  `dashboard/src/presentation.ts`, and `dashboard/src/styles.css` are not planned.
- Changing Refresh to advance relative ranges, changing URL/filter semantics,
  adding automatic refresh, or introducing automatic retries.
- New browser-facing streaming protocols, per-panel endpoints, cursor APIs,
  report sessions, or page payloads. Do not replace the existing whole-report
  contract with a different loading model.
- Changing the current 20-row UI pages, expanding a canonical latest-50
  population, removing display caps, changing sort order, changing chart
  resolution, or fetching only the first page as though it were the complete
  measurement population.
- Caching completed measurements across refreshes, stale-success fallbacks,
  time-to-live-based qualification, or skipping validation to reduce latency.
- New persistent workers, job services, external caches, materialized views,
  background precomputation, telemetry infrastructure, or permanent UI test
  frameworks.
- Production scheduling, installed packages, installed production emitters,
  production deployment, storage, telemetry configuration, maintenance, backups,
  scans, vacuum, scheduler pause, changes to the measurement cutoff, metric
  definitions, native qualification matrices, gates, or native-row status/promotion.
- Root Electron dependencies, unrelated refactors, staging, commits, pushes,
  worktree resets, or changes to existing evidence, screenshots, calculation
  scripts, backups, clones, wheels, or `.codegraph/`.
- Cross-request parsing caches, new read concurrency, and internal streaming or
  batching. These are deferred, not optional execution branches. Reconsider
  only through a separately reviewed plan after focused measurements establish
  the remaining cost and an equivalence analysis establishes safety.

## Evidence informing the order

These are investigation samples, not a latency distribution or service-level
commitment:

| Observation | Implication |
| --- | --- |
| Companion log reports a 10-second Bun connection timeout. Pipeline itself receives a 10-second subprocess allowance, after earlier request work. | Coordinate deadlines across the existing serving path; a larger timeout alone is not the optimization. |
| A live 24-hour report took 12.1 seconds, including 11 sequential queries and about 5.1 seconds outside query timers. | Reduce redundant query and Python processing work before changing delivery architecture. |
| The identical scan-snapshot query ran twice in that report. | Share one validated result within the same request and exact query scope. |
| Function profiling identified repeated lifecycle-manifest decoding and UUID validation as a major hotspot. | Reuse exact validated content without removing per-envelope, completeness, or conflict checks. Profiling overhead means its wall time is not a normal latency measurement. |
| Registry loading took 0.22 seconds; the independent health probe took 0.51 seconds. | Remove repeated registry work, but do not mistake it for Pipeline's principal cost. |
| A recorded response was 56,822 bytes. Simulating first-20-row table delivery reduced it to 51,406 bytes: 9.5%, with no calculation work removed. | Browser-facing pagination is not justified as the first timeout fix. This was serialization analysis of a recorded response, not a new live benchmark. |
| Identical native-query parameters returned additional trace rows between reads. | A date range or evaluation timestamp alone does not make live query results immutable or safe to reuse across refreshes. |

Relevant source boundaries:

- [`dashboard/server.ts`](../dashboard/server.ts): serial request stages,
  subprocess cleanup, deadlines, and complete-response assembly.
- [`dashboard/src/registry.ts`](../dashboard/src/registry.ts): repeated registry
  loading and authoritative qualification.
- [`pipeline_dashboard.py`](../src/agent_introspection/pipeline_dashboard.py):
  report orchestration and common evaluation scope.
- [`pipeline_attribution.py`](../src/agent_introspection/pipeline_attribution.py)
  and [`pipeline_projection.py`](../src/agent_introspection/pipeline_projection.py):
  shared snapshots, complete histories, and repeated reductions.
- [`pipeline_events.py`](../src/agent_introspection/pipeline_events.py):
  immutable-event and manifest validation.
- [`source.py`](../src/agent_introspection/source.py): buffered subprocess query
  output and source-row decoding.
- [`pipeline_delivery.py`](../src/agent_introspection/pipeline_delivery.py):
  complete-drain validation and totals before the display cap. Applying a SQL
  page limit before these checks would change correctness.

## Ordered implementation plan

### 1. Coordinate request deadlines and resource cleanup

Owner: the existing Bun server and dashboard-invoked query execution path.

1. Use the existing profiling evidence to select changes, with bounded read-only
   timing experiments where further measurement is needed. Do not add persistent
   diagnostics or telemetry features, change browser responses, or expose query
   rows, credentials, or private identifiers.
2. Set an explicit application deadline using measured supported-range behavior.
   Propagate the remaining budget through registry validation, the health probe,
   Pipeline execution, and dashboard backend queries. Give the HTTP transport
   enough headroom to return the existing error response before closing the
   connection. Do not disable deadlines or raise limits without evidence.
3. Check cancellation before spawning work; handle already-aborted requests.
   Stop and reap spawned processes on timeout, cancellation, output overflow,
   decoding failure, and other exceptional exits. Verify descendants and remote
   query termination rather than assuming that killing `uv` is sufficient.
4. Keep the production scheduler's query behavior unchanged. Any shared-client
   change must be scoped to dashboard execution and checked at all callers.

Exit: slow and aborted requests release resources and settle through the
existing response/state contract instead of unexplained connection resets.

### 2. Remove redundant request and registry work

Owner: `dashboard/server.ts` and `dashboard/src/registry.ts`.

1. Read and prepare registry inputs once per dashboard request. Keep the existing
   canonical and authoritative validation order, then apply the report's current
   implementation binding without a second full file-read/validator cycle.
2. Share equivalent work within the same request. Do not introduce cross-request
   measurement caching or coalesce reports with different evaluation semantics.
3. Preserve the independent health probe and its current visible effect. Do not
   remove, relabel, or convert its failure into a successful measurement state.

Exit: Pipeline no longer performs two full registry validation passes for one
request; gates and public responses remain equivalent for the same inputs.

### 3. Reduce Pipeline query and calculation overhead

Owner: the existing Python report orchestration, query adapters, and reducers.

1. Fetch and validate identical scan-snapshot inputs once per report, using the
   same range, evaluation time, and measurement cutoff for every consumer.
2. Decode exact repeated manifest content once where safe. Cache only within
   the request; include all validation-relevant content and counts. Check each
   immutable envelope and conflicting identity before reuse. Do not memoize by
   event ID alone or replace complete-history checks with latest-only reads.
3. Reuse already-derived cohort indexes, native identities, and scan reductions
   where their inputs and calculation rules are identical. Avoid repeated
   allocation and normalization without changing numeric operations or their
   ordering.
4. Keep the existing SQL projections, predicates, row limits, and ordering.
   Do not add aggregation, sampling, read concurrency, or a new calculation.
   Limit this phase to removal of demonstrated duplicate work, preserving
   attribution ownership, integrity, ledger selection, final deployment
   admission, every fixed signal, and required work for undisplayed panels.

Exit: exact comparison against identical captured inputs produces the same
complete report, while repeated snapshot reads and measured processing overhead
fall. Before the changed fingerprint serves Pipeline, renew genuine central
application-binding evidence for the existing twelve bindings; do not alter
native qualification or evidence policy.

### 4. Verify performance, equivalence, and qualification before release

Owner: the implementation integrator, using existing behavioral scenarios and
runtime verification tools. Run shared quality gates once after edits settle.

- Compare baseline and optimized complete reports against the same captured
  inputs, explicit evaluation time, filters, and qualification context. Preserve
  formulas, canonical signals, metrics, exact numeric kinds, rows and ordering,
  series and bucket values, reasons, provenance, and error classification. Live
  reads alone are not a reliable equality oracle because late arrivals can
  change the population.
- For authorized Python changes, treat implementation content hashes as expected
  execution-identity changes, not metric differences. Require genuine renewed
  central application-binding qualification evidence for the changed fingerprint
  and all twelve existing central bindings before serving it. Do not promote any
  of the 129 native rows, weaken a gate, change proof policy, or alter installed
  production emitters.
- Recover the existing mock-derived Pipeline layout with real qualified metrics,
  not placeholder values. Diagnose the current `Missing data` root cause from
  qualification and request evidence before attributing it to a timeout or hash.
  Verify the recovered surface retains its existing layout and Completion time
  section unchanged.
- Extend affected existing server, registry, and Pipeline behavioral scenarios.
  For deadline and cleanup changes, cover already-aborted and overlapping
  requests, backend stalls, malformed output, output overflow, failed refresh,
  and recovery. For reducer reuse, cover complete populations, duplicates,
  missing histories, current ownership, and unchanged error classification.
  Add coverage only where the relevant existing scenario does not protect the
  implemented change.
- Repeat comparable 24-hour and maximum-supported-range measurements for the
  changed paths. Record full-response latency, query count/time, processing
  time, and any resource consumption targeted by the optimization. Compare
  repeated runs; do not call one successful sample a p95 result. Do not add a
  separate cache or read-concurrency benchmark matrix for excluded features.
- Smoke the actual shared request path on all five routes when server or registry
  code changes. Verify the existing Pipeline presentation and affected
  interactions in the browser. Unchanged complete payloads and unchanged UI
  source are the primary presentation-parity evidence; a full desktop/mobile
  screenshot campaign is not planned. A presentation difference rejects the
  optimization rather than authorizing a UI change.
- Run the existing companion checks from `dashboard/`:
  `bun run check && bun run test && bun run lint && bun run format:check && bun run build`.
  For Python changes, run the relevant existing behavioral scenarios and the
  repository's applicable quality gates. Do not install root dependencies or
  weaken checks. Do not build concurrently with browser interaction.
- Leave the companion available and preserve existing evidence. Report measured
  improvements, unchanged-output evidence, and any release prerequisite that
  remains blocked.

## Executed Bun scope and historical evidence

The earlier executed scope was limited to the Bun companion.
`dashboard/server.ts` prepared the request registry once, applied a bounded
14.5-second application deadline with a 15-second transport deadline, and
directly terminated and reaped its child process on cancellation or deadline.
This did not claim remote-query termination.

The earlier **Keep Python unchanged** decision left Python request deduplication
and remote-query cancellation unimplemented. That constraint is historical, not
a current authorization blocker: current work may optimize redundant Python
execution work under exact captured-input equivalence and renewed genuine central
application-binding evidence for its changed fingerprint. The completed authorized
execution is recorded below.

The Bun server scenarios recorded 21 tests and 88 expectations. Four complete
captured responses compared exactly equal
([equivalence evidence](../.tmp/performance-serving-equivalence.json)). At that
time, a live all-five-route smoke returned HTTP 200 with all 55 widgets and 12
attached Pipeline measurements; repeated 1-day and 31-day requests took
10.357–11.668 seconds ([live evidence](../.tmp/performance-serving-live.json)).
These are historical bounded samples, not p95 results, a speedup against the
unmatched Python-only baseline, or proof of the current Pipeline recovery.
Dashboard `tsc` passed; the TypeScript language server failed to initialize.

Historical browser verification confirmed 12 measurements without horizontal
overflow, the existing `Rows 21–40` pagination transition, clearing on failed
refresh, and recovery to 12 measurements. The
[desktop screenshot](../output/playwright/serving-performance-desktop.png)
records the unchanged presentation at that time. A real HTTP deadline scenario
confirmed the existing complete 503 response arrives instead of a transport
reset; it does not establish the cause of the current `Missing data`.

## Executed Python scope and current recovery

The 2026-09-10 recovery first established that OrbStack was stopped, its Docker
socket was absent, and the calculator still matched the existing qualification.
Starting the existing OrbStack deployment restored healthy SigNoz transport on
the configured external storage. No stack recreation or configuration change was
needed.

The first isolated Python candidate passed exact report equivalence, quality
gates, and genuine renewal of the twelve bindings. Its activation nevertheless
returned zero attached measurements in a 13.988-second current-evaluation HTTP
request: healthy transport and historical qualification alone did not prove
current execution could finish within the deadline. That failed activation and
subsequent failed timing samples are retained, not counted as recovery.

The final isolated refinement removes only demonstrated redundant execution:

- `pipeline_dashboard.py`, `pipeline_projection.py`, and
  `pipeline_attribution.py` share successful, fully validated scan snapshots and
  existing cohort reductions within one report.
- `pipeline_events.py` reuses exact-content manifests and successfully validated
  canonical UUIDs within one lifecycle parse. Each envelope still checks its own
  count; malformed and duplicate identities remain errors.
- `pipeline_integrity.py` returns its already decoded physical envelopes only
  after all physical and complete-history integrity checks succeed.
  `pipeline_attribution.py` retains the separate identity-validation stage before
  schema and population-envelope validation.
- `source.py` validates sorted native identity arrays without repeated set/sort
  allocations and avoids redundant identity classification. Dashboard-only query
  deadlines and targeted remote cancellation leave the scheduler client unchanged.
- `dashboard/server.ts` launches `.venv/bin/python` directly, propagates the
  absolute deadline, and observes process-group disappearance after cooperative
  termination and bounded escalation. This replaces the `uv` launch that failed
  to deliver Python cleanup in the real cancellation experiment.

All reuse is request-local. SQL and parameter contracts, complete histories,
calculation ownership, and measurement definitions remain unchanged. Query count
falls from 11 to 10 by removing one identical validated snapshot request.
The 14.5-second application deadline and 15-second transport deadline remain
unchanged; no concurrency, streaming, batching, or cross-request caching was added.

### Exact equivalence and genuine qualification

The daily, maximum 31-day, and bounded central captures each produce an exactly
equal complete canonical report after removing only
`implementation.calculationSha256`. The normalized report hashes remain:

```text
daily:   b150dc674ccdc81472d6aa36ed6f533e4ce347aa3656bd1277f87d8042d3dc91
31-day:  a033f210072e20ab4cdec0e11f04cb49c5ac277744d8a328bfa25b758a82beb1
central: f77ba1d7dd0cf73c6197a6f7c47d804b0d620553745101d4cde38b1aae68a0ab
```

The final calculator fingerprint is
`7c2c5d488e120e868aaed0de6bdf5990ff7c8ad89456ff8fbba3379475b74ecd`.
Fresh bounded remote acquisition, retained fresh-real native provenance,
independent derivation of all twelve panels, the unchanged baseline, and all
eight adverse categories passed the unchanged authoritative matrix validator.
The matching source and matrix were activated together while the companion was
stopped. The installed deployment/projection fingerprint remains
`7287a225b1606d6e7a30ba9d288c2baeb219a2ec159b55e653f790490815b75e`.
All 129 native rows and non-binding policy remain exactly unchanged:
127 Blocked, 2 Not applicable, 0 Proven; native evidence bundles remain null.

Evidence is retained under
[the execution directory](../.tmp/performance-execution-1789044045613/) and
[the final renewal](../.tmp/performance-execution-1789044045613/current-evaluation/renewed-central/).
The refined Python package passed Ruff formatting/lint, mypy for 39 source files,
and all 1,056 tests. A separate comparison covered 5,602 native-array cases and
40 manifest cases, including values and error precedence. Companion TypeScript,
all 21 tests, lint, formatting, and build passed. Real abort and deadline
experiments first observed the owned ClickHouse query running, then established
zero remaining owned queries and absent Python and Docker processes.

### Measured execution and HTTP results

These are individual observed samples, not p95 results or latency distributions:

| Measurement | Daily samples (seconds) | 31-day samples (seconds) |
| --- | --- | --- |
| Earlier fixed-evaluation baseline, direct report | 16.962, 15.087 | 17.152, 18.237 |
| First candidate at that fixed evaluation, direct report | 9.986, 9.099 | 11.779, 11.110 |
| Final current evaluation, fresh-process direct report | 11.151, 10.057 | 11.724, 11.956 |
| Final activated HTTP response | 11.904, 10.580 | 12.366, 12.042 |

The earlier fixed-evaluation and later current-evaluation workloads are not
interchangeable: later reads processed hundreds of thousands of raw native
records. Final direct-report samples contained roughly 451,000 daily and 608,000
31-day native records, with all ten queries completing and no query/system-error
panels. Direct-report measurements exclude the Bun/HTTP path.
The [final HTTP samples](../.tmp/performance-execution-1789044045613/current-evaluation/final-http/summary.json)
all returned HTTP 200 and twelve Pipeline attachments. The other four routes
returned HTTP 200 in 0.149–0.188 seconds.

Intermediate refinement failures remain in the execution evidence, including
the 13.661-second 31-day run and the 13.814-second fresh-process daily run that
exhausted the budget and failed later panels. They motivated the final removal
of repeated immutable-envelope decoding; they were not omitted or converted into
successful samples.

### Current presentation and interaction

Actual browser verification confirmed all five routes, 55 widget positions,
23 Additional measurements, and no horizontal overflow. Pipeline health receives
twelve qualified panels: eleven display measurements, while P11 correctly shows
`Unavailable` because its required immutable observation population is incomplete.
`Completion time · at a glance` remains unchanged.

The existing scan evidence table transitions to `Rows 21–40 of 50` with 20 visible
rows; its complete canonical panel JSON in the actual browser response retains
all 50 rows and exact nanosecond identities. A controlled transport failure
cleared every previous measurement and displayed
`Query failed — outcome unknown`; a subsequent genuine refresh recovered twelve
attachments. No permanent UI test framework or UI source changes were introduced.

- [Browser outcomes](../output/playwright/refined-performance-browser.json)
- [Current desktop presentation](../output/playwright/refined-performance-desktop.png)
- [Pagination](../output/playwright/refined-performance-pagination.png)
- [Complete panel JSON](../output/playwright/refined-performance-complete-panel.json)
- [Failed refresh](../output/playwright/refined-performance-failed-refresh.png)
- [Recovered refresh](../output/playwright/refined-performance-recovered.png)

The companion remains available at `http://127.0.0.1:4173/pipeline`. The existing
telemetry configuration, external storage, installed emitter, scheduling, measurement
cutoff, maintenance, and backups were not changed. No operational scan,
maintenance, backup, vacuum, or scheduler pause was run for this work.

## Definition of done

The dashboard-serving path uses less time and fewer resources under comparable
inputs and workload, while preserving fixed calculation semantics, canonical
signals, qualification requirements, and dashboard presentation and interaction.
Every implemented optimization has exact captured-input equivalence and runtime
evidence. For a changed Python fingerprint, genuine renewed central
application-binding evidence qualifies the existing twelve bindings, and the
existing mock-derived Pipeline layout again shows real qualified metrics after
the `Missing data` root cause is diagnosed. No excluded loading architecture,
product behavior, native-row promotion, or gate/proof-policy change has been
introduced.

## KISS review disposition

The draft was reviewed with the `kiss` agent. Its recommendation was to simplify:
retain deadline/cleanup coordination and same-request reuse; remove speculative
cross-request caching, concurrency, and streaming branches; and make calculator
qualification an upfront release prerequisite. Those changes are incorporated.

The five-route request smoke and existing companion quality gates are retained
because server and registry changes affect the shared path. The blanket
desktop/mobile verification matrix was removed in favor of affected-path checks.
