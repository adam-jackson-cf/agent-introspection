# Agent Introspection Dashboard Rework Plan

## Objective

Restore Agent Introspection as a behavior-review dashboard using the canonical activity contract, while preserving its existing route and nested UUID. The initial release carries forward the useful question behind the earlier dashboard—what behaviors occurred, where, how often, and when—without reviving removed generation logic or claiming actionability that current remote telemetry cannot prove.

Operational ingestion, correlation, attribution, reconciliation, and scan panels move to Agent Introspection Health under `HEALTH_DASHBOARD_MIGRATION_PLAN.md`. Both plans share one clean-cutover release gate: neither deployed route changes until both final builders, assets, query oracles, layouts, and recovery payloads are approved.

## Previous signals and carry-forward decisions

The historical audit is complete enough to fix the initial release boundary:

| Prior signal or UX commitment | Prior question and denominator | Data/state/time contract | Disposition |
| --- | --- | --- | --- |
| Dashboard purpose split | Are observed behaviors separate from pipeline operations? | Separate persisted Insight and Health identities | Carry forward; Insight contains behavior review only. |
| Project data attribution | How much selected data is assigned to a project? Denominator: observations/activities. | Project identity plus unresolved share in the selected analysis range | Move coverage mechanics to Health; retain Unresolved visibility in Insight summary and project rows. |
| Actionable trends | Which distinct findings are actionable? | Finding/trend state, occurrence count, and evaluation over an explicit evidence window | Defer; current remote activity events do not expose the required finding projection. |
| Observed signals by detector | Which detector families produced observations? Denominator: observation/activity identities. | Human-readable detector series over selected source time | Carry forward as Observed behaviors by detector. |
| Detector signal yield | What share of distinct findings became actionable? | Distinct findings and actionable finding state | Defer; Behavior mix is activity-by-detector composition and is not yield. |
| Actionable issue composition and concentration | How are actionable occurrences distributed by category and project? | Actionable finding occurrences plus trustworthy attribution | Defer; activity counts cannot substitute for actionable occurrences or prioritization. |
| Finding-status distribution | How are findings distributed across human-readable states? | Canonical finding state | Defer with actionable trends. |
| Explicit selected timeframe and purpose subtitles | What range and question does each panel represent? | Persisted descriptions and global SigNoz time control | Carry forward in every panel description; no custom control. |
| 7/14/30/60-day horizons | How does analysis change by bounded review horizon? | Separately bounded projections and proven retention; seven days was the earlier default | Do not recreate fixed horizons. Use the selected global source-time range; wider predefined horizons remain blocked on canonical projection and retention proof. |

The local ledger contains recomputed findings, but the dashboard queries remote SigNoz. No panel may join local SQLite implicitly or infer `emerging`/`actionable` from activity counts. A future actionable-trend, composition, status, or yield panel requires a separately approved immutable canonical finding projection with finding identity, detector, project, state, occurrence count, canonical task count, first seen, last seen, version, and deterministic OTLP identity.

## Canonical insight cohort

Every panel uses one fail-closed cohort. Remote source time is the ClickHouse `timestamp` UInt64 nanosecond column populated from the canonical activity OTLP `timestamp_ns`; it must equal local `canonical_activities.source_ended_at_ns` for every version. The selected interval is `timestamp > $start_timestamp_nano AND timestamp <= $end_timestamp_nano`.

First identify candidate activity IDs having a canonical version with `activity.payload_schema_version = 2` in that interval. Then load all canonical versions carrying that immutable contract metadata for those IDs, require every version of an activity to share exactly one source timestamp, and choose the greatest version globally. A later-delivered reconciliation remains in the original source-time cohort. Divergent version timestamps fail before selection.

Repeated delivery is valid only when the complete equality tuple matches: `event.id`, event name, payload schema version, activity ID/version, source timestamp, producer, surface, correlation ID, detector ID/version, normalization version, attribution state/method/evidence/reason, attribution project identity ID, and canonical project ID/name. The deterministic SHA-256 event ID must match activity ID, version, schema, and event name; exactly one event ID may represent one activity/version. Same-ID divergence, multiple event IDs for one activity/version, deterministic-ID mismatch, version gaps, and empty required identities are integrity failures.

All panels depend on one `canonical_activity_integrity` CTE/helper that validates those invariants with a proven ClickHouse fail-closed expression before aggregation. Required non-empty values are event ID, activity ID, positive version/schema, producer, surface, correlation ID, detector ID, positive detector/normalization versions, attribution state/method, and project ID/name. Evidence and reason fields follow their attribution-state contract. No panel may filter corrupt rows and continue.

Resolved project identity is keyed by `(agent.project.id, agent.project.name)` with ID authoritative and name used for display. Two IDs may share a name and remain separate. One ID with conflicting names in the cohort fails closed. Unresolved rows use canonical ID `unresolved` and display name `Unresolved`.

