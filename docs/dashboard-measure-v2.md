# Dashboard Measure v2

## Objective

Define the measures needed to distinguish pipeline/process health, model-provider health, and model usage. The dashboard must make failures attributable to the correct layer and expose repeated user, agent, and tooling behavior without treating missing evidence as success or failure.

## Delivery and proof references

- [React and SigNoz Companion Implementation Plan](react-signoz-companion-implementation-plan.md)
  is the active, gated route from the approved measurements and mock design to
  the five production React routes.
- [Dashboard Metric-to-Proof Register](dashboard-metric-proof-register.md) is
  the current human-readable status and remaining-actions authority for every
  metric, widget, and Appendix control.
- [`dashboard-prototype-proof-matrix.json`](dashboard-prototype-proof-matrix.json)
  owns machine-readable Appendix row outcomes.
- [Gap-Closure Prototype Experiment Plan](retired/gap-closure-prototype-experiment-plan.md)
  is retired as an active plan and retained only for historical experiment
  evidence.

This design carries forward useful questions from the original dashboard introduced in commit `93e6dff` and the Health/Insight split in `497d89d`. Its canonical attribution, cohort, identity, and source-time contracts are self-contained below.

The three dashboard groups are:

1. **Pipeline health** — whether OTEL source data arrived, correlated, attributed, reconciled, and completed through the introspection pipeline on time.
2. **Provider health** — whether a model provider accepted, served, streamed, and completed logical model requests reliably.
3. **Model usage** — how models and tools were used, where users corrected or interrupted work, which failures repeated, and which successful practices recur without deterministic codification.

## Measurement rules

Every panel must persist its question, formula, cohort, timestamp domain, dimensions, and availability state. A panel is invalid if any of these are implicit.

### Canonical identities and time

Every panel must declare its cohort and timestamp domain. The common display range is a source-event range, but context evidence used to classify a source session may have started before that range. Queries must therefore use directional cohorts:

- **Source cohort:** raw producer sessions with an authoritative source event inside the selected range. Look up matching accepted lifecycle evidence by producer/session ID without incorrectly requiring the lifecycle start event to fall inside the same display range.
- **Lifecycle cohort:** accepted lifecycle sessions whose lifecycle event occurred inside the selected range. Determine whether each has raw telemetry independently; absence of a detector finding is not absence of telemetry.
- **Activity cohort:** latest canonical activity versions whose canonical source-event timestamp is inside the selected range.
- **Scan cohort:** one logical pipeline snapshot per immutable scan/event identity inside the selected range, regardless of duplicate remote deliveries.

Producer and session joins are namespaced tuples; a bare session ID is never sufficient. Native sessions are namespaced `(producer, native_session_id)`. Reconstruct accepted lifecycle intervals by producer/session from ordered `session_start`, `workspace_changed`, and `session_end` events, using `(timestamp, event.id)` ordering and half-open `start <= source_time < end` membership. `workspace_changed` closes one interval and opens the next; `session_end` closes the active interval; an unclosed interval remains open. Future context, ended context, and a different project interval do not match. Multiple accepted events never multiply a session row.

For source coverage, select source sessions by first raw source event in the display range and look up the interval containing that source time even when its start predates the range. For lifecycle coverage, select distinct sessions with an accepted lifecycle event in the range and report whether a valid raw source event in the same display range falls inside one of their intervals. Percentages with no applicable denominator render `Not applicable`; applicable zero denominators render an explicit no-data state.

Remote source time is the ClickHouse `timestamp` UInt64 nanosecond column populated from the canonical activity OTLP `timestamp_ns`; it must equal local `canonical_activities.source_ended_at_ns` for every version. The selected interval is `timestamp > $start_timestamp_nano AND timestamp <= $end_timestamp_nano`.

First identify candidate activity IDs having a canonical version with `activity.payload_schema_version = 2` in that interval. Then load all canonical versions carrying that immutable contract metadata for those IDs, require every version of an activity to share exactly one source timestamp, and choose the greatest version globally. A later-delivered reconciliation remains in the original source-time cohort. Divergent version timestamps fail before selection.

Repeated delivery is valid only when the complete equality tuple matches: `event.id`, event name, payload schema version, activity ID/version, source timestamp, producer, surface, correlation ID, detector ID/version, normalization version, attribution state/method/evidence/reason, attribution project identity ID, and canonical project ID/name. The deterministic SHA-256 event ID must match activity ID, version, schema, and event name; exactly one event ID may represent one activity/version. Same-ID divergence, multiple event IDs for one activity/version, deterministic-ID mismatch, version gaps, and empty required identities are integrity failures. No panel may filter corrupt rows and continue.

All panels depend on one `canonical_activity_integrity` CTE/helper that validates those invariants with a proven ClickHouse fail-closed expression before aggregation. Required non-empty values are event ID, activity ID, positive version/schema, producer, surface, correlation ID, detector ID, positive detector/normalization versions, attribution state/method, and project ID/name. Evidence and reason fields follow their attribution-state contract.

Resolved project identity is keyed by `(agent.project.id, agent.project.name)` with ID authoritative and name used for display. Two IDs may share a name and remain separate. One ID with conflicting names in the cohort fails closed. Unresolved rows use canonical ID `unresolved` and display name `Unresolved`.

Logical provider requests require a stable `(provider, logical_request_id)` identity. Attempts require `(provider, logical_request_id, attempt_id)` or an immutable attempt index.

- Missing Boolean or outcome fields remain `Unknown`; they are never interpreted as `false`, `success`, or zero.

### Population and percentage rules

- Counts identify their unit: events, activities, sessions, tasks, requests, attempts, scans, or findings.
- Percentages divide like populations only. For example, request successes divide by requests, never by attempts or emitted events.
- Every numerator must be a subset of its denominator.
- Applicable zero denominators render `No data`. Unsupported producer capabilities render `Not applicable`. Unavailable required fields render `Unavailable`.
- Percentiles exclude unknown and invalid values and disclose their sample count. Negative latency becomes a separate clock-skew count.
- Overlapping signals use a distinct union for totals. Signal-specific counts may overlap and therefore must not be summed.

### Availability states

- **Canonical now** — calculable from the current immutable remote contract.
- **Local only** — calculable in the local ledger but not valid for a SigNoz dashboard until projected immutably.
- **Requires projection** — source evidence exists or can be captured, but required identity or outcome fields are not in the canonical remote contract.
- **Deferred** — the concept needs a new canonical state or registry before a trustworthy calculation exists.

## Pipeline health

Pipeline health answers whether producer evidence and introspection processing are complete, timely, and internally consistent. It does not use detector findings as a proxy for raw OTEL capture.

### P1. Canonical pipeline snapshot

**Question:** What is the latest completed state of the introspection pipeline?

**Calculation:** Select one valid canonical `introspection.pipeline.snapshot` with `pipeline.payload_schema_version = 2` per non-empty `event.id`, then select the row with the greatest `(scan.completed_at_ns, event.id)`. Display terminal status, completion time, duration, error class, rows processed, log count, trace count, context-event count, canonical-activity count, pending outbox count, failed-during-drain count, and source lag fields.

**Cohort:** Completed canonical scan snapshots. Snapshot completion time is the timestamp domain.

**Dimensions:** Deployment/runtime identity when more than one scanner exists.

**Interpretation:** The latest pipeline result. SigNoz endpoint reachability must remain separate; a reachable endpoint does not override a failed scan.

**Availability:** Requires the canonical snapshot cutover specified by the current Health plan.

### P2. Scan outcome and freshness

**Question:** Is scanning succeeding at the expected cadence?

**Calculations:**

- `scan success % = 100 × successful scans / terminal scans`.
- `scan failure % = 100 × failed scans / terminal scans`.
- `scan age = dashboard evaluation time - latest successful scan completion time`.
- `missed cadence count = max(0, floor(scan age / expected interval) - 1)` when a single fixed expected interval is configured.

**Cohort:** One logical canonical snapshot per scan identity in the selected completion-time range. Cancelled or partial states require explicit terminal-state classification.

**Dimensions:** Terminal status, error class, scanner/runtime identity.

**Interpretation:** Separates an isolated failed run from a stale or persistently failing scanner. Freshness thresholds must come from configured scheduling policy, not a dashboard constant.

**Availability:** Requires projection for canonical snapshots; expected cadence also requires configured schedule metadata.

### P3. Scan workload and normalized performance

**Question:** Is scan time increasing because the pipeline slowed down or because it processed more data?

**Calculations:**

- `rows per second = rows.processed / (scan.duration_ms / 1000)` for successful scans with positive duration.
- `milliseconds per 1,000 rows = 1000 × scan.duration_ms / rows.processed` for positive row counts.
- Report scan-duration p50/p95 and rows-processed p50/p95 with sample counts; do not mix rows and duration on one axis.

**Cohort:** Successful canonical scan snapshots in completion time.

**Dimensions:** Scanner/runtime identity, terminal status, optional source-schema fingerprint.

**Interpretation:** Raw duration indicates user-visible cost; normalized throughput distinguishes source growth/replay from processing regression.

**Availability:** Requires projection for canonical snapshots.

### P4. Source lag and capture freshness

**Question:** How far behind source OTEL is each captured signal?

**Calculation:** For each supported `(producer, surface, signal kind)`, `source lag = scan extraction upper bound - latest authoritative source timestamp at or before that bound`. Report current lag and p50/p95 across scans. Negative values are clock-skew observations, not latency.

**Cohort:** Successful scan snapshots paired with valid raw source events available at each extraction bound.

**Dimensions:** Producer, surface, signal kind (`logs`, `traces`, lifecycle), scanner/runtime identity.

**Interpretation:** A producer-specific lag spike points to capture/export delay; a simultaneous spike across producers points to collector, storage, or scanner delay.

**Availability:** Existing lag fields are partial; per-producer and per-signal values require projection.

### P5. Producer lifecycle correlation coverage

**Question:** Did lifecycle evidence and raw OTEL evidence arrive for the same producer sessions?

**Calculations:** For each capability-supported producer/surface, report:

