# Dashboard findings and outstanding plan review

## Purpose and review boundary

Reviewed on **2026-09-28**. This document consolidates the Pipeline serving and
scalability investigation, reviews outstanding obligations in the documentation
plans, and inventories every file under `docs/`, including its subdirectories.

This is a documentation review, not a new runtime qualification or an
implementation authorization. Runtime measurements below come from retained
September investigations; they were not rerun for this document. Current file
inventory, matrix counts, and local link availability were checked during this
review. No application, producer, storage, scheduler, measurement boundary, or
proof status is changed by this document.

The central distinction is:

> The dashboard presentation and twelve central Pipeline bindings have recorded
> implementation and qualification evidence. Reliable serving at the larger
> observed workload remains unresolved. Most native metric proof obligations
> remain blocked independently of that serving problem.

This overview is a dated synthesis, not a second proof register. The authorities
and individual reopening conditions linked below continue to control decisions.

## Executive findings

| Finding | Meaning for delivery |
| --- | --- |
| The later Pipeline failure is supported by capacity evidence, not just an unavailable backend. | The unchanged calculator completed the supported 31-day request in 41.608 seconds with diagnostic headroom, exceeding the existing 14.5-second application deadline. The earlier successful release samples do not establish durable recovery. |
| Data transport, decoding, object expansion, and history processing dominate the investigation. | The largest two database logical results totaled about 896 MB, while the compact complete report was about 42 KB. Browser pagination or rendering changes would not remove the principal calculation cost. |
| Existing optimizations were real but insufficient for the later population. | Request-local snapshot reuse, manifest/identity reuse, reduced duplicate validation work, and cancellation improvements were implemented and verified. They must not be listed as wholly unimplemented work. |
| Qualification and availability are separate. | Twelve central application bindings do not promote the native Appendix rows, and a qualified implementation does not guarantee that a particular request finishes or has a complete applicable population. |
| The larger-volume target has not been agreed. | First optimize the existing read/reducer path against the captured supported 31-day workload. Define a future volume/history/concurrency target before selecting broader architecture. An incremental index is conditional, not an approved next step. |
| Some documentation and evidence references need reconciliation. | Recovery wording predates the renewed failure; retired-plan links use their old directory base; several browser evidence paths do not resolve in this workspace. These are documentation/evidence-access gaps, not permission to rewrite historical results. |

## Documentation authority and file inventory

### Which document answers which question

1. **What must be calculated and shown?** The measurement specification owns
   formulas, identities, populations, time semantics, controls, states, and visual
   contracts.
2. **What is qualified, blocked, and required next?** The proof register owns the
   current human-readable status/action queue. The JSON matrix owns machine-readable
   Appendix classifications and the existing application-binding evidence.
3. **How is the product delivered?** The companion implementation plan owns the
   gated route and feature sequence. The performance plan owns the narrower
   performance-only scope and its release prerequisites.
4. **What happened in earlier experiments?** The retired plans retain historical
   decisions and evidence, not a second active queue.
5. **What should the interface resemble?** The HTML mocks are design references,
   not measurement data or evidence of producer capability.

### Every file under docs

There were nine existing files at review start. This overview is the tenth.
Paths below are relative to `docs/`; the inventory includes `mock/` and `retired/`.

| File | Purpose | Authority or lifecycle | How to use it now |
| --- | --- | --- | --- |
| [dashboard-findings-and-plan-review.md](dashboard-findings-and-plan-review.md) | Consolidates the serving findings, outstanding plan obligations, scope boundaries, evidence limitations, and this file inventory. | Dated review; not a new execution plan or proof authority. | Start here for the overall position, then follow the controlling document for an individual decision. |
| [dashboard-measure-v2.md](dashboard-measure-v2.md) | Defines P1–P12 Pipeline, R1–R8 Provider, and M1–M18 Model usage measures; canonical identities, formulas, temporal rules, capability/availability states, five-view visual contracts, and Appendix A controls. | Normative measurement and presentation specification. | Resolve semantic disagreements here. “Canonical now” is a contract-availability category, not automatic row-level production proof. |
| [dashboard-metric-proof-register.md](dashboard-metric-proof-register.md) | Maps measures, independent widgets, all 43 Appendix controls across producers, evidence keys, missing authority, and concrete reopening conditions. Records central acquisition and qualification separately from native rows. | Sole current human-readable proof/status/action register. | Use it for the next proof action and its owner; update it with new bounded evidence or an authoritative contract change, not with speculative success. |
| [dashboard-prototype-proof-matrix.json](dashboard-prototype-proof-matrix.json) | Stores 129 producer-specific Appendix rows, query/oracle and temporal contracts, experiment dependencies, capability exclusions, privacy/cleanup boundaries, and twelve central application evidence bindings. | Machine-readable classification and application qualification authority; despite “prototype” in the name, still active. | Validate against it and preserve row-bound evidence requirements. Do not edit classifications to make a dashboard request appear healthy. |
| [react-signoz-companion-implementation-plan.md](react-signoz-companion-implementation-plan.md) | Describes the independent React/TypeScript/Bun companion, five routes, shared controls and states, typed query boundary, route-specific dependencies, hardening, and separate presentation/calculation release gates. | Active, proof-gated product delivery plan. | Distinguish implemented route shells from blocked calculation delivery. Consult the register rather than treating every numbered instruction as unfinished or every rendered route as proven. |
| [dashboard-performance-improvement-plan.md](dashboard-performance-improvement-plan.md) | Defines performance-only invariants and exclusions, ordered deadline/reuse/reducer work, exact equivalence and central renewal requirements, and historical execution/browser results. | Active scope and verification contract with historical implementation records. | Preserve its existing SQL, calculation, loading, and presentation boundaries. Read its recovery claims alongside the later capacity failure described here. |
| [retired/gap-closure-prototype-experiment-plan.md](retired/gap-closure-prototype-experiment-plan.md) | Retains the bounded prototype program: five reducer workstreams, 24 experiments, producer field audits, temporal contracts, proof matrix freeze, viability decisions, and cleanup incident/correction. | Retired as an active plan on 2026-09-05. | Use as historical evidence and design rationale. “Completed — Blocked” means the experiment concluded with a blocker, not that the desired production metric was delivered. |
| [retired/authoritative-proof-closure-plan.md](retired/authoritative-proof-closure-plan.md) | Retains the follow-on proof-closure requirements, native owner assignments, workstream dependencies, row-promotion gate, and 2026-09-05 outcomes. | Retired as an active plan on 2026-09-05; superseded by the proof register. | Trace unresolved obligations into the current register. Do not resurrect its old Pipeline prerequisites as though later central qualification never occurred. |
| [mock/show-me-signoz-dashboard-architecture.html](mock/show-me-signoz-dashboard-architecture.html) | Visual map of three semantic groups, five focused views, navigation, first-viewport priorities, and progressive detail. | Illustrative architecture/design reference. | Understand information hierarchy; do not treat sample values or the diagram as runtime architecture or measurement proof. |
| [mock/show-me-signoz-dashboard-mocks.html](mock/show-me-signoz-dashboard-mocks.html) | Interactive visual reference for the five views, panel hierarchy, labels, layout, controls, tables, and sample charts. | Illustrative UI reference, subordinate to the canonical specification. | Compare presentation and geometry, never copy illustrative metrics into production results. Preserve the accepted `Completion time · at a glance` section. |