Sessions are namespaced `(activity.producer, activity.correlation_id)`. Detector identity is the raw detector ID. Display labels use the canonical injective label map; unknown values render `Other: <raw-id>`. A label collision fails integrity validation.

The source-time oracle independently computes the same stable activity-ID/latest-version manifest from canonical inputs. Browser rendering is never the population oracle.

## Final insight panel manifest

| Panel ID | Title | Type | Layout `(x,y,w,h)` | Persisted description |
| --- | --- | --- | --- | --- |
| `behavior-summary` | Behavior summary | table | `(0,0,12,4)` | Canonical observed behavior totals in the selected source-event time range. |
| `observed-behaviors-by-detector` | Observed behaviors by detector | graph | `(0,4,12,6)` | Canonical observed behaviors by detector over the selected source-event time range. |
| `behavior-mix` | Behavior mix | table | `(0,10,6,6)` | Activity, session, and resolved-project composition by behavior in the selected source-event time range. |
| `behaviors-by-project` | Behaviors by project | table | `(6,10,6,6)` | Canonical observed behaviors by project identity in the selected source-event time range, including Unresolved. |
| `recent-observed-behaviors` | Recent observed behaviors (latest 100) | table | `(0,16,12,7)` | The latest 100 canonical observed behaviors in the selected source-event time range; Behavior summary reports the complete cohort. |

All five IDs are new because the current deployed panels have operational contracts and the older behavior panel identities no longer exist on the current dashboard. No alias or deprecated panel definition remains. Layout coordinates are non-overlapping and cover one deliberate reading order: summary, timeline, composition, project distribution, evidence detail.

## Exact result manifest

Counts are logical non-negative integers emitted as ClickHouse `Float64` for SigNoz compatibility. Source times are `DateTime64(9, 'UTC')`; the browser may localize display without changing query values. Zero cohorts return zero rows in every panel. Null percentiles do not apply to Insight. Sorts use raw canonical keys, never display labels.

| Panel | Ordered output columns and types | Sort and cardinality |
| --- | --- | --- |
| Behavior summary | `Observed activities` Float64, `Behaviors` Float64, `Sessions` Float64, `Resolved projects` Float64, `Unresolved activities` Float64, `Latest source time` DateTime64(9, UTC) | One row when non-empty; otherwise no rows. |
| Observed behaviors by detector | `ts` DateTime64(9, UTC), `detector` String display label, `value` Float64 | `ts ASC`, raw detector ID ASC; one row per bucket/raw detector. |
| Behavior mix | `Behavior` String, `Detector ID` String, `Activities` Float64, `Sessions` Float64, `Resolved projects` Float64, `First seen` DateTime64(9, UTC), `Last seen` DateTime64(9, UTC) | Activities DESC, raw detector ID ASC; one row per raw detector. |
| Behaviors by project | `Project` String, `Project ID` String, `Behavior` String, `Detector ID` String, `Activities` Float64, `Sessions` Float64, `Last seen` DateTime64(9, UTC) | Activities DESC, project ID ASC, raw detector ID ASC; one row per project tuple/raw detector. |
| Recent observed behaviors | `Source time` DateTime64(9, UTC), `Behavior` String, `Detector ID` String, `Project` String, `Project ID` String, `Producer` String, `Surface` String, `Attribution state` String, `Activity` String | Source time DESC, full activity ID DESC; exactly `min(100, cohort_count)` rows. |

`Activity` is the first 12 lowercase hexadecimal characters of the validated full activity ID. It is display-only. The full ID remains the tie-breaker and oracle identity. The graph bucket expression and minimum bucket are approved against the installed SigNoz version before fixtures; its output remains the exact `ts`/`detector`/`value` schema above.

## Panel contracts

### Behavior summary

Show total stable activities, distinct raw detector IDs, distinct producer/session tuples, distinct resolved project IDs, unresolved activities, and latest source time. The complete-cohort identity set is the conservation denominator. Zero cohort returns no row.

### Observed behaviors by detector

Plot canonical activity count by raw detector identity over source time using the approved bucket expression. Known labels are human-readable; unknown IDs use `Other: <raw-id>`. The raw ID remains the grouping and sort key. Each series returns `ts`, display `detector`, and numeric `value`.

### Behavior mix

For each raw detector, show the exact result-manifest columns. These are observed activity counts, not finding status, actionable composition, concentration, prioritization, severity, success, prevalence, or detector yield.

### Behaviors by project

Group resolved rows by canonical `(project ID, project name)` and raw detector; group unresolved rows under the explicit canonical Unresolved tuple. Duplicate display names across different IDs remain separate. Conflicting names for one ID fail before aggregation. Project-row activity sums, including Unresolved, equal the complete cohort.

### Recent observed behaviors