- source sessions in range;
- source sessions with an accepted containing lifecycle interval;
- source sessions without a containing interval;
- `source-to-lifecycle coverage % = 100 × source sessions with containing lifecycle / source sessions`;
- lifecycle sessions in range;
- lifecycle sessions with valid raw source telemetry in range;
- lifecycle sessions without valid raw source telemetry in range;
- `lifecycle-to-source coverage % = 100 × lifecycle sessions with source / lifecycle sessions`.

**Cohort:** The current directional source and lifecycle cohorts. Accepted lifecycle intervals use ordered `(timestamp, event.id)` events and half-open `start <= source_time < end` membership.

**Dimensions:** Producer, surface, capability state.

**Interpretation:** The two directions diagnose different losses. Source without lifecycle indicates missing or rejected context; lifecycle without source indicates absent producer OTEL. Lifecycle-only producers show `Not applicable` for source coverage.

**Availability:** Canonical now for currently supported raw and lifecycle sources when implemented with the normative capability matrix.

### P6. Lifecycle-to-source delay

**Question:** How long after lifecycle start does the first raw source event arrive?

**Calculation:** For every matched source session, `delay_ms = (first source timestamp - containing lifecycle interval start) / 1,000,000`. Report p50, p95, matched-session count, and negative clock-skew-session count. Exclude negative values from percentiles.

**Cohort:** Source sessions whose first source event in range belongs to an accepted containing lifecycle interval, even when the interval began before the selected range.

**Dimensions:** Producer, surface, approved time bucket.

**Interpretation:** Sustained delay indicates producer initialization/export delay. Clock-skew count diagnoses invalid temporal ordering separately.

**Availability:** Canonical now under the current Health contract.

### P7. Canonical activity attribution coverage

**Question:** What share of canonical observed activities is assigned to a project?

**Calculations:**

- `eligible activities = attributed activities + unresolved activities`.
- `attribution % = 100 × distinct attributed stable activity IDs / distinct eligible stable activity IDs`.

**Cohort:** Latest valid canonical activity versions in immutable source time.

**Dimensions:** Producer, surface, attribution state, method, and rejection reason.

**Interpretation:** Measures project-attribution completeness for detector output, not producer OTEL capture. The historical observation formula is retained only after changing its population to canonical stable activities.

**Availability:** Canonical now.

### P8. Attribution diagnostics

**Question:** Why are eligible activities unresolved?

**Calculation:** Count distinct stable activity IDs by producer, surface, attribution method, state, and rejection reason. The sum across resolved and unresolved groups must equal the complete eligible activity cohort.

**Cohort:** The same activity population as P7.

**Dimensions:** Producer, surface, attribution method, state, rejection reason.

**Interpretation:** Reveals missing context, identity mismatch, interval mismatch, or unsupported attribution without turning rejection counts into a capability score.

**Availability:** Canonical now.

### P9. Late-context reconciliation

**Question:** How often did late context resolve a previously unresolved activity?

**Calculation:** Count stable activities where valid version `n` is resolved and its immediate valid predecessor `n-1` is unresolved. Count the transition once by the resolved version's deterministic event ID. Report `reconciliation rate = 100 × reconciled activities / activities ever observed unresolved` only if the denominator can load all versions for candidate activity IDs independent of the selected delivery range.

**Cohort:** Canonical activity version histories selected by immutable source time.

**Dimensions:** Producer, surface, attribution method, original rejection reason, project.

**Interpretation:** Quantifies useful late context. It does not claim idempotent no-op or failed reconciliation outcomes because those are not remotely projected.

**Availability:** Transition count is canonical now; the rate requires a proven all-version denominator.

### P10. Outbox delivery health

**Question:** Is derived telemetry waiting or repeatedly failing delivery?

**Calculations:**

- latest pending non-terminal events;
- events whose delivery attempt failed during the scan's bounded final drain;
- `pending age = scan completion time - oldest pending event creation time`;
- `drain failure % = 100 × distinct attempted events that failed / distinct events attempted during drain`.

**Cohort:** One canonical pipeline snapshot per scan. Event-level percentages require immutable drain-attempt identities.

**Dimensions:** Destination, event type, error class.

**Interpretation:** Pending includes failed attempts and must not be split into disjoint pending/failed totals. Increasing age or repeated failure indicates delivery blockage rather than detector inactivity.

**Availability:** Latest counts require the canonical snapshot projection. Age, destination, and attempt percentage require additional immutable outbox-delivery projection.

### P11. Pipeline integrity failures

**Question:** Is the dashboard population itself trustworthy?

**Calculation:** Count fail-closed integrity incidents by invariant: empty identity, same-ID divergence, multiple event IDs for one entity/version, deterministic-ID mismatch, version gap, conflicting project name, conflicting native session identity, or negative impossible count. Do not continue to normal aggregates when an affected cohort is corrupt.

**Cohort:** Every canonical event examined for a dashboard cohort or scan.

**Dimensions:** Projection/event name, invariant, producer, scanner/runtime identity.

**Interpretation:** Distinguishes absence of data from data that cannot be safely counted.

**Availability:** Requires a separate immutable rejection/integrity projection. Until then, integrity remains a deployment/query-oracle gate rather than a dashboard count.

### P12. Local ledger health

**Question:** Is the durable local introspection ledger safe and maintainable?

**Calculations:** Latest `quick_check`/`integrity_check` result and age; backup age; database bytes; WAL bytes; free-page ratio; migration state. `free-page % = 100 × freelist_count / page_count` for positive page count.

**Cohort:** Latest completed maintenance observation per database identity.

**Dimensions:** Database identity, check type, runtime host.

**Interpretation:** Restores the original SQLite integrity, size, and backup-age question without conflating it with OTEL health.

**Availability:** Local only; requires a redacted immutable maintenance projection before use in SigNoz.

## Provider health

Provider health requires logical request identity. Existing HTTP status and explicit `success=false` events can identify individual failure evidence, but event counts cannot establish request success rate, retry amplification, or latency without request/attempt boundaries.

### Required provider projection

Each immutable attempt record must carry:

- provider, producer, surface, and namespaced native session identity;
- logical request ID, attempt ID/index, and deterministic event ID;
- requested model and response model;
- request start, provider acceptance, first-token, stream end, and terminal timestamps when applicable;
- terminal outcome, HTTP status, provider error class/code, retryability, and retry reason;
- input, cached-input, output, and reasoning tokens when reported;
- stream-started and stream-completed states;
- canonical project identity or unresolved attribution state.

Unknown values remain explicit. Provider panels become available only from the forward availability boundary of this contract.

### R1. Logical request outcomes

**Question:** What share of logical model requests succeeded, failed, were cancelled, or have unknown outcome?

**Calculations:**

- `request success % = 100 × distinct successful logical requests / distinct terminal logical requests`;
- equivalent percentages for provider failure, client cancellation, timeout, and unknown terminal outcome;
- one logical request takes the terminal outcome of its final attempt, after validating attempt ordering and uniqueness.

**Cohort:** Distinct `(provider, logical_request_id)` values whose final attempt became terminal in the selected range.

**Dimensions:** Provider, requested model, response model, producer, surface, project, terminal outcome, error class.

**Interpretation:** Primary provider reliability measure. Cancellation must stay separate from provider failure unless provider evidence explicitly caused it.

**Availability:** Requires projection.

### R2. Provider error composition

**Question:** Which provider-side failures dominate?

**Calculations:** Distinct failed logical requests and failed attempts by normalized provider error class. Include:

- `rate-limit request % = 100 × logical requests ending in HTTP 429 / terminal logical requests`;
- `provider 5xx request % = 100 × logical requests ending in HTTP 500–599 / terminal logical requests`;
- authentication, quota, overload, timeout, safety/policy, invalid request, and unknown classes only when explicitly evidenced.

**Cohort:** Terminal logical requests for request rates; attempts for attempt-level diagnostic counts. Never combine the two units.

**Dimensions:** Provider, model, error class/code, HTTP status, retryability, producer, project.

**Interpretation:** Separates provider capacity/reliability from caller configuration and client cancellation.

**Availability:** Requires projection. Current raw fields support evidence discovery but not trustworthy logical-request percentages.

### R3. Provider latency phases

**Question:** Where does provider time accrue?

**Calculations:** For successful attempts with valid ordered timestamps:

- `accept latency = provider accepted - request started`;
- `time to first token = first token - request started`;
- `stream duration = stream end - first token`;
- `total provider latency = terminal - request started`.

Report p50/p95/p99 with sample count. Put negative or out-of-order timestamps in an integrity count.

**Cohort:** Successful provider attempts with the relevant timestamp pair.

**Dimensions:** Provider, requested model, response model, producer, surface, project.

**Interpretation:** Distinguishes request queueing/first-token delay from slow generation or streaming.

**Availability:** Requires projection.

### R4. Retry amplification and recovery

**Question:** How much extra provider traffic is created by retries, and do retries recover?

**Calculations:**

- `attempt amplification = total attempts / distinct logical requests`;
- `retried request % = 100 × requests with more than one attempt / logical requests`;
- `retry recovery % = 100 × retried requests whose final attempt succeeded / retried requests`;
- attempts-before-success p50/p95 for recovered requests.

**Cohort:** Logical requests whose final attempt is terminal in range, with all attempts loaded by identity even if an earlier attempt started before the range.

**Dimensions:** Provider, model, first-attempt error class, retry reason, producer, project.

**Interpretation:** High amplification with low recovery indicates harmful automatic retrying; high recovery after a short 429 interval indicates transient provider pressure.

**Availability:** Requires projection.

### R5. Streaming interruption

**Question:** How often does a provider start a response but fail to complete the stream?

**Calculation:** `stream interruption % = 100 × attempts with stream_started=true and stream_completed=false and explicit terminal failure / attempts with stream_started=true and explicit terminal outcome`.

**Cohort:** Attempts that explicitly started streaming and reached an explicit terminal outcome.

**Dimensions:** Provider, model, error class, producer, surface, project.

**Interpretation:** Detects mid-response provider or transport instability separately from rejection before first token.

**Availability:** Requires projection.

### R6. Request/response model conformance

**Question:** Did the provider serve the requested model?

**Calculation:** `model mismatch % = 100 × terminal requests with non-empty requested and response models that differ / terminal requests with both model identities present`. Also report unknown response-model count separately.

**Cohort:** Terminal logical requests with validated model identity fields.