## Recorded delivery and qualification position

### Timeline and what it establishes

| Recorded stage | Established outcome | What it does not establish |
| --- | --- | --- |
| Prototype consolidation and 2026-09-05 follow-on | Bounded experiments classified the gaps. The follow-on retained historical E-Attribution-1 and qualified E-Pipeline-5 at experiment level; 22 experiments remained blocked. | No native Appendix row was promoted. Completion of an experiment program is not completion of the dashboard's metric coverage. |
| Central delivery, 2026-09-08 | The authorized fresh measurement boundary and twelve central application bindings qualified P1–P4, P10–P12, and Canonical scan evidence against the captured deployment and independent calculations. | Native lifecycle, request, task, and recurrence rows did not inherit that proof. Historical populations were not repaired or rebased. |
| Performance recovery starting 2026-09-10 | The existing stopped OrbStack deployment was restored without recreation. Python/Bun refinements reduced redundant work, renewed the twelve bindings, and passed exact comparison, software gates, cancellation, HTTP, and browser checks. | Its successful individual request samples were not a latency distribution or a capacity guarantee. |
| Later failure investigation, 2026-09-12 | The user reported `Query failed`. SigNoz and OrbStack were healthy in that investigation, the calculator identity was unchanged, and the calculator completed the larger workload with isolated diagnostic headroom. | No scalability fix was activated. A diagnostic completion outside the serving deadline is not live dashboard recovery. |
| This review | Current documentation inventory, proof-matrix counts, evidence availability, and outstanding plan obligations were inspected. | No fresh health, throughput, browser, or production qualification claim is made. |

The [retained final release verification](../.tmp/performance-execution-1789044045613/current-evaluation/final-verification.json)
records 1,056 passing Python tests and 21 passing companion tests, plus formatting,
lint, type checking, build, complete-report comparisons, browser outcomes, and real
cancellation. Those are historical results, not checks rerun for this document.

### Current matrix facts checked during this review

- 43 Appendix controls multiplied by three supported producers produce **129 rows**.
- **127 Blocked, 2 Not applicable, 0 Proven**; all 129 native `evidence_bundle`
  values remain `null`.
- The two static exclusions are OMP A24, Explicit user-friction rate, and OMP A34,
  Sandbox friction. They are not missing-data work items to “fix.”
- Supported producers are `omp`, `codex-cli`, and `codex-app-server`.
  Claude remains outside supported-producer denominators pending its separate
  three-way identity proof.
- **Twelve central application bindings** remain recorded separately. They cover
  snapshot; scan outcomes; freshness; duration; rows; throughput; source lag;
  outbox snapshot; outbox delivery detail; integrity; ledger; and scan evidence.
- All twelve reference calculator fingerprint
  `7c2c5d488e120e868aaed0de6bdf5990ff7c8ad89456ff8fbba3379475b74ecd`.
  The retained deployed emitter/projection fingerprint is
  `7287a225b1606d6e7a30ba9d288c2baeb219a2ec159b55e653f790490815b75e`.
  These identify different components and must not be substituted for one another.

The twelve bindings are not twelve universally visible numeric results. A
qualified panel can correctly be `Unavailable` for an incomplete observation
population or `No data` for a valid empty cohort. P11 was legitimately unavailable
in one release capture and had data in another diagnostic population. Likewise,
a raw calculator result for P5/P6 does not bypass their separate display gates.