Render the exact newest subset ordered by `(source timestamp DESC, full activity ID DESC)`. The displayed size is `min(100, cohort_count)`, and fixtures include a cohort above 100 to prove the boundary. The title and persisted description always disclose the latest-100 cap. Include only the result-manifest fields; never expose prompts, responses, command output, arbitrary source attributes, or full correlation IDs.

## Work plan

### Phase 1 — Audit previous and current signal contracts

1. Verify the completed historical decision table against the retained dashboard definitions and UX records; any discrepancy reopens Gate 1.
2. Inventory every field and type emitted by `introspection.activity.version.recorded` and prove the source-time, event-ID, version, project, session, and detector invariants above.
3. Confirm installed SigNoz support for the approved graph bucket expression, widget descriptions, exact table types, UTC query values, and zero-row rendering.
4. Freeze UTC ranges containing resolved, unresolved, duplicate-name projects, multiple detectors/sessions/versions, later-delivered reconciliation, boundary events, over-100 detail, and zero results.

Gate 1 approves the historical dispositions, exact descriptions/result manifest, remote field/type map, `(start,end]` source-time contract, global latest-version ordering, integrity tuple/helper, project identity rule, detector label policy, short-ID rule, top-100 contract, graph bucket, and zero/no-data shape before fixtures or SQL.

### Phase 2 — Build independent expected results

Create the redacted joint live oracle at `~/.local/share/agent-introspection/backups/dashboard-query-oracle-<cutoff>.json`, containing exact UTC bounds, local SQLite snapshot identity and checksum, remote export identities and checksums, stable activity/event IDs, latest versions, allowlisted activity attributes, and exact typed result rows for all five Insight and eight Health panels.

Add checked-in deterministic inputs and expected Insight rows at `tests/fixtures/dashboard_insight_query_cases.json`. The fixture defines every referenced scalar/map column and covers: identical delivery; same-event divergent payload; two event IDs for one activity/version; deterministic-ID mismatch; every empty required identity; start/end boundaries; several versions and later reconciliation; divergent version timestamps; version gaps; unknown detector and label collision; duplicate project names across IDs; conflicting names for one ID; resolved/unresolved attribution; zero cohort; and more than 100 activities. Its paired Health fixture is `tests/fixtures/dashboard_health_query_cases.json`.

The independent oracle consumes canonical fixture/live inputs plus the approved result manifest, never rendered SQL. Summary, detector, behavior-mix, and project activity sets must equal the complete cohort. Recent IDs must equal the exact ordered top-100 subset and its row count must be `min(100, cohort_count)`.

### Phase 3 — Replace the insight builder

1. Remove the five operational definitions from `INSIGHT_PANELS` as part of the coordinated two-dashboard change.
2. Introduce one canonical latest-activity cohort and integrity helper implementing exact event equality, deterministic identity, `(start,end]` source time, global latest version, project consistency, session namespacing, and detector labeling.
3. Implement the five panel queries and exact description/result/layout manifests above.
4. Preserve the Agent Introspection route contract, nested UUID `576f5068-d183-5cab-88b7-395f65cf1094`, schema version, title, lock state, and behavior-oriented description.
5. Replace the stale `codex` tag with purpose-accurate tags shared with the health migration contract.
6. Update verifier logic to require the common fail-closed integrity helper, exact source table/event/schema/time predicate, event and activity uniqueness, global latest-version selection, output aliases/types/sorts, graph shape, exact panel manifest, and non-overlapping layout.
7. Regenerate `src/agent_introspection/assets/agent-introspection.json`; do not hand-edit generated JSON.

### Phase 4 — Behavioral tests and query execution

Update `tests/test_dashboard.py` to defend observable contracts:

- exact insight panel ownership, IDs, types, titles, and coordinates;
- no health panel in `INSIGHT_PANELS` and no insight panel in `HEALTH_PANELS`;
- stable nested UUID, schema version, lock state, and generated asset equality;
- strict latest-version selection within the declared source-time cohort;
- duplicate invariance and fail-closed divergent duplicates;
- exact summary/detail and detector/project denominator conservation;
- explicit unresolved project handling;
- producer/session tuple namespacing;
- unknown detector label retention;
- no sensitive or unsupported fields in recent behavior rows;
- exact `(start,end]` boundaries, global latest version, later-delivered reconciliation, and timestamp disagreement failure;
- complete duplicate equality/identity failures and every empty required identity;
- project identity/name collisions and project-row conservation;
- exact result aliases, types, descriptions, sorts, short ID, zero-row shape, and top-100 disclosure/boundary;
- graph output shape and approved bucket behavior;
- accurate zero-data rendering.