**Dimensions:** Provider, requested model, response model, producer, project.

**Interpretation:** Exposes provider routing/substitution or telemetry provenance problems. It must not infer equivalence from aliases without an approved canonical model registry.

**Availability:** Requires projection.

### R7. Provider token throughput

**Question:** Is generation throughput degraded independently of response size?

**Calculation:** For successful attempts with positive stream duration, `output tokens per second = output tokens / stream duration seconds`. Report p50/p95 with sample count alongside output-token p50/p95. Never calculate zero throughput from missing token counts.

**Cohort:** Successful streamed attempts with explicit output-token count and valid stream duration.

**Dimensions:** Provider, response model, producer, project.

**Interpretation:** Separates slow generation from large responses and supports provider/model comparison within equivalent workloads.

**Availability:** Requires projection.

### R8. Unknown provider outcome coverage

**Question:** How much provider traffic cannot be classified?

**Calculation:** `unknown outcome % = 100 × logical requests lacking a valid terminal outcome / logical requests expected to have become terminal before a configured grace boundary`.

**Cohort:** Requests started before `evaluation time - terminal grace period`; the grace period comes from the provider contract.

**Dimensions:** Provider, model, producer, surface.

**Interpretation:** A rising unknown share usually indicates instrumentation loss or incomplete request correlation, not provider success.

**Availability:** Requires projection.

## Model usage

Model usage combines canonical activity, task, tool, and token populations. It identifies user friction and recurring behavior; it does not infer user emotion from latency, token volume, or tool failure alone.

### M1. Model calls and provenance

**Question:** Which models are used, from which producers, and with what provenance completeness?

**Calculations:** Distinct logical model requests by provider/requested model/response model; `provenance coverage % = 100 × requests with validated provider and response model / terminal logical requests`.

**Cohort:** The provider logical-request cohort.

**Dimensions:** Provider, requested model, response model, producer, surface, project.

**Interpretation:** Restores the original model-call and provenance question using request identities rather than event totals.

**Availability:** Requires the provider projection.

### M2. Token and tool-call usage pressure

**Question:** Where are input, cached-input, output, reasoning, and total tokens consumed, and which tasks use unusually many tool calls?

**Calculations:** Sum explicit token fields once per logical request's accepted accounting record; report per-request and per-task token p50/p95 plus tool calls per task p50/p95. For each metric with at least 20 comparable episodes, an outlier is strictly above the nearest-rank p95 within its declared comparison population.

**Cohort:** Terminal logical requests or canonical trace episodes with explicit token/tool-call accounting. Do not merge provider and trace token records without a reconciliation identity.

**Dimensions:** Provider, response model, producer, project, task outcome, metric, outlier state.

**Interpretation:** Shows cost/context pressure, tool-use pressure, and unusually large episodes. High volume alone is not frustration or failure.

**Availability:** Canonical trace token and tool-call totals are available for supported producers; provider/model splits and deduplicated request accounting require projection.

### M3. Explicit user-frustration task rate

**Question:** How often did a task contain explicit evidence that the user interrupted, redirected, or rejected an action?

**Calculation:** Define a frustrated task as a distinct canonical task containing at least one `turn/interrupt`, `turn/steer`, or user-sourced `codex.tool_decision`. Then `frustrated task % = 100 × distinct frustrated canonical tasks / distinct canonical tasks with observable user-turn evidence`. Count each task once in the total; retain overlapping signal counts as diagnostics.

**Cohort:** Canonical tasks in source time for producers that expose the required signals.

**Dimensions:** Producer, surface, project, explicit signal type, model when proven.

**Interpretation:** Conservative direct evidence of correction/friction. It does not claim sentiment and cannot compare producers that lack equivalent user-turn instrumentation.

**Availability:** Canonical now for supported Codex surfaces; other producer capabilities must render `Not applicable`.

### M4. Correction and interruption timing

**Question:** At what point in a task does explicit user friction occur?

**Calculations:** Tool calls and elapsed task time before the first explicit frustration signal; p50/p95 by signal type. `post-frustration recovery % = 100 × frustrated tasks with a later explicit successful terminal task outcome / frustrated tasks with an observable terminal outcome`.

**Cohort:** Frustrated canonical tasks with ordered task events. Recovery requires an explicit task outcome; absence is unknown.

**Dimensions:** Producer, project, signal type, model.

**Interpretation:** Early corrections suggest intent/alignment failure; late corrections suggest execution drift or poor validation. Recovery measures whether work got back on track.

**Availability:** Timing is calculable where task event ordering exists; terminal recovery requires projection.

### M5. Tool failure rate and failure fingerprint

**Question:** Which tools and failure classes repeatedly fail?

**Calculations:**

- `tool-call failure % = 100 × explicit failed tool calls / tool calls with explicit outcomes`;
- distinct failed canonical tasks by tool and normalized failure fingerprint;
- `task impact % = 100 × tasks containing that fingerprint / observable tool-using tasks`.

Explicit failure means `success=false`, HTTP status `>=400`, or an approved structured failure outcome. Missing outcome remains unknown.

**Cohort:** Tool calls with immutable call identity and canonical task correlation.

**Dimensions:** Producer, tool, operation kind, normalized target, normalized failure class, project, model when proven.

**Interpretation:** Separates a noisy tool with many calls from a failure fingerprint affecting many tasks.

**Availability:** Detector activities are canonical now; complete call-rate denominators and recovery require a canonical tool-call projection.

### M6. Tool failure recovery

**Question:** Do agents recover from tool failures, and at what cost?

**Calculations:** A failed operation is recovered when a later call with the same normalized operation/target in the same task explicitly succeeds. Report `recovery % = 100 × recovered failed operation groups / failed operation groups with an observable task end`, calls-to-recovery p50/p95, and time-to-recovery p50/p95. Unrecovered groups with no observable end remain unknown.

**Cohort:** Ordered tool calls grouped by canonical task and normalized operation identity.

**Dimensions:** Producer, tool, normalized failure class, project, model.

**Interpretation:** High failure with fast recovery is different from repeated dead ends; recovery cost exposes wasted interaction.

**Availability:** Requires a canonical ordered tool-call projection.

### M7. Repeated attempts

**Question:** Which operations are retried within one task?

**Calculation:** A repeated-attempt activity occurs when the same normalized operation appears at least twice in one canonical task. Report distinct affected tasks, activity count, attempts per affected task p50/p95, and explicit-success-after-repeat rate when outcomes are available.

**Cohort:** Canonical tasks with normalized ordered operations.

**Dimensions:** Producer, project, operation kind, normalized target, model when proven.

**Interpretation:** Identifies local repetition, while outcome distinguishes useful retry from waste.

**Availability:** Activity counts are canonical now; attempt distributions and outcomes require ordered tool-call projection.

### M8. Command churn

**Question:** Where do agents try many different shell commands against the same target?

**Calculation:** Command churn occurs when at least three distinct normalized shell commands operate on the same normalized target in one canonical task. Report affected tasks, distinct command count p50/p95, and terminal outcome where explicit.

**Cohort:** Canonical tasks containing normalized shell operations.

**Dimensions:** Producer, project, normalized target, command family, model.

**Interpretation:** Repeatedly changing tactics against one target can indicate missing knowledge, unstable tooling, or poor diagnosis.

**Availability:** Activities are canonical now; distribution and outcome require ordered shell-call projection.

### M9. Tool loops

**Question:** Which tasks enter repeated operation cycles?

**Calculation:** A tool loop is a contiguous repeated normalized-operation cycle: a one-operation cycle appears at least three times total; a longer cycle appears at least four operations total. Report affected tasks, cycle length, repetitions, operations consumed, and whether the task later recovered.

**Cohort:** Canonical tasks with complete ordered operation sequences.

**Dimensions:** Producer, project, cycle fingerprint, tools involved, model.

**Interpretation:** Detects structural repetition that per-tool failure rates miss.

**Availability:** Activity counts are canonical now; cycle detail and recovery require ordered tool-call projection.

### M10. Sandbox friction

**Question:** How often do sandbox or permission constraints block work?

**Calculations:** Distinct sandbox-friction activities and affected tasks by explicit outcome; `sandbox-friction task % = 100 × tasks with explicit sandbox friction / tasks with observable sandbox operations`.

**Cohort:** Canonical tasks for surfaces that emit structured sandbox outcomes.

**Dimensions:** Producer, project, sandbox outcome/class, operation, target, model.

**Interpretation:** Separates environment-policy friction from model-provider and tool implementation failures.

**Availability:** Activities are canonical now; denominator requires projection or a proven raw-operation cohort.

### M11. Quality-gate bypass

**Question:** Did mutation continue after a failed quality command before that command passed?

**Calculation:** Count canonical tasks where a quality command explicitly fails, one or more mutations follow, and the same normalized quality command later explicitly passes. Report affected tasks and complete fail-mutate-pass sequences, not merely failed quality commands.

**Cohort:** Canonical tasks with complete ordered quality-command and mutation evidence.

**Dimensions:** Producer, project, quality command, normalized target, model.

**Interpretation:** Detects unsafe practice rather than ordinary red-green development.

**Availability:** Canonical detector activities exist; complete sequence audit requires ordered operation projection.

### M12. Skill adherence

**Question:** Are applicable skills or workflow rules followed when their trigger is present?

**Calculation:** For each versioned skill rule with a deterministic trigger and observable required action, `adherence % = 100 × applicable tasks satisfying the required action / applicable observable tasks`. Report unknown observability separately; never infer non-adherence from missing instrumentation.

**Cohort:** Tasks where the rule trigger and required action are both representable in the canonical evidence contract.

**Dimensions:** Skill/rule ID and version, producer, project, model, adherence state.

**Interpretation:** Reveals repeated workflow omissions and whether guidance is effective.

**Availability:** Detector activities are canonical now; trustworthy rates require a canonical versioned rule registry and applicability projection.

### M13. Scope recurrence

**Question:** Which normalized targets recur across tasks?

**Calculation:** Scope recurrence occurs when the same normalized target appears in at least two canonical tasks. Report distinct tasks, days, projects, producers, and associated operation/failure fingerprints.

**Cohort:** Canonical attributed tasks with normalized targets.

**Dimensions:** Project, normalized target, operation kind, failure class, producer, model.