## Pipeline failure and scalability findings

### Request path and observed failure modes

The recorded serving path is:

```text
Browser /api/dashboard request
  -> Bun request parsing and shared deadline
  -> registry preparation and independent SigNoz health probe
  -> dashboard-owned Python subprocess
       -> attribution/history/native-source reads and validation
       -> Pipeline projections and reductions
       -> complete report with implementation/deployment identities
  -> complete-report validation and qualification of every deployment
  -> atomic response and existing browser rendering
```

The application deadline is 14.5 seconds; the HTTP transport deadline is
15 seconds. Python/backend work receives the remaining budget, with cleanup
headroom; the diagnostic child budget was approximately 13.5 seconds.

The retained [request diagnosis](../.tmp/pipeline-failure-1789199847021/request-diagnosis.json)
returned HTTP 200 in 13.970 seconds but attached **zero measurements**. Its report
contained query-error panels and an empty deployment list. That diagnostic is
not a contradiction of the user's whole-request `Query failed` observation:
timing can determine whether the request fails entirely or returns a complete
response whose measurements cannot be admitted. HTTP 200 alone is not a recovery
criterion.

The registry stage took about 232 ms; the Pipeline stage took about 13.178 seconds.
The [query diagnosis](../.tmp/pipeline-failure-1789199847021/query-diagnosis.json)
then showed a daily read processing 1,407,493 native trace rows. Client decoding
and reduction could outlast the query's nominal budget; subsequent queries
encountered an already-exhausted deadline. The Bun parent remained the hard
request boundary.

### Measured costs

The maximum-supported-range diagnostic requested:

```text
start:       2026-08-12T07:59:28.323Z
end:         2026-09-12T07:59:28.323Z
evaluatedAt: 2026-09-12T07:59:28.323Z
```

The existing measurement cutoff was `2026-09-08T08:32:42.802Z`
(`1788856362802000000` nanoseconds). “31-day report” therefore describes the
supported requested interval, not 31 full days of post-cutoff contributing data.
Required history also has its own evaluation-time rules; it is not generally
bounded by the displayed interval.

| Observation | Recorded value | Interpretation or limit |
| --- | ---: | --- |
| Unprofiled complete 31-day diagnostic | 41.608 s | Ten queries finished, no query-error panels, and the expected deployment identity was present. This used isolated 90-second headroom, not a changed companion deadline. |
| Native traces in that complete run | 2,427,869 | Physical input rows, not distinct sessions or eligible metric rows. |
| Native logs in that complete run | 34,149 | Separate log population. |
| Lifecycle rows in that complete run | 7,287 | Low row count hides large repeated history manifests. |
| Trace query database duration | 1.482 s | ClickHouse query-log measurement. |
| Trace query client iteration | 18.673 s | Includes client-side query/transport/decoding work; not a measurement of network time alone. |
| Largest two database logical result sizes combined | 895,741,235 bytes | Logical database result bytes, not measured compressed wire traffic or total process memory. |
| Compact complete report JSON | 41,658 bytes | The final browser response is small relative to the acquired data. |
| Fixed-window profiled Python peak resident memory | 3,886,104,576 bytes, about 3.62 GiB | One instrumented Python process; not total system memory or a concurrency capacity result. |
| Fixed-window profiled elapsed time | 75.817 s | Includes profiler overhead; must not replace 41.608 s as ordinary latency. |

Sources: [complete diagnostic](../.tmp/pipeline-failure-1789199847021/complete-report-diagnosis.json),
[database costs](../.tmp/pipeline-failure-1789199847021/remote-query-costs-expanded.json),
[profile summary](../.tmp/pipeline-failure-1789199847021/fixed-window-profile-summary.json),
and [recorded analysis](../.tmp/pipeline-failure-1789199847021/scalability-analysis.json).

A later bounded [native-volume inspection](../.tmp/pipeline-failure-1789199847021/native-volume-summary.json)
of the same requested window observed 2,427,879 trace rows. Of these,
2,372,016, or **97.699%**, had no native session identity. The remaining 55,863
rows yielded 1,019 service/session keys and 44,093 service/session/timestamp
combinations. The physical source keys were distinct in that sample, so ordinary
duplicate elimination alone would not remove the dominant row volume.

The extra ten trace records are also a correctness warning: fixed report bounds
do not freeze a live source population. Later reads are not an exact replay of
earlier inputs. A separate later rolling daily profile had only 10,769 native
trace rows and is not comparable evidence of recovery for the earlier daily
failure.

### Why the current work grows so much