Use `./scripts/run-dashboard-query-contracts.sh` to provision a disposable isolated ClickHouse instance, create canonical `signoz_logs.distributed_logs_v2` and `signoz_traces.distributed_signoz_index_v3` fixture schemas, load both checked-in typed fixture sets, substitute only the four validated dashboard time macros, execute all thirteen rendered queries otherwise unchanged, compare typed rows exactly, and destroy the instance on every exit path. The harness never writes configured live SigNoz databases and runs without credentials or home-directory evidence.

Run `uv run agent-introspection dashboard verify-data --oracle <path>` separately before deployment. This read-only command validates the joint live oracle and checksums, executes all thirteen queries against the configured SigNoz range, and compares exact typed rows. Missing workstation evidence blocks deployment but never makes pytest or the deterministic contract harness environment-dependent.

### Phase 5 — Joint release readiness

Combine this final manifest with the eight-panel Health manifest. Require:

- both builders and generated assets pass their focused tests;
- every panel query matches its frozen oracle;
- panel ownership sets are disjoint;
- both layouts are non-overlapping;
- both intended raw dashboard documents are validated;
- pre-update route/nested UUID and normalized document checksums are recorded;
- the two-route update and partial-failure recovery procedure from the Health plan has been rehearsed;
- exact populated, no-data, unresolved, unsupported-capability, and pipeline-failure browser expectations are recorded.

Only then may the existing routes be updated. If either route fails post-fetch structural equality or query-result checks, execute the paired rollback and verify both restored documents.

## Files and evidence surfaces

- `src/agent_introspection/dashboard.py`
- `src/agent_introspection/assets/agent-introspection.json`
- `src/agent_introspection/assets/agent-introspection-health.json` for ownership comparison
- `src/agent_introspection/attribution.py` for projected canonical fields
- `src/agent_introspection/telemetry.py` for immutable event identity
- `src/agent_introspection/cli.py` for the read-only `dashboard verify-data` command
- `tests/test_dashboard.py`
- `tests/fixtures/dashboard_insight_query_cases.json`
- `tests/fixtures/dashboard_health_query_cases.json`
- `scripts/run-dashboard-query-contracts.sh`
- historical dashboard definitions and retained UX decision records
- deployed routes, the joint live oracle, and timestamped backups

## Acceptance criteria

- Agent Introspection retains route ID `019f4da0-4a13-7c62-9ac9-fc6d850d633b`, nested UUID `576f5068-d183-5cab-88b7-395f65cf1094`, schema version, and locked state.
- Its panel set exactly matches the five-panel ID/title/type/description/layout manifest and contains no ingestion, attribution-health, lifecycle-correlation, reconciliation, or scan-health panel.
- All panels use the same `(start,end]`, globally latest, integrity-validated canonical activity cohort; all versions of an activity share the local canonical source-end timestamp.
- Aggregate summary, detector, behavior-mix, and project results conserve the complete stable activity-ID denominator; recent detail is the exact ordered `min(100, cohort_count)` subset and visibly discloses its cap.
- Resolved projects group by authoritative ID/name tuple, equal names across IDs never merge, conflicting names fail closed, and unresolved activities remain visible.
- Behavior mix is explicitly activity composition, not finding status, actionable composition, concentration, prioritization, or yield.
- No panel claims actionable state, severity, success, or trend promotion from unsupported fields.
- Identical duplicate deliveries do not change results; divergent equality tuples, deterministic-ID mismatch, multiple event IDs per activity/version, version gaps, timestamp disagreement, project-name conflicts, detector-label collisions, and empty required identities fail before aggregation.
- Every output alias, type, unit, description, sort, tie-breaker, short ID, zero-row shape, graph bucket, and top-100 boundary matches the approved result manifest.
- Every rendered query exactly matches its checked-in typed fixture, including over-100, boundaries, multiple versions, corruption, project-collision, unresolved, unknown-label, and zero cohorts.
- All thirteen queries pass `./scripts/run-dashboard-query-contracts.sh` unchanged except the four time macros, and the disposable instance is destroyed on every exit path.
- The separate read-only live `dashboard verify-data` comparison passes before deployment.
- Generated asset, canonical builder, deployed document, and post-fetch route content agree structurally.
- `uv run pytest -q tests/test_dashboard.py tests/test_scan.py tests/test_telemetry.py`, `./scripts/run-dashboard-query-contracts.sh`, and `uv run ./scripts/run-ci-quality-gates.sh` pass.
- Browser verification confirms exact populated, no-data, range, unknown-label, unresolved, and latest-100 disclosure behavior, plus readable layout, legends, and lock state.

## Stop conditions

Stop if the canonical remote activity projection cannot support one of the five contracts, the graph bucket expression is not supported, independent result sets do not conserve the activity denominator, the Health plan is not approved, or the paired identity-preserving deployment cannot be rehearsed. Do not restore generation predicates, query local SQLite from the dashboard, infer finding state, duplicate operational panels, or deploy a partial dashboard pair.