**Interpretation:** Cross-task recurrence distinguishes a persistent workflow or tooling issue from one task's local repetition.

**Availability:** Canonical activity counts are available; full target exposure must remain redacted and allowlisted.

### M14. Actionable repeated failures and practices

**Question:** Which repeated findings cross the evidence threshold for intervention?

**Calculation:** Preserve the original deterministic promotion rule over a seven-day `Europe/London` calendar window. A finding is actionable when either:

- it has at least three occurrences across at least two canonical tasks and at least two calendar days; or
- it has at least five occurrences across at least three canonical tasks.

State counts use the globally latest immutable finding version. `detector actionable yield % = 100 × distinct actionable finding IDs / distinct finding IDs evaluated for that detector`. Finding occurrence count, task count, and day count remain separate fields.

**Cohort:** Canonical findings evaluated over an explicit evidence window, keyed by versioned detector and normalized fingerprint.

**Dimensions:** Detector, finding state, producer, project, model when proven, normalized failure/practice fingerprint.

**Interpretation:** Identifies repeat failures or practices worth intervention without promoting one-off examples.

**Availability:** Local only. SigNoz use is deferred until an immutable canonical finding projection carries finding identity, detector, project, state, occurrence count, canonical task count, day count, first/last seen, evidence window, version, and deterministic event ID.

### M15. Post-intervention recurrence

**Question:** Did an applied intervention reduce the finding it targeted?

**Calculations:** Compare equal-length, exposure-matched pre/post windows around explicit application time:

- `pre recurrence rate = occurrences / observable canonical tasks in pre-window`;
- `post recurrence rate = occurrences / observable canonical tasks in post-window`;
- `relative change % = 100 × (post rate - pre rate) / pre rate` when pre rate is positive.

Always display raw counts, task denominators, and window bounds. Do not claim causality when model, producer, project, or task mix materially changes.

**Cohort:** The same finding fingerprint and comparable observable tasks before and after an intervention marked `applied` with validation evidence.

**Dimensions:** Intervention type, detector, project scope, producer, model.

**Interpretation:** Restores the original post-application recurrence question with exposure normalization.

**Availability:** Deferred until canonical finding and intervention-event projections exist.

### M16. Project concentration

**Question:** Is a repeated behavior localized or broadly distributed?

**Calculations:** For each detector/finding, report occurrences and affected tasks by canonical project. `top-project concentration % = 100 × occurrences in the highest-count project / attributed occurrences`. Report unresolved occurrences separately and exclude them from the project concentration denominator.

**Cohort:** Canonical attributed activities or finding occurrences in source time.

**Dimensions:** Detector/finding, canonical project identity, producer, model.

**Interpretation:** High concentration supports project-scoped intervention; broad distribution supports workflow- or user-level intervention.

**Availability:** Activity-level concentration is canonical now. Actionable-finding concentration requires the finding projection.

### M17. Uncodified successful practice recurrence

**Question:** Which successful operation sequences recur often enough to codify but have no deterministic owner?

**Calculation:** Build a versioned sequence fingerprint from ordered normalized operations, stable targets, and explicit successful terminal outcome. A candidate must meet the same recurrence threshold as M14 and have no matching entry in the versioned enforcement registry. Report:

- support tasks and days;
- `sequence support % = 100 × successful comparable tasks containing the sequence / successful comparable tasks`;
- `sequence success % = 100 × tasks containing the sequence with explicit success / tasks containing the sequence with explicit terminal outcome`;
- median operations/time saved against comparable successful tasks without the sequence, only when comparison populations are declared and sufficiently sized.

Subsequences that are ubiquitous setup/teardown, project-generated boilerplate, or already required by an established tool/rule are excluded by explicit registry classification, not by dashboard heuristics.

**Cohort:** Comparable canonical tasks with complete ordered operations and explicit terminal outcomes.

**Dimensions:** Sequence fingerprint/version, producer, project, task class, model, enforcement ownership state.

**Interpretation:** Finds repeat practices that may deserve established-tool configuration, a new tool, a script, a skill, or guidance. Frequency alone does not prove benefit; explicit success and comparable exposure are required.

**Availability:** Deferred until ordered operation, terminal outcome, task-class, and versioned enforcement-registry projections exist.

### M18. Enforcement-tier audit

**Question:** Are repeated failures and practices being addressed at the strongest deterministic layer available?

**Calculation:** For each actionable finding or successful-practice candidate, record exactly one selected intervention tier and explicit rejection reasons for earlier tiers in this order: established project tool, new tool, bespoke script, existing skill, new skill, guidance. Report counts by selected tier, missing-audit count, approval outcome, and post-intervention recurrence.

**Cohort:** Canonical actionable findings and successful-practice candidates with intervention decisions.

**Dimensions:** Detector/practice type, selected tier, scope, project, approval/application state.

**Interpretation:** Exposes recurring uncodified work and prevents guidance from replacing enforceable tooling.

**Availability:** Deferred until canonical finding, practice, proposal, and intervention-event projections exist.

## Reading order and cross-group diagnosis

Use the groups in this order when investigating a spike:

1. **Pipeline health:** confirm that raw evidence arrived, the producer/session correlation is applicable, attribution is complete, and scans/delivery are current.
2. **Provider health:** for valid captured requests, identify provider outcomes, error classes, latency phases, retries, and streaming failures.
3. **Model usage:** only after data and provider health are known, inspect explicit user-frustration evidence, tool failures, repeated behavior, recovery, and intervention candidates.

This order avoids blaming a model or agent for missing OTEL, treating provider outages as poor tool use, or treating absent outcomes as success.

## Initial delivery boundary

The companion presentation is delivered as five route shells from the start.
Every designed measurement, control, and supporting evidence-table place retains
its target layout and renders the repository-owned missing-data image with
`Missing data` while authoritative measurement/control data is blocked. This
presentation is not a calculation result, does not fabricate a value, and does
not promote a proof or projection gate. Static producer-specific capability
exclusions still render `Not applicable`.

Calculations are enabled incrementally without mixed-contract panels:

1. Implement **Canonical now** Pipeline health and Model usage calculations
   using the existing canonical activity, lifecycle, attribution, and
   source-time contracts.
2. Complete the forward canonical pipeline snapshot projection before enabling
   snapshot-dependent calculations.
3. Add the immutable logical provider request/attempt projection, then enable
   Provider health and request/model usage calculations only after its
   availability boundary.
4. Add ordered tool-call/task-outcome projection for recovery, sequence, and
   rate denominators.
5. Add canonical finding, intervention, and enforcement-registry projections
   before enabling actionable yield, post-intervention recurrence, uncodified
   practice, or enforcement-tier calculations.

No panel may reconstruct a missing canonical state from activity counts, join
local SQLite implicitly, or query superseded projections as a fallback.

## Visual design addendum

### Target dashboard architecture

Use five dashboard views, not tabs:

1. **01 · Pipeline health** — P1–P12 target ownership.
2. **02 · Provider health** — R1–R8.
3. **03a · Model usage · Usage & interaction** — M1–M4.
4. **03b · Model usage · Tool execution** — M5–M11.
5. **03c · Model usage · Recurrence & interventions** — M12–M18.

The three semantic groups remain Pipeline health, Provider health, and Model usage. Model usage is split because its 18 measures support three different decisions: resource/interaction review, execution diagnosis, and recurrence/intervention review. One Model usage dashboard would require a six-to-eight-screen scroll before recurrence evidence.

Separate dashboards are the baseline because installed SigNoz support for
persistent tabs, shared tab filters, focus restoration, and tab deep links is
unproven. Do not create a landing dashboard. Numeric titles preserve diagnostic
order. A `Next diagnostic step` link is optional only after installed-version
verification; static names and the dashboard list are the fallback.

This is a conceptual information architecture. Persisted dashboard identities,
routes, UUIDs, and migration sequencing are implementation concerns outside
this visual addendum. The five companion routes remain visible even when every
calculation is gated: each designed place renders the missing-data image and
`Missing data`, not an empty dashboard or a fabricated result.

Each measure has exactly one dashboard owner. A measure may use more than one
widget only when the widgets expose different contracted views of that measure.
Supporting evidence tables do not become separate measures.

### Progressive disclosure and screen density

Every dashboard reads vertically:

1. **At a glance:** at most three panels in the first viewport; show current state, population size, dominant exception, and selected context.
2. **Trend or distribution:** show persistence and concentration.
3. **Diagnostic breakdown:** rank exact producer, model, project, detector, tool, error, or fingerprint groups.
4. **Bounded evidence:** finish with a capped redacted table carrying short stable identities for investigation.

The first viewport must answer the dashboard's primary question without scrolling. Do not use generated narrative, trend arrows without a declared comparison window, or a composite health/frustration score.

The grid has 12 columns. Full diagnostic/evidence tables use width `12`; two genuinely compact panels may use `6 + 6`; the first viewport may use one full-width panel followed by two half-width panels. Graph height is `5–6`, compact table height `4–5`, and diagnostic/evidence table height `6–8`. Responsive behavior stacks panels full width. Every deployed availability wave persists its own exact non-overlapping `(x,y,w,h)` manifest. Before a calculation gate opens, its designed place remains occupied by the missing-data image and `Missing data`; it is not hidden or compacted.

### Shared panel contract

Every panel subtitle displays:

```text
Population: <unit and cohort> · Range basis: <one exact timestamp domain> · denominator=<population for a rate, when present> · n=<percentile/sample population, when present>
```

The global SigNoz time range remains the only time control. Slash-separated or ambiguous time domains are prohibited.

Visual rules:

- Recompute `All` results from canonical identities; never sum percentages or average group percentages.
- Show numerator and denominator beside every percentage.
- Show `n` beside every percentile. `n = 0` renders `No data`. `1 <= n < 20` displays the value plus `Low sample (n=x)` but prohibits ranking, alerting, or provider/model comparison. `n >= 20` permits normal display; performance comparison still requires a declared comparable-workload cohort.
- Never place counts, percentages, durations, lag, rows, and throughput on one axis.
- Use graphs only for ordered time or a bounded fixed series set. Use tables for high-cardinality identities, differing percentile cohorts, failure fingerprints, targets, sequences, and exact diagnostic states.
- More than eight visible series uses a table, not a graph. A graph/table change is a separately approved manifest, never a runtime ambiguity.
- Ranked tables show top 50 by the declared raw metric with a raw-key tie-breaker. Evidence tables show latest 100 unless a stricter existing contract applies. Titles disclose limits and complete cohort totals are calculated before `LIMIT`.
- Prompt text, response text, command output, full session IDs, and arbitrary attributes never appear. Targets, fingerprints, errors, and sequences use approved allowlisting/redaction plus a short opaque identity.