| Mechanism established in the investigation | Relevant implementation area | Consequence |
| --- | --- | --- |
| The query subprocess buffers complete text output before decoding; decoding splits it into lines. | [source.py](../src/agent_introspection/source.py), `DashboardClickHouseClient.query` and `_decode_query_rows` | Large serialized output and subsequent objects can coexist in memory. Bounding subprocess `communicate` is not the same as bounding all later Python work. |
| Native rows are fully decoded, collected, parsed, and compared for source-identity conflicts before identity-less rows are excluded from the metric population. | [source.py](../src/agent_introspection/source.py), `query_raw_native_sources` | Millions of rows incur validation and allocation even when few contribute to the displayed populations. Excluding them earlier without equivalent validation changes correctness. |
| Lifecycle reads include complete history through evaluation time, not merely the visible range. | [pipeline_attribution.py](../src/agent_introspection/pipeline_attribution.py), lifecycle query and parser | A small display range does not guarantee a small request. Historical manifest growth contributes independently of recent trace volume. |
| Immutable event bodies and typed attribute maps are both checked, including exact manifest populations. | [pipeline_events.py](../src/agent_introspection/pipeline_events.py) and [pipeline_integrity.py](../src/agent_introspection/pipeline_integrity.py) | Removing one representation or selecting latest rows before validation cannot be assumed equivalent. |
| Attribution ownership contributes to final deployment admission even when attribution widgets remain blocked. | [pipeline_projection.py](../src/agent_introspection/pipeline_projection.py) and [registry.ts](../dashboard/src/registry.ts) | Skipping the blocked panels' dependencies can change which central measurements qualify. |

The [lifecycle volume inspection](../.tmp/pipeline-failure-1789199847021/lifecycle-volume-summary.json)
found 1,487 population records with 279,971,949 body bytes, including
265,398,281 bytes of interval manifests in the corresponding manifest field.
Manifests are represented in immutable bodies and attribute maps; those amounts
must not be added as though they were disjoint payload categories. The original
lifecycle query alone reported 568,352,165 logical result bytes.

The profiler attributed 2,462,028 calls to native source-row parsing and
12,310,140 calls to string-array validation. This supports focusing on the
read/reducer path, but cumulative profile times overlap and must not be summed.

**[INFERENCE]** Larger source populations, longer histories, and concurrent
requests can increase this work and memory pressure substantially. No measured
p95, safe concurrency limit, or maximum supported population has been established.

### Optimizations already completed