### Result and error states

| State | Meaning | Rendering |
| --- | --- | --- |
| Loading | Query in progress. | Stable skeleton; no zero values. |
| Data | Valid applicable cohort has rows. | Normal visual plus population/range subtitle. |
| No data | Applicable valid selected cohort is empty. | `No data for the selected filters and range`; preserve selections. |
| Not applicable | Static capability matrix says the producer/surface cannot supply the measure. | `Not applicable` plus capability reason. |
| Unavailable | A deployed canonical contract became incomplete or unqueryable after its availability boundary. | `Unavailable` plus missing contract field/boundary. |
| Integrity failure | A fail-closed invariant rejected the cohort. | `Results withheld` plus invariant; suppress affected aggregates. |
| Query/system error | SigNoz or ClickHouse did not produce a valid result. | `Query failed — outcome unknown`; never render zero. |

`Missing data` is a gate-presentation message, not an eighth result state:
while `Requires projection`, `Local only`, or `Deferred` authority is blocked,
the route shell renders the repository-owned missing-data image in every
designed measurement/control/evidence place. It never means `No data`, never
uses `Unavailable`, and does not claim an applicable query ran. `Unavailable`
remains reserved for a deployed canonical contract that became incomplete or
unqueryable after its availability boundary.

### 01 · Pipeline health

Pipeline uses two visible sections:

- **Scan completion/extraction-bound health:** P1, P2, P3, P10, and P4. P4 range membership is scan completion time; its lag reference is the scan extraction upper bound.
- **Source-time capture and attribution:** P5, P6, P7, P8, and P9.

If SigNoz cannot render a section text/header widget, prefix every title with `Completion time ·`, `Extraction-bound ·`, or `Source time ·`. The prefix fallback is required, not optional.

#### Pipeline full target manifest

| Widget | Type | Target `(x,y,w,h)` | Contract |
| --- | --- | --- | --- |
| P1 · Pipeline snapshot | one-row table | `(0,0,12,4)` | Latest completed snapshot state, completion time, duration, rows, logs, traces, pending, and failed-during-drain. Scan age is excluded. |
| P2 · Scan outcomes | 100% stacked bar | `(0,4,6,5)` | Terminal scan composition by scan completion time. |
| P2 · Scan freshness | one-row table | `(6,4,6,5)` | Evaluation time minus latest successful completion plus missed cadence from authoritative schedule configuration. |
| P3 · Scan duration | p50/p95 graph | `(0,9,6,5)` | Per-bucket duration p50/p95 and `n`. |
| P3 · Rows processed | p50/p95 graph | `(6,9,6,5)` | Per-bucket rows-processed p50/p95 and `n`; raw rows may be an additional approved series. |
| P3 · Normalized throughput | table | `(0,14,12,4)` | Rows/s and ms/1,000 rows with `n`. |
| P10 · Outbox snapshot | table | `(0,18,12,4)` | Snapshot-only pending and failed-during-drain counts; overlapping values are never stacked. |
| P10 · Outbox delivery detail | diagnostic table | `(0,22,12,6)` | Pending age, destination, attempted event identity, and error class only after immutable event-level outbox delivery projection. |
| P4 · Source lag | diagnostic table | `(0,28,12,6)` | Current/p50/p95 lag, `n`, and negative-skew count by producer/surface/signal; range basis scan completion, lag reference extraction bound. |
| P5 · Directional correlation | table | `(0,34,12,7)` | Source-to-lifecycle and lifecycle-to-source counts/rates in separate columns with capability state. |
| P6 · Lifecycle delay | percentile table | `(0,41,12,5)` | p50/p95, matched sessions, and clock-skew sessions. |
| P7 · Attribution coverage | table | `(0,46,12,5)` | Attributed, unresolved, eligible, and attribution percentage. |
| P8 · Attribution diagnostics | top-50 diagnostic table | `(0,51,12,6)` | Method/state/rejection reason; totals conserve P7 eligible activities. |
| P9 · Reconciliation transitions | trend | `(0,57,12,5)` | Materialized unresolved-to-resolved transitions; rate hidden until all-version denominator proven. |
| P9 · Reconciliation evidence | detail table | `(0,62,12,6)` | Producer, original reason, method, project, and short activity identity. |
| P11 · Integrity incidents | table | `(0,68,12,5)` | Only after immutable rejection/integrity projection. |
| P12 · Ledger health | table | `(0,73,12,5)` | Only after redacted immutable maintenance projection. |
| Canonical scan evidence | latest-50 table | `(0,78,12,7)` | Supporting evidence after canonical snapshot projection. |

P10's two widgets have separate contracts and gates; no widget mixes snapshot counts with event-level delivery detail.

### 02 · Provider health

The Provider health route shell and every designed place render the
missing-data image and `Missing data` until the immutable logical
request/attempt projection is remotely available and its forward boundary is
recorded. This does not enable a Provider health calculation.

#### Provider and model filters

Control order:

1. global time range;
2. `Provider`;
3. `Model role`;
4. `Model`.

`Provider` values are exact validated identities. `All providers` applies no provider predicate and recomputes each panel from canonical request/attempt identities. Empty or conflicting provider identity is an integrity failure, never `Unknown provider`.

`Model role` is `Requested` or `Response`; `Requested` is default. `Model` values are exact raw identities for the selected role. `All models` applies no predicate on that role, so every panel retains all records permitted by its own cohort, including explicit unknown. `Unknown model` applies the exact explicit-unknown predicate. R6 then shows `No data` for its both-known conformance percentage while retaining the separate unknown count. Raw aliases remain distinct until an approved registry proves equivalence.

Every panel applies the selected role consistently and states it. R6 filters the selected side and displays the other side.

Preferred dependent options refresh after Provider or Model role changes. Preserve an existing Model value even when the new combination is impossible, show `No data`, and offer `Reset model`. If installed SigNoz forces reset, reset only Model to `All`, visibly announce `Model reset because it is unavailable for <provider>/<role>`, and require browser proof. Time changes never reset it.

Fallback when dependent searchable variables are unsupported:

- expose independent `Requested model` and `Response model` controls;
- default both to `All`;
- apply both exact predicates;
- preserve impossible combinations and show `No data`;
- never fabricate cascading behavior.

#### Exact manifest

| Widget | Type | `(x,y,w,h)` | Contract |
| --- | --- | --- | --- |
| R1 · Final outcomes | 100% stacked graph | `(0,0,8,5)` | Logical requests whose unique final attempt became terminal in range; range predicate is final-attempt terminal timestamp. |
| R1 · Outcome summary | table | `(8,0,4,5)` | Counts/rates over the same terminal logical-request denominator. |
| R8 · Unknown coverage trend | graph | `(0,5,8,5)` | Grace-eligible requests by request-start time. |
| R8 · Unknown coverage summary | table | `(8,5,4,5)` | Numerator/denominator separate from R1. |
| R2 · Failed logical requests | table | `(0,10,6,6)` | Final request error class/status. |
| R2 · Failed attempts | table | `(6,10,6,6)` | Attempt population; never added to request counts. |
| R3 · Latency phases | percentile table | `(0,16,12,6)` | Accept, TTFT, stream, total p50/p95/p99, each with own `n`; separate negative-duration and out-of-order timestamp counts. |
| R4 · Retry behavior | table | `(0,22,12,5)` | Amplification, retried %, recovery %, attempts-before-success p50/p95/`n`. |
| R5 · Streaming interruption trend | graph | `(0,27,8,5)` | Explicit-terminal stream-start attempt population. |
| R5 · Streaming interruption summary | table | `(8,27,4,5)` | Exact numerator/denominator. |
| R6 · Model conformance | long-form table | `(0,32,12,6)` | Requested model, response model, mismatch requests, denominator, mismatch percentage, and unknown-response count. An accessible matrix replacement requires a separately approved manifest and browser proof. |
| R7 · Output throughput | percentile table | `(0,38,6,6)` | Output tokens/s p50/p95/`n` for successful streamed attempts. |
| R7 · Output volume | percentile table | `(6,38,6,6)` | Output tokens p50/p95/`n` for its declared eligible cohort. |
| Provider evidence | latest-100 table | `(0,44,12,7)` | Redacted request/attempt identity, outcome, models, latency, retry, and error. |

R3 stays a table because phase cohorts differ. R7 uses tables by default; a graph replacement is a separately approved exact manifest. `All` aggregates remain descriptive and stratified values remain visible because mixed workloads can reverse comparisons.

### 03a · Model usage · Usage & interaction

Use global time plus `Project`. Provider/model controls retain their designed
places and render the missing-data image and `Missing data` until every included
population has a canonical model association.

Visible domain sections:

- **Request/accounting-time usage:** M1 uses logical-request terminal time. Request-accounted M2 uses the same exact accounting record/time contract.
- **Canonical task source-time interaction:** trace-accounted M2 declares its trace episode source-time domain; M3 and M4 use canonical task source time.

If section headers are unsupported, prefix each title with `Request terminal time ·`, `Trace source time ·`, or `Task source time ·`.

#### Usage and interaction full target manifest

| Widget | Type | `(x,y,w,h)` | Contract |
| --- | --- | --- | --- |
| M1 · Provenance summary | table | `(0,0,12,4)` | Request/model provenance coverage; renders missing-data presentation until the provider projection carries project. |
| M2 · Request-accounted token pressure | table | `(0,4,6,5)` | Per-request input, cached-input, output, reasoning, and total tokens; terminal/accounting-time population only. Tool calls are excluded. |
| M3 · Explicit-friction rate | table | `(6,4,6,5)` | Capability-comparable task numerator/denominator. |
| M1 · Calls over time | graph | `(0,9,12,5)` | Logical requests by terminal time. |
| M2 · Trace-accounted pressure | table | `(0,14,12,5)` | Trace episode token/tool-call p50/p95, outliers, and source-time basis; never merged with request accounting. |
| M4 · Friction timing/recovery | table | `(0,19,12,6)` | Timing columns first; recovery columns only after explicit task-outcome projection. |
| Friction evidence | latest-100 table | `(0,25,12,7)` | Explicit signal, source time, producer, project, task short ID, and explicit outcome. |

The first viewport has exactly M1 summary, M2 request-accounted pressure, and M3. While their authority is blocked, each retains its designed place with the missing-data image and `Missing data`; no widget is compacted upward or treated as trace-accounted data.

### 03b · Model usage · Tool execution

Use global time plus `Project`. The first viewport is exception-first: failure impact, recovery, and repetition.

#### Tool execution full target manifest

| Widget | Type | `(x,y,w,h)` | Contract |
| --- | --- | --- | --- |
| M5 · Tool failure | top-50 table | `(0,0,12,6)` | Failure fingerprint, failed calls, affected tasks, and task impact after full denominator projection. |
| M6 · Recovery | table | `(0,6,6,5)` | Recovery %, calls/time-to-recovery, unknown task ends. |
| M7 · Repeated attempts | table | `(6,6,6,5)` | Affected tasks, attempt p50/p95, explicit-success-after-repeat. |
| M8 · Command churn | top-50 table | `(0,11,12,6)` | Target, distinct commands, affected tasks, terminal outcome. |
| M9 · Tool loops | top-50 table | `(0,17,12,6)` | Cycle fingerprint/length/repetitions/operations/recovery. |
| M10 · Sandbox friction | table | `(0,23,6,5)` | Explicit outcomes and affected-task denominator. |
| M11 · Quality-gate bypass | table | `(6,23,6,5)` | Complete fail-mutate-pass sequence. |
| Tool evidence | latest-100 table | `(0,28,12,7)` | Redacted operation/target/failure/outcome and short identities. |

M5–M11 use tables because their identities are high cardinality. Full measure titles and gated columns appear only after their complete contracts open.

### 03c · Model usage · Recurrence & interventions

Use global time plus `Project`. The route shell is always present in navigation.
Each designed place renders the missing-data image and `Missing data` until its
owned M12–M18 calculation is remotely available. It never joins local SQLite.

#### Recurrence and interventions full target manifest

| Widget | Type | `(x,y,w,h)` | Contract |
| --- | --- | --- | --- |
| M14 · Actionable findings | ranked table | `(0,0,6,6)` | Occurrences, tasks, days, state, window, detector, fingerprint. |
| M17 · Successful practices | ranked table | `(6,0,6,6)` | Support, explicit success, comparison population, owner state, sequence ID. |
| M13 · Scope recurrence | top-50 table | `(0,6,12,6)` | Targets across tasks/days/projects with operation/failure fingerprint. |
| M16 · Project concentration | table | `(0,12,12,6)` | Distribution and top-project concentration; unresolved outside denominator. |
| M12 · Skill adherence | table | `(0,18,6,6)` | Applicable, satisfied, unknown observability, versioned rule. |
| M18 · Enforcement-tier audit | table | `(6,18,6,6)` | Selected tier, earlier-tier rejection reasons, state, missing audit. |
| M15 · Pre/post recurrence | table | `(0,24,12,6)` | Raw counts, observable-task denominators, equal windows, rates, comparability; no causal label. |
| Candidate/intervention evidence | latest-100 table | `(0,30,12,7)` | Redacted identity and state history. |

The full first viewport places actionable failures beside uncodified successful practices. An earlier Wave-A manifest with only M13/M16 places them side by side as the first viewport.

### Project filter contract

All three Model usage dashboards use the same control:

- `All projects` applies no project predicate and includes resolved plus Unresolved.
- Resolved option value is canonical project ID; label is `<canonical project name> · <short project ID>`.
- `Unresolved` selects canonical ID/state `unresolved`; it is never empty-string, corruption, or fallback.
- Conflicting project identity fails closed.
- Selection filters each canonical record population by the project carried by
  that record. Provider-request panels retain their designed places with
  missing-data presentation until that projection carries project identity.
- A valid empty cohort shows `No data` and preserves selection.
- Time changes never reset project.

Cross-dashboard state transfer is optional after URL-state proof. Manual reselection with identical controls is the baseline.

### Availability constraints

- Render every designed measurement, control, and supporting evidence-table
  place. When its complete canonical population and required fields are
  blocked, show the repository-owned missing-data image and `Missing data`
  instead of a disabled widget, zero-filled placeholder, `No data`, or
  `Unavailable`.
- Never combine snapshot counts with event-level detail, request accounting
  with trace/task accounting, or activity counts with finding/intervention state
  in one visual.
- A count-only view uses an explicit `Observed ... counts` title and omits
  unavailable rate, recovery, sequence, or outcome columns.
- Keep designed placements while authority is missing; only a deployed
  availability-wave manifest may define calculated-widget compaction.
- Record unsupported SigNoz interactions as constraints with the fallbacks in
  this addendum; implementation sequencing remains outside this visual design.

### Navigation, accessibility, and fallbacks

Diagnostic order:

```text
01 Pipeline health
  -> 02 Provider health
  -> 03a Usage & interaction
  -> 03b Tool execution
  -> 03c Recurrence & interventions
```

Users may enter any dashboard directly. Evidence tables are the reliable drill-down. Panel click-to-filter, Explore links, URL-carried filters, and copyable deep links are gated enhancements. Without them, show short stable IDs and visible filter/range values for manual reproduction. Do not use tabs, hidden accordions, hover-only evidence, or legend selection as the only detail path.

Outcome color is paired with status text and, where supported, icon or line pattern. Use one semantic palette across boards. `Not applicable`, `Unavailable`, `Integrity failure`, and `Query/system error` are text states, not failure shades. Tables include units, deterministic sort, keyboard-reachable rows, and visible focus. Sticky headers, pagination/export, accessible patterns, and responsive behavior require installed-version proof; capped full-width tables, full legends, and vertical stacking are fallbacks. At 200% zoom, status text, units, filter values, and evidence IDs remain visible.

Authoritative schedule cadence comes from scanner configuration. Provider terminal grace comes from the approved provider contract. Comparable-workload claims require explicit task/workload class in the projection; otherwise show stratified descriptive values and omit comparative language.

### Adversarial review resolution

The data-analysis review initially preferred three dashboards for population continuity. The UX review rejected the resulting 18-measure Model usage wall. Cross-review converged on five because the additional dashboards own distinct decisions, share exact time/project filter semantics, and duplicate no measure.

Subsequent review resolved:

- mixed snapshot/event, request/trace, activity/finding, and partial/full contracts with separate visuals or explicit omission;
- P1/P2 ownership by keeping scan age only in P2;
- P3 workload requirements with rows-processed p50/p95/`n`;
- table density with full-width diagnostic/evidence tables;
- invalid timestamp semantics with separate negative-duration and out-of-order counts;
- requested/response ambiguity with role-aware exact model filtering;
- model-filter invalidation with preserved selection and explicit reset behavior;
- mixed timestamp domains with section/prefix fallbacks and exact subtitles;
- low samples with visible values plus prohibited comparison below 20;
- missing/corrupt data with seven distinct non-zero states.

The visual target has no unresolved design issue. Runtime capability and data-projection gaps remain explicit constraints with defined fallbacks or panel omission.

## Appendix A: dashboard mock control data readiness

The [Dashboard Metric-to-Proof Register](dashboard-metric-proof-register.md)
records the current human-readable proof status and remaining action for every
unproven `High-level gap closure`. The
[Gap-Closure Prototype Experiment Plan](retired/gap-closure-prototype-experiment-plan.md)
retains the historical experiments that tested those closures. Machine-readable
Appendix row outcomes remain in
[`dashboard-prototype-proof-matrix.json`](dashboard-prototype-proof-matrix.json).
No historical experiment table opens an implementation gate; the active
[React and SigNoz Companion Implementation Plan](react-signoz-companion-implementation-plan.md)
remains gated by the applicable row-level proof.

This appendix inventories every measurement-bearing panel and data filter in
the local [dashboard mock](mock/show-me-signoz-dashboard-mocks.html).
Navigation, refresh, and overflow-menu controls are excluded because they do
not form measurement output.

`Fields exist?` answers the schema question only:

- **Yes** means every semantic input needed by the displayed calculation already
  exists in the current raw, canonical activity, pipeline, or local durable
  schemas. A query, reducer, or remote projection may still need to be built, and
  the fields may not yet have sufficient observations.
- **No** means at least one required identity, timestamp, outcome, registry, or
  relationship is absent or has not been proven in the current schemas. More data
  with the same shape cannot complete the calculation.

The status does not permit a dashboard to join local SQLite implicitly. Where a
**Yes** row depends on fields that are currently local or only available during
reduction, implementation must still publish a population-conserving remote
projection before SigNoz can query it.

`High-level gap closure` is grounded in the current repository, a live 30-day
SigNoz raw-attribute audit, and the installed producer adapters. SigNoz can query
arbitrary string, number, and boolean attribute maps with ClickHouse SQL. Complex
multi-event identities and outcomes must nevertheless be reduced once into
immutable canonical events rather than reconstructed independently by panels.
The installed Codex CLI, Codex app-server, Claude Code, and OMP adapters are
attribution/lifecycle adapters; they do not observe provider requests, tool calls,
model accounting, or terminal task outcomes and must not be overloaded with
facts they cannot authoritatively supply.