The [performance execution record](dashboard-performance-improvement-plan.md#executed-python-scope-and-current-recovery)
and retained release evidence establish that the prior work:

- prepared registry inputs once per request;
- shared successful, fully validated snapshots and equivalent reductions within
  a report, reducing query count from eleven to ten;
- reused exact manifest content and valid canonical UUIDs within the appropriate
  request/parser scope without skipping envelope or conflict checks;
- avoided repeated native-array set/sort allocation and identity classification;
- reused decoded physical envelopes only after complete integrity validation;
- scoped backend deadlines and targeted cancellation to dashboard execution;
- launched the worktree `.venv/bin/python` directly instead of `uv`, with
  cooperative termination, bounded escalation, reaping, and observed process-group
  disappearance;
- genuinely renewed the twelve central bindings and compared three complete
  captured-report scopes, normalizing only `implementation.calculationSha256`.

Recorded activated HTTP samples were 10.580–11.904 seconds for daily requests and
12.042–12.366 seconds for 31-day requests. They involved different populations
from the later failure and are historical successes, not sustained capacity proof.
The next investigation should find remaining demonstrated duplication rather than
repeat these completed changes or promise the same speedup again.

## Correctness and operational boundaries

Performance and proof work must preserve the following obligations:

1. **Exact populations and time.** Preserve each measure's range operator,
   lifecycle containment, evaluation policy, measurement cutoff, complete required
   histories, and globally latest selection. Keep nanosecond identities and numeric
   kinds exact.
2. **Validate before excluding or selecting.** Detect malformed records,
   conflicting duplicates, missing versions, supersessions, and manifest/population
   mismatches even when a row would not contribute a visible value.
3. **Keep both correlation directions.** First source time supports forward
   coverage/delay; reverse coverage also needs whether any eligible source time
   falls in an authoritative interval. One minimum per session is insufficient.
4. **Keep deployment admission complete.** Every contributing deployment must
   match its qualification; an empty deployment list cannot qualify. Hidden or
   blocked widgets do not authorize omitting ownership or integrity dependencies.
5. **Keep the whole-report and UI contract.** Preserve five routes, 55 widget
   positions, 23 Additional measurements, exact labels, controls, table caps,
   twenty-row pagination, complete-panel JSON, and atomic response application.
   `Completion time · at a glance` is accepted and is not a redesign task.
6. **Preserve error distinctions.** `Missing data` describes a closed gate, not an
   eighth result state. `No data`, `Not applicable`, `Unavailable`, integrity
   failure, and `Query failed — outcome unknown` remain distinct. A current failed
   refresh clears prior measurements; superseded responses cannot overwrite it.
7. **Renew changed Python identities genuinely.** Any package Python change affects
   the calculator fingerprint. Renew the twelve existing central bindings before
   serving the changed implementation; do not substitute hashes or promote native
   rows as a shortcut.
8. **Keep cleanup observable.** Verify the owned remote query and process descendants
   disappear on abort/deadline; parent exit or a successful signal alone is not
   proof of cleanup.
9. **Preserve evidence and installed operations.** No automatic scans, maintenance,
   backups, vacuum, scheduler pause, emitter changes, storage move, stack recreation,
   historical reset, or measurement-cutoff change is implied. OrbStack's recorded
   storage remains `/Volumes/UGreen-External/Docker`. Preserve the independent
   `dashboard/` package and retained captures, clones, wheels, and `.codegraph/`.

## Outstanding work in the active plans

“Outstanding” below means an unmet outcome or release obligation, not necessarily
missing source code. Historical software/browser success does not close every
future production calculation gate. Component owners identify responsibility,
not a newly authorized assignment.

### Dashboard performance improvement plan

Controlling sections: [ordered implementation](dashboard-performance-improvement-plan.md#ordered-implementation-plan),
[scope exclusions](dashboard-performance-improvement-plan.md#explicitly-out-of-scope),
and [definition of done](dashboard-performance-improvement-plan.md#definition-of-done).

| Plan item | Recorded completion | What remains outstanding | Owner and exit condition |
| --- | --- | --- | --- |
| 1. Coordinate deadlines and cleanup | Shared bounded deadlines, direct Python launch, targeted remote cancellation, and descendant cleanup were implemented and exercised. | The later supported-range workload still exceeds the serving budget. Preserve and recheck cleanup if subsequent changes affect execution; do not treat timeout increases as the optimization. | Bun server and dashboard-only Python query path. Representative requests finish within the agreed budget or fail through the existing contract with no owned work left behind. |
| 2. Remove redundant request/registry work | Registry preparation occurs once per request; the independent health probe remains. | No evidence here calls for rebuilding this step. Investigate only additional demonstrated duplicate work while preserving qualification order. | Server/registry owner. Same gates and complete response for identical inputs, without a second validation pass. |
| 3. Reduce Pipeline query/calculation overhead | Snapshot, manifest, identity, and decoded-envelope reuse have recorded exact-equivalence evidence. | Identify remaining redundant decoding, normalization, allocation, or reductions at the larger captured 31-day workload. Existing SQL projections/predicates/order and loading architecture remain fixed under this plan. | Python read/reducer owners. Exact complete-report equivalence and measured resource improvement on identical inputs; no omitted validation or ownership. |
| 4. Verify before release | Prior captured equality, genuine renewal, quality gates, real cancellation, HTTP and browser checks succeeded for their recorded populations. | New candidates need new comparable timing/resource evidence, applicable quality gates, genuine binding renewal for changed Python, actual HTTP/browser recovery, and repeated supported-range runs. A 200 response with zero attachments is not success. | Integrator and qualification owner. End-to-end recovery at the declared workload with unchanged report semantics, qualification, presentation, and failure behavior. |
| Overall outcome | A narrower release was successfully demonstrated. | Reliable serving at the later observed volume is not established. Reconcile the document's “current recovery” language with the subsequent failure. | Performance plan owner. Record workload and limits explicitly; do not claim p95 or durability from isolated samples. |

The plan currently excludes new SQL aggregation/projections, internal streaming
or batching, new read concurrency, cross-request caches, persistent workers,
materialized views, background precomputation, and new loading/job protocols.
They are not optional branches of the existing implementation authorization.

### React and SigNoz companion implementation plan

Controlling sections: [delivery sequence](react-signoz-companion-implementation-plan.md#delivery-sequence),
[release gates](react-signoz-companion-implementation-plan.md#release-gates), and
[complete implementation](react-signoz-companion-implementation-plan.md#definition-of-complete-implementation).

| Phase | What exists or is recorded | Outstanding outcome and prerequisite |
| --- | --- | --- |
| 0. Contract and proof readiness | The canonical specification, 129-row matrix, register, typed registry, and bounded transport/qualification evidence exist. The embedded prototype “Phase 6 consolidation record” is historical handoff evidence. | Close each required producer/control/measure proof separately. Keep the Appendix implementation manifest, query owner, temporal contract, capability reason, and evidence bindings aligned. No generic phase-complete claim replaces a row's EvidenceBundle. |
| 1A–1C. Shell, state/design system, typed query surface | Five route shells, shared controls and state handling, an independent Bun package, qualified Pipeline responses, and recorded visual/interaction checks exist. | Preserve these rather than rebuilding them. Full future calculation coverage still needs its independent oracles, provenance, state behavior, and accessibility verification. The review does not establish every seven-state/keyboard/zoom journey as currently closed. |
| 1D. Pipeline health | Twelve central bindings cover P1–P4, P10–P12, and scan evidence. P10 snapshot and detail are distinct bindings; P12 retains actual maintenance/database ownership. | Fix the serving-capacity failure separately from P5–P9 proof closure. P4 must retain scan-bound cohorts, P6 its containing-interval/delay proof, P9 its transition and independent rate proof, and P11/P12 population-specific availability. Native A01–A06 rows remain blocked despite central delivery. |
| 1E. Canonical-now Model usage wave | The plan explicitly keeps these calculations gated. | Historical aggregate Attribution proof does not open production activity counts, trace pressure, friction, or the shared controls. Supply each row/independent population proof before attaching values. |
| 2. Provider health | Route and designed widget places exist; request/attempt calculations remain blocked. | Authoritative native request, attempt, accounting, model-role, timing, retry, streaming and terminal fields must co-occur. Then prove immutable projections, R1–R8, provider/model filters, unknown populations, phase samples, and terminal-grace policy boundaries for all applicable producers. |
| 3. Usage and interaction | Route shell exists; M1–M4 are not qualified as production calculations. | M1/request-accounted M2 need request/accounting authority; trace-accounted M2 needs its own complete episode/percentile/outlier proof, not missing request fields; M3 needs observable-task/friction proof; M4 timing and terminal recovery open separately. Preserve source-time domains and Project including unresolved. |
| 4. Tool execution | Route shell exists; M5–M11 remain gated. | Exact immutable calls, tasks, order, targets and explicit outcomes must support independent failure, repeat, churn, loop, sandbox and bypass populations. M6 and applicable recovery columns separately need observable task ends. Preserve static OMP exclusions and redacted Tool evidence. |
| 5. Recurrence and interventions | Route shell exists; M13/M16 Wave A and later calculations remain blocked. | Prove M13/M16 individually before enabling their wave. Prove M14 London-calendar findings, M15 exposure-matched non-causal pre/post comparisons, and separate M12 rule, M17 practice and M18 intervention/tier contracts. Application evidence work follows individual dependencies, not a blanket terminal-outcome prerequisite. |
| 6. Integrated hardening and release | Historical route/layout, URL/filter, pagination, failed-refresh and software-quality evidence exists. Presentation completion is distinct from production metric completion. | For enabled calculations, finish the complete Appendix coverage, cross-route controls, direct entry/deep links, manual evidence reproduction, desktop/mobile/200% zoom, keyboard/accessibility, real error states, privacy checks, installed-version/query/capability record, and availability manifests. Reverify affected paths after changes. Most calculation gates remain unmet even if the presentation gate is complete. |

The plan's imperatives are delivery requirements, not a reliable checklist of
unimplemented code. In particular, do not restart the shell or replace the
existing design because later metric proofs remain blocked.

### Proof work that remains behind those route phases

This table summarizes the [current register](dashboard-metric-proof-register.md#latest-evidence-keys-and-unresolved-boundaries).
It does not replace its producer-specific A01–A43 rows or the independent widget
gates. An **EvidenceBundle** is the row-bound packet connecting fresh native
source lineage, deterministic reduction and delivery identities, direct remote
calculation, and an independent oracle with matching values and scalar types.

| Workstream and affected obligations | Missing boundary or remaining action | Owner and reopening rule |
| --- | --- | --- |
| Native Pipeline A01–A06, register K1–K3 | Complete per-producer row-bound populations and query/oracle proof for scan, source-session and activity time; snapshot, terminal/cadence, workload and extraction-bound lag obligations. Central application proof is not automatic coverage of these wider rows. | Application scanner/scheduler/source/proof owners. Reconcile existing central evidence with the exact remaining native row obligations; do not recreate already-proven central schemas by default. |
| OMP/CLI Attribution A07–A09, K7 | Six row bundles remain unqualified. Retained evidence had remote-retention gaps; CLI activity evidence also had unbound native identities. Fresh exact producer/native-session/source lineage and complete version/project populations are required. | Native lifecycle/source surfaces and shared attribution reducer. Preserve the historical aggregate and failed windows; obtain new bounded complete row evidence rather than trimming/rebasing them. |
| Codex app-server Attribution, K8 | Startup, resume, clear and compact scenarios need accepted lifecycle boundaries, exact native/source identity, stable project, directional correlation, concurrent-project isolation, non-Git classification and qualifying same-project source activity. | Authoritative app-server producer boundary. Shared global CLI/Desktop hooks cannot establish the caller merely through labels, paths, or process guesses. |
| Claude boundary, K9 | Fresh equality of hook session ID, local artifact session ID and OTEL session ID. | Native Claude boundary. Keep unsupported denominators and ingestion restrictions unchanged until that authority exists; unchanged audits do not reopen it. |
| P6 delay, K9a | Complete non-empty remotely visible source-to-accepted-containing-lifecycle population, delay percentiles/sample counts, matched sessions and negative skew. | Lifecycle/source and attribution owners. A historical empty cohort or partial delivery is not proof. |
| P9 transitions and rate, K9b | Exact immediate valid predecessor transitions with complete histories; the rate additionally requires the complete remote ever-unresolved denominator independent of selected delivery range. | Lifecycle and activity version owners. Transition delivery alone does not open the rate. |
| Request/attempt/accounting, K10; R1–R8, request M1/M2 and related controls | Retained audit classified 84 fields absent, 8 ambiguous and 4 authoritative; no authoritative candidate population was constructed. Need co-occurring native session/request/attempt/accounting identity, phase timing, role-separated models, tokens, terminal/error/retry semantics. | OMP provider execution/retry/accounting and Codex native request/OTEL owners. Reopen only after the affected native contract changes; more records of the same shape or adapter-generated grouping are not authority. |
| Trace-accounted M2 independent widget | Complete trace-episode identity, comparison population, source/project filters, samples, percentiles, outliers, direct remote query and independent oracle remain unqualified despite available raw totals. | Application trace-accounting/reducer owner. Do not merge request/trace accounting or require unrelated missing request fields for this population. |
| Task/operation/terminal, K11; M3–M11 and controls | Exact immutable task/call identity, operation route, complete order, source time and explicit outcomes. Terminal metrics separately require the same durable task at an authoritative terminal boundary. | Each native operation dispatcher and task-terminal owner. Do not infer success from session end, the last tool, response text or absent errors; do not repeat unchanged audits. |
| E-Recurrence-0, K12; M13/M16, A40/A41 | Producer-specific Attribution proof, durable canonical task identity, retention-compatible complete activity projection and applicable population proof. | Native task identity and application activity owners. No invented global task-terminal gate: A40/A41 do not acquire that requirement. |
| E-Recurrence-1, K13; M14, A38 | Immutable finding versions, globally latest activity authority, explicit evidence windows, exact canonical-task membership and required seven-day Europe/London calendar semantics. Observed `rolling_utc_7d` is not the required `europe_london_calendar_7d`. | Application finding/activity owners. This experiment has its own proof obligations and no generic experiment prerequisite; thread lineage is not canonical-task authority. |
| E-Recurrence-2, K14; M17, A39 | Real ordered operations and terminal success, task class, comparable population and versioned enforcement owner for successful-practice candidates. | Native task owners, then application practice owner; E-Task-1/2/3 prerequisites. Unavailable candidates are not zero successful practices. |
| E-Recurrence-3, K15; M12, A42 | Versioned machine rule, trigger/applicability, required-action observation and violation registry with separate unknown observability. | Application policy owner after E-Task-1/2 authority. A detector count does not establish rule adherence. |
| E-Recurrence-4, K16; M15/M18 and A36/A37/A43 | Finding/practice/proposal lineage, exactly one tier, earlier-tier reasons, approval/application time, validation and comparable recurrence populations. | Application workflow/intervention owners after E-Recurrence-1/2. M15 additionally needs equal exposure-matched windows, observable-task denominators, raw counts, positive-pre-rate handling and comparability suppression; never a causal claim. |
| Independent evidence widgets and shared controls | Provider, Friction, Tool and Candidate/intervention evidence need exact redacted memberships and their own filters/time/row limits. Provider unknowns, latency phases, streaming and throughput keep separate populations. | Respective query/UI and proof owners. Event presence or one numeric experiment does not prove URL semantics, per-panel metadata, drill-down or an independent denominator. |

## Retired plans and carried-forward obligations

### Gap-closure prototype experiment plan

The bounded prototype program is complete as a **classification program**:
experiments can finish as `Blocked`. Its unfinished semantic boundaries now live
in the current register and companion gates; it is not an instruction to rerun
all experiments or deploy their proposed projections.

The document contains successive historical layers. Its earlier Phase 6 text
calls E-Attribution-1 the sole proven experiment, while the 2026-09-05 follow-on
records E-Pipeline-5 as proven too. Read those as different dated stages, not as
current mutually interchangeable status summaries. The later central Pipeline
bindings are a further distinct qualification layer.

Preserve the cleanup incident record. It describes an unsafe historical predicate
that removed namespace-omitting remote rows, its fail-closed correction, and
retained immutable local identities. It does not authorize restoration from
insufficient evidence, new cleanup, or deletion of old failed proof populations.

### Authoritative proof closure plan

Its row-promotion and ownership rules remain useful historical context, but its
active queue was explicitly transferred to the register. In particular:

- historical missing Pipeline snapshot, cadence, integrity and maintenance
  primitives must be read alongside later central acquisition and qualification;
- Attribution still needs individual fresh row bundles and exact native authority;
- Request and Task wait for actual native contract changes, not more adapter work
  against unchanged fields;
- Recurrence follows its separate finding, practice, rule and intervention
  dependencies, not a universal M13/M16 or terminal-outcome gate for evidence work.

Neither retirement declares the remaining blocked objectives complete or
impossible. Neither old plan authorizes production changes in this review.

## Documentation gaps and evidence-access limitations

### Status and scope reconciliation

| Gap | Evidence and significance | Recommended documentation action |
| --- | --- | --- |
| Recovery wording is temporally stale. | The performance plan's “Executed Python scope and current recovery” and “Current presentation and interaction” sections describe successful earlier samples. The later retained failure and 41.608-second request invalidate treating those headings as present capacity assurance. | Retain the release evidence, label it historical, and link the later failure/workload. Do not delete successful samples or claim a new recovery. |
| Some native Pipeline action cells still use pre-central-delivery wording. | The register's A02/A04/A05 actions describe persisting inputs/policy while K1–K6 document proven central acquisition. The wider native rows are still blocked; the scope distinction matters. | Clarify the additional producer-row evidence still required rather than treating all central persistence work as undone. Keep all classifications unchanged until individual gates pass. |
| Canonical-now and imperative wave wording can look like an enabled feature list. | The companion plan says to retain trace M2/M3 from a wave, but explicitly keeps Model usage gated and the register requires independent proof. | Make the dated implementation/qualification status explicit at future plan updates; never infer a visible production calculation from a normative availability label. |
| The initial scalability artifact contains broader suggestions than the final recommendation. | Its recommendation array proposes an incremental index and an “at least 10x” workload. Subsequent discussion narrowed both: current supported-workload optimization first, explicit future target, index only if needed. | Treat the artifact as retained analysis, not an approved execution plan. Use the sequencing in this overview when preparing a future proposal. |
| No agreed future capacity envelope exists. | Existing evidence comprises bounded individual runs, not an SLO, p95 distribution, memory ceiling or concurrency qualification. | Define source volume, history/manifest size, time range, concurrency, freshness, latency and memory together before accepting a larger-scale design. |

### Local link audit

File-target existence was checked for inline and reference-style local links in
the six pre-existing Markdown documents. Counts below are **unique unresolved
link targets within each file**, not distinct missing artifacts across the whole
repository. External URLs were not network-validated, and these counts do not
claim a complete anchor audit of the existing documents.

| Markdown document | Unresolved local targets | Finding |
| --- | ---: | --- |
| `dashboard-measure-v2.md` | 0 | Referenced local files resolve. |
| `react-signoz-companion-implementation-plan.md` | 0 | Referenced local files resolve. |
| `dashboard-performance-improvement-plan.md` | 7 | Browser screenshot/JSON paths under `output/playwright/` do not resolve. |
| `dashboard-metric-proof-register.md` | 2 | Current-build surface/interaction JSON paths under `output/playwright/` do not resolve. |
| `retired/authoritative-proof-closure-plan.md` | 14 | Links still resolve relative to the old `docs/` base; every listed target exists when resolved from that original base. |
| `retired/gap-closure-prototype-experiment-plan.md` | 15 | Same moved-document relative-path problem; every listed target exists from the original base. |

For the retired plans, root documentation links generally need `../` and
repository experiment links need `../../experiments/`. This review records the
repair needed but does not edit the historical files.

The unresolved browser evidence paths, relative to the repository root, are:

```text
output/playwright/serving-performance-desktop.png
output/playwright/refined-performance-browser.json
output/playwright/refined-performance-desktop.png
output/playwright/refined-performance-pagination.png
output/playwright/refined-performance-complete-panel.json
output/playwright/refined-performance-failed-refresh.png
output/playwright/refined-performance-recovered.png
output/playwright/companion-current-build-surface-verification.json
output/playwright/companion-current-build-interaction-verification.json
```

A recursive filename search for the principal browser summaries/screenshots also
found no alternate copies in this workspace. Their absence at these paths does
not establish when or why they became unavailable. The retained final release
verification JSON still contains browser outcome summaries, but that is not a
replacement for independently reviewing the missing screenshots. Recover original
artifacts or correct their locations if available; otherwise state the visual
verification limit and retain a future fresh verification as a new observation,
not a reconstruction of old evidence.

Many investigation references live in `.tmp/`. They resolve in this worktree but
are local evidence, not guaranteed to accompany a clean checkout or a published
copy of the documentation. Preserve those artifacts and their original identities;
do not silently replace them with later mutable reads.

## Recommended sequence and decision gates

These are recommendations, not implementation work authorized by this document.
The latest user request authorizes this overview and plan review only.

| Order | Proposed action | Entry condition | Exit evidence |
| --- | --- | --- | --- |
| 1 | Optimize remaining demonstrated redundant work in the existing read/reducer path against the captured supported 31-day workload. | Explicit implementation instruction; existing performance-plan constraints remain in force. Identify the frozen-input replay set and unchanged complete-report oracle first. A live read of the same dates is not a frozen capture. | Exact reports, numeric kinds, populations, error behavior and provenance; measured latency/memory improvement. Preserve already-completed request-local optimizations. |
| 2 | Define the future capacity envelope. | A concrete intended operating workload, not an arbitrary multiplier. This decision can proceed alongside the bounded optimization. | Agreed source-row and eligible-session volumes, lifecycle/history age and manifest sizes, supported ranges, concurrency, freshness, latency distribution and memory limits. The earlier 10×/approximately 25-million-trace scenario is only an exploratory example. |
| 3 | If bounded optimization misses the explicit target, review the smallest required scope expansion. | Measured residual bottleneck and an equivalence analysis. | Separately approved changes, potentially compact validated query inputs or bounded-memory processing, without weakening full-population validation. No assumption that SQL aggregation or streaming is already allowed. |
| 4 | Consider a rebuildable incremental serving index only if the preceding approach cannot meet the agreed target. | Demonstrated need, storage/operational approval, and a trustworthy arrival/change/completeness mechanism. | Correct invalidation for late arrivals, conflicting duplicates, history/supersession changes, policy/fingerprint changes and retention; retained immutable evidence and Python calculation ownership. Timestamp maxima or TTL alone do not establish completeness. |
| 5 | Qualify and verify any candidate before activation. | Exact frozen-input equivalence and required genuine central evidence renewal completed. | Matching source/matrix activation, all twelve bindings, unchanged native rows, real HTTP attachments and browser behavior, repeated representative timings, remote-query/descendant cleanup, and applicable quality gates. |
| 6 | Continue metric proof closure by its own owners and prerequisites. | The relevant row's native or application authority becomes available and proof work is separately authorized. | Fresh row-bound source, delivery, direct remote calculation and independent typed-oracle equality. Performance recovery alone never opens a metric gate. |

Longer deadlines or asynchronous job delivery can change waiting behavior but do
not remove data movement, validation, computation or memory cost. They require a
separate operational/product decision; they are not the default recovery plan.
Likewise, concurrency/admission changes are outside the current narrow scope even
though concurrency belongs in the future capacity target.

No maintenance action is recommended for this dashboard issue. Preserve the
existing threshold-triggered maintenance design and production scheduling.

## Completion criteria for future work

There are three independent completion statements; report them separately:

1. **Serving recovery:** representative supported requests finish within the
   declared budget, attach the correctly qualified panels, preserve legitimate
   unavailable/empty states, and leave no owned work behind on cancellation.
2. **Capacity qualification:** repeated measurements meet an explicit
   volume/history/concurrency/latency/memory envelope. Neither one fast sample nor
   a long-budget diagnostic satisfies this claim.
3. **Metric coverage:** every applicable enabled measure, control and independent
   widget has its own authoritative proof and projection boundary. The remaining
   native rows stay blocked or statically not applicable until their individual
   conditions change.

For this documentation task, completion is narrower: a reconciled findings
summary, an outstanding-plan review, a complete recursive file-purpose inventory,
and checked references/Markdown for the new document. No runtime recovery,
performance implementation, native proof promotion, or source-plan rewrite is
claimed.