| Dashboard mock tab | Control | Fields required | Calculation used | Fields exist? | Required change when fields do not exist | High-level gap closure |
| --- | --- | --- | --- | --- | --- | --- |
| 01 · Pipeline health | Time range | Snapshot completion timestamp; canonical activity source timestamp; source-session timestamp | Apply the selected half-open range to each section's declared timestamp domain. | Yes | — | Query the current canonical timestamps directly; no schema change. |
| 01 · Pipeline health | Latest pipeline snapshot | Stable scan/event ID; snapshot schema version; completion timestamp; terminal status; duration; error class; total, log, trace, context-event, and canonical-activity counts; pending outbox count; failed-during-drain count; source lags | Select the greatest valid `(scan.completed_at_ns, event.id)` snapshot and display its fields; `rows/s = rows.processed / (duration_ms/1000)`. | No | Extend the application scan snapshot projector with a payload schema version, per-population counts, and drain-failure count. These are scanner/reducer changes; no producer change is required. | Extend `_PipelineSnapshotRequest` and `_snapshot_attributes`; project the existing `scan_runs.details_json` log, trace, hydration, context, activity, trend, source-session, and conservation counts, add payload version, and persist failed selected drain-event count. |
| 01 · Pipeline health | Scan outcomes | Stable scan identity; completion timestamp; explicit terminal class; error class | For each terminal class, `100 × distinct scans in class / distinct terminal scans`; group the same population over completion time. | No | Extend the scanner terminal-state contract to represent every displayed class, including partial or cancelled states, then publish it in the canonical snapshot. No producer change is required. | Expand the `scan_runs.status` state machine beyond `running/succeeded/failed/no_data`, migrate its constraint, and snapshot explicit partial/cancelled terminal classes. |
| 01 · Pipeline health | Freshness & cadence | Successful scan completion timestamp; evaluation timestamp; expected interval and schedule-policy identity; terminal scan identity | `scan age = evaluation time - latest successful completion`; `missed cadence = max(0, floor(scan age / expected interval) - 1)`; count terminal scans. | No | Publish authoritative schedule cadence and policy identity from scanner configuration with the snapshot or a versioned schedule projection. No producer change is required. | Emit a versioned scanner schedule-policy event from `SchedulerConfig.interval_seconds`, keyed by scanner/runtime identity, and join it to snapshots. |
| 01 · Pipeline health | Scan workload & duration | Stable scan identity; completion timestamp; terminal status; `scan.duration_ms`; `rows.processed` | Successful-scan duration and row-count p50/p95/`n`; `rows/s = rows / (duration_ms / 1000)` for positive duration. | Yes | — | Query current snapshot fields; no schema change. |
| 01 · Pipeline health | Source lag by producer | Scan extraction upper bound; producer; surface; signal kind; latest authoritative source timestamp at or before the bound | Per `(producer, surface, signal)`, `lag = extraction upper bound - latest source timestamp`; report current, p95, `n`, and negative clock-skew count. | Yes | — | Add a scan-time reducer over existing source-session/log/trace identities and timestamps, then publish per-producer/surface/signal lag; no producer change. |
| 01 · Pipeline health | Producer lifecycle correlation | Producer; surface; canonical native session ID; source-session first timestamp; lifecycle interval start/end and event IDs; capability state | Count both directional session cohorts and matched members; each coverage is `100 × matched / directional cohort`. | Yes | — | Query the current source-session and lifecycle projections with the normative capability matrix. |
| 01 · Pipeline health | Attribution coverage | Stable activity ID and latest version; source timestamp; attribution state; canonical project ID | `eligible = attributed + unresolved`; `attribution % = 100 × distinct attributed activity IDs / distinct eligible activity IDs`; count distinct resolved projects. | Yes | — | Query latest canonical activity versions; the current remote attribution/project fields are sufficient. |
| 01 · Pipeline health | Attribution diagnostics | Stable activity ID and latest version; producer; surface; attribution state/method; rejection reason | Count distinct eligible activity IDs by rejection reason; grouped totals must conserve the P7 population. | Yes | — | Query the same latest activity population as P7 and enforce population conservation. |
| 02 · Provider health | Time range | Logical request ID; final-attempt terminal timestamp; request start timestamp for grace-eligible unknowns | Apply the selected range to final-attempt terminal time; load all attempts for selected request identities regardless of earlier starts. | No | Build the logical request/attempt contract. Audit authoritative supported-producer raw attributes; instrument its boundary if stable request ID or terminal timestamps are absent. Claude Code stays blocked until E-Attribution-3 freshly proves hook=artifact=OTEL identity. | Map authoritative supported-producer IDs, attempts, and terminal evidence; add native Codex OTEL or a separate authoritative supported-producer request adapter for logical-request grouping; emit one canonical request/attempt event family. Claude Code remains excluded until E-Attribution-3 freshly proves hook=artifact=OTEL identity. |
| 02 · Provider health | Provider | Canonical provider identity on every request and attempt | Apply an exact provider predicate; `All` removes the predicate without excluding unknown provider values. | No | Map an existing authoritative provider attribute if present; otherwise add it in the producer/adapter. Publish it on the request/attempt projection. | Map authoritative supported-producer provider attributes through a versioned provider registry into the request/attempt projection; instrument only supported producers lacking an authoritative value. Claude Code remains excluded until E-Attribution-3 freshly proves hook=artifact=OTEL identity. |
| 02 · Provider health | Model role | Requested model and response model as separate validated fields | Select which exact model field the Model filter predicates; preserve unknown values separately. | No | Audit raw model attributes, then map or instrument separate requested- and response-model fields in the producer/adapter and request projection. | Map authoritative supported-producer requested/response fields separately; add native telemetry where a supported producer does not distinguish the two roles, then project both fields without alias inference. Claude Code remains excluded until E-Attribution-3 freshly proves hook=artifact=OTEL identity. |
| 02 · Provider health | Model | Requested model; response model; selected model role | Apply exact identity equality to the role-selected model field; `Unknown model` selects an explicit missing/unknown state. | No | Same request/model contract as Model role; do not derive aliases without a versioned canonical model registry. | Use the role-separated request projection, preserve unknowns, and compare exact producer model identities; introduce a versioned registry only if approved aliases exist. |
| 02 · Provider health | Logical request outcomes | Provider; logical request ID; attempt ID/index; final-attempt order; terminal outcome/timestamp; error/cancellation class; project | One outcome per validated final attempt; `outcome % = 100 × distinct terminal logical requests in outcome / distinct terminal logical requests`; group by terminal time. | No | Add stable request and attempt/retry identities plus explicit terminal outcomes. Map existing raw IDs; instrument only ambiguous producer/adapter boundaries; publish canonical projection. | Reduce authoritative supported-producer request IDs, attempts, success/finish evidence, and timestamps to final outcomes; add native logical-request/retry identity where absent; attach canonical project attribution before immutable request versions. Claude Code is excluded until E-Attribution-3 freshly proves hook=artifact=OTEL identity. |
| 02 · Provider health | Outcome summary | Same R1 request/outcome fields; request start; terminal grace policy | Reuse R1 counts and percentages; `unknown % = 100 × grace-eligible requests without valid terminal outcome / all grace-eligible requests`. | No | Add the request/attempt projection and a versioned provider terminal-grace contract. Producer/adapter instrumentation is required only where start or terminal evidence is absent. | Reuse the canonical request reducer and add a versioned per-provider terminal-grace policy; classify unknown only after the grace boundary. |
| 02 · Provider health | Provider error composition | Logical request and attempt identities; final outcome; HTTP status; provider error code/class; retryability; provider/model/project | Count distinct failed logical requests by normalized final error; request-rate percentages divide by terminal logical requests, never attempts. | No | Map raw status/error attributes into a versioned provider-error taxonomy; instrument producer/adapter emission where explicit error semantics are absent; publish final-request error fields. | Normalize observed HTTP status, success, finish reason, and error attributes through a versioned taxonomy in the request reducer; add producer telemetry only for missing explicit error semantics. |
| 02 · Provider health | Latency phases | Attempt ID; request start; provider-accepted; first-token; stream-end; terminal timestamps; successful outcome; provider/model/project | For valid ordered pairs calculate accept, first-token, stream, and total latency; report p50/p95/p99/`n` and count negative or out-of-order pairs separately. | No | Add missing phase markers at the producer/adapter boundary where they are not already emitted, then preserve them in the attempt projection. Joins alone cannot infer unobserved phase boundaries. | Reuse observed request start/end and TTFT/first-chunk fields; add authoritative provider-accepted and stream-end markers natively where absent, then validate timestamp ordering in the attempt reducer. |
| 02 · Provider health | Retry behavior | Logical request ID; ordered attempt IDs/indexes; final outcome; first-attempt error; retry reason | `amplification = attempts / requests`; `retried % = 100 × requests with >1 attempt / requests`; `recovered % = 100 × retried requests whose final attempt succeeded / retried requests`. | No | Add or map stable logical-request membership, attempt order, and retry reason. Instrument the producer/adapter if attempts cannot be related unambiguously; then project complete attempt sets. | Use authoritative supported-producer request IDs and attempt fields; add supported-producer logical-request grouping/retry reason at its native request boundary where absent, then materialize complete ordered attempt sets. Claude Code remains excluded until E-Attribution-3 freshly proves hook=artifact=OTEL identity. |
| 02 · Provider health | Requested → response model conformance | Logical request ID; requested model; response model; terminal outcome | Group requests by exact requested/response pair; `mismatch % = 100 × both-known unequal pairs / both-known pairs`; unknown response model stays separate. | No | Add or map separate requested/response model fields and publish them on the request projection; add a model registry only for approved alias equivalence. | Populate separate role fields through the same producer mapping/instrumentation as Model role and compare exact canonical identities in the request reducer. |
| 03a · Model usage · Usage & interaction | Time range | Request final-terminal timestamp for M1; trace-source timestamp for trace-accounted M2; canonical task source timestamp for M3/M4 | Apply the selected range to each section's declared request-terminal, trace-source, or task-source domain. | No | The trace-source and task-source fields exist; add the canonical provider request/attempt projection to supply request terminal time. | Keep current trace-source time for trace-accounted M2 and task source time for M3/M4; use final-attempt terminal time from the new canonical request projection for M1. |
| 03a · Model usage · Usage & interaction | Project | Canonical project ID or explicit unresolved state on logical requests and canonical tasks | Exact project-ID predicate; `All projects` removes the predicate and retains unresolved records. | No | Task/activity attribution exists. Carry the same canonical project identity or unresolved state onto the provider request projection. | Resolve request correlation through the existing session/project attribution boundary and emit canonical project ID or unresolved on every request version. |
| 03a · Model usage · Usage & interaction | Model calls & provenance | Logical request ID; provider; requested/response model; producer/surface; project; terminal outcome/time; token counts | Count distinct terminal logical requests; `provenance % = 100 × requests with validated provider and response model / terminal requests`; sum tokens once per accepted request accounting record; count exact/unknown models. | No | Build the provider request/accounting projection. Map existing raw token/model attributes and instrument producer/adapter fields where request identity, provider, or response model is absent. | Aggregate the canonical request/accounting projection from authoritative supported-producer fields; add stable request grouping and role-separated model/accounting identity where absent. Claude Code remains excluded until E-Attribution-3 freshly proves hook=artifact=OTEL identity. |
| 03a · Model usage · Usage & interaction | Request-accounted token pressure | Logical request ID; accepted accounting-record identity; input, cached-input, output, and reasoning tokens; provider/model/project | Per token metric, report per-request p50/p95/`n`; when `n >= 20`, count values strictly above nearest-rank p95. | No | Add deduplicated request accounting identity and cached-input tokens to the provider projection. Instrument producer/adapter token fields only where the raw producer does not emit them. | Map authoritative supported-producer request-span token fields to one accepted accounting record; add supported-producer request-accounting identity where current turn totals cannot be assigned safely to individual provider requests. Claude Code remains excluded until E-Attribution-3 freshly proves hook=artifact=OTEL identity. |
| 03a · Model usage · Usage & interaction | Explicit user-friction rate | Canonical task ID; source timestamp; producer/surface capability; project; `turn/steer`, `turn/interrupt`, and user-sourced `codex.tool_decision` signals | A task is frustrated when it contains at least one qualifying signal; `100 × distinct frustrated tasks / distinct tasks with observable user-turn evidence`; retain overlapping signal counts. | Yes | — | Materialize the canonical observable-task denominator from current supported Codex signals and detector/task identities; no producer schema change. |
| 03a · Model usage · Usage & interaction | Model calls over time | Logical request ID; requested model; final-attempt terminal timestamp; provider/project | Count distinct terminal requests by requested model and time bucket. | No | Same canonical provider request projection as Model calls & provenance. | Group the new canonical request projection by terminal-time bucket and requested model. |
| 03a · Model usage · Usage & interaction | Correction timing & recovery | Canonical task ID; ordered event timestamps; task start; ordered tool calls; first friction signal; explicit terminal task outcome; project/model | Per frustrated task, count tools and elapsed time before first signal; report medians; `recovery % = 100 × frustrated tasks with later success / frustrated tasks with observable terminal outcome`. | No | Preserve ordered task/tool events and add an explicit terminal task-outcome projection. Instrument the producer/adapter where terminal outcome is not emitted. | Build an immutable ordered task/tool event projection from current IDs, timestamps, hydrated operations, and friction signals; add explicit terminal task outcome through native telemetry or a separate authoritative task hook, not the lifecycle adapters. |
| 03b · Model usage · Tool execution | Time range | Canonical task and operation source timestamps | Apply the selected half-open source-time range to canonical tasks/operations. | Yes | — | Query current source timestamps or their canonical ordered-operation projection. |
| 03b · Model usage · Tool execution | Project | Canonical project ID or unresolved state on task/operation evidence | Apply an exact canonical project-ID predicate; `All projects` includes unresolved. | Yes | — | Carry the existing activity attribution onto the ordered-operation projection; no producer change. |
| 03b · Model usage · Tool execution | Tool failure fingerprints | Immutable call/event ID; canonical task ID; tool; explicit outcome/status; normalized operation, target, and failure class; source timestamp; project | Group explicit failed calls and distinct tasks by fingerprint; `task impact % = 100 × tasks with fingerprint / observable tool-using tasks`; compare equivalent prior-range counts for trend. | Yes | — | Project current local observation fields plus hydrated call outcomes and task identities into immutable remote tool-call/activity records. |
| 03b · Model usage · Tool execution | Failure recovery | Canonical task ID; ordered call IDs/timestamps; normalized operation/target; explicit failure and later success; explicit task end | A failure group recovers when a later same-operation/target call succeeds; report `100 × recovered groups / groups with observable task end`, calls-to-recovery p50, and time-to-recovery p50. | No | Add an ordered tool-call projection and explicit terminal task outcome. Reuse current normalized operation fields; instrument producer/adapter outcomes only where they are absent. | Reuse current call IDs, timestamps, hydrated arguments/results, normalized operations, and task IDs in the ordered tool-call projection; add explicit task end/outcome natively or through an authoritative task hook. |
| 03b · Model usage · Tool execution | Repeated attempts | Canonical task ID; ordered normalized operations; call identity; explicit outcomes | Select task/operation groups with at least two occurrences; count affected tasks; report attempts p50 and `success after repeat % = successful repeated groups / repeated groups with explicit outcome`. | Yes | — | Persist the existing `DetectorEvent` ordering, operation identity, and explicit outcomes in the canonical tool-call projection. |
| 03b · Model usage · Tool execution | Command churn | Canonical task ID; normalized shell command identity; normalized target; project | Select task/target groups with at least three distinct normalized commands; count affected tasks by allowlisted target. | Yes | — | Project the current command-churn detector membership and redacted normalized target fields remotely. |
| 03b · Model usage · Tool execution | Tool loops | Canonical task ID; complete ordered normalized-operation sequence; call IDs; terminal task outcome | Detect contiguous repeated cycles; group by cycle fingerprint; report tasks, cycle length, operations consumed, and `recovered %` over tasks with observable outcomes. | No | Preserve complete ordered operations and explicit terminal task outcomes in a remote projection. Instrument producer/adapter terminal outcomes where absent. | Persist complete current operation ordering and loop membership, then add explicit terminal task outcome through native telemetry or an authoritative task hook before calculating recovery. |
| 03b · Model usage · Tool execution | Sandbox friction | Canonical task ID; structured sandbox outcome/class; operation; target; producer capability; project | Count affected tasks and permission-denied outcomes; `task % = 100 × tasks with explicit sandbox friction / tasks with observable sandbox operations`. | Yes | — | Project existing Codex sandbox outcomes and the observable-operation denominator with producer capability state; unsupported surfaces remain not applicable. |
| 03b · Model usage · Tool execution | Quality-gate bypass | Canonical task ID; ordered normalized quality commands with exit outcomes; mutation events; target/project | Count tasks and complete sequences where a quality command fails, mutation follows, and the same normalized command later passes. | Yes | — | Publish the existing detector's fail/mutate/pass membership and hydrated ordered events as immutable remote evidence. |
| 03c · Model usage · Recurrence & interventions | Time range | Activity source timestamp; finding first/last-seen timestamps and evidence window; practice/intervention timestamps | Apply the selected source/evidence range without mixing activity, finding, or intervention timestamp domains. | No | Activity/finding timestamps exist; add canonical successful-practice and intervention-event schemas with explicit timestamps. | Emit immutable successful-practice and enriched intervention events with explicit evidence/application timestamps; retain existing activity and finding timestamp domains. |
| 03c · Model usage · Recurrence & interventions | Project | Canonical project ID or unresolved state on activities, findings, practices, and interventions | Apply exact project identity; `All projects` includes unresolved. | No | Activity/finding project fields exist; carry project identity onto future practice and intervention projections. | Join local finding/proposal state through existing canonical attribution and include project ID or unresolved on practice and intervention events. |
| 03c · Model usage · Recurrence & interventions | Actionable repeated failures | Finding ID/version; detector; normalized fingerprint; project; occurrence count; canonical task count; local-day count; first/last seen; evidence window; state | A finding is actionable with `>=3` occurrences across `>=2` tasks and `>=2` days, or `>=5` occurrences across `>=3` tasks`; display globally latest immutable finding version. | Yes | — | Emit the already-persisted local finding identity, counts, days, task counts, version, state, project, and evidence window as an immutable remote finding projection. |
| 03c · Model usage · Recurrence & interventions | Uncodified successful practices | Complete ordered normalized operations; sequence fingerprint/version; stable targets; explicit terminal success; task class; project/model; support tasks/days; versioned enforcement owner | Select successful sequences meeting M14 recurrence thresholds with no registry owner; calculate support and success percentages over comparable tasks. | No | Add ordered operation and terminal task-outcome projections, a task-class contract, and a versioned enforcement registry. Instrument producer/adapter outcomes only where absent. | Combine the ordered task/tool projection with explicit terminal success, add a canonical task-class contract and versioned enforcement registry, then fingerprint and promote qualifying sequences. |
| 03c · Model usage · Recurrence & interventions | Scope recurrence | Canonical task ID; normalized allowlisted target; operation/failure fingerprint; source day; project; producer | Select targets appearing in at least two canonical tasks; count distinct tasks by target and retain day/project/producer dimensions. | Yes | — | Publish existing observation task/project/fingerprint and redacted normalized-target fields remotely, then aggregate across tasks. |
| 03c · Model usage · Recurrence & interventions | Project concentration | Stable activity or finding ID; detector/fingerprint; canonical project ID or unresolved state | `top-project concentration % = 100 × highest resolved-project occurrence count / all attributed occurrences`; report unresolved separately. | Yes | — | Query current activity attribution for activity-level concentration; use the remote finding projection for actionable-finding concentration. |
| 03c · Model usage · Recurrence & interventions | Skill adherence | Versioned rule ID; deterministic trigger; required action; canonical task ID; observability state; satisfied state; project/producer | Per rule, `adherence % = 100 × applicable tasks satisfying required action / applicable observable tasks`; report unknown observability separately. | No | Add a versioned rule/applicability registry and canonical applicability projection. This is an application-policy schema change, not producer instrumentation unless required actions are unobservable. | Build the application-owned versioned rule/applicability registry and exact skill-action projection; current mentioned/selected skill counts are insufficient, and lifecycle adapters do not observe skill IDs. |
| 03c · Model usage · Recurrence & interventions | Enforcement-tier audit | Actionable finding/practice ID and version; ordered tier evaluation; selected tier; rejection reason for each earlier tier; approval/application state; project; recurrence result | Require exactly one selected tier per candidate; count selected tiers and missing audits, retaining approval and post-intervention state. | No | Add canonical practice, proposal, intervention, and tier-audit schemas with explicit earlier-tier rejection reasons. This is an application workflow/projection change, not a producer change. | Project the existing validated `ProposalInput.established_tool_audit`, proposal event history, finding/project identity, selected tier, and earlier-tier reasons into an enriched immutable intervention-audit event; no producer change. |
