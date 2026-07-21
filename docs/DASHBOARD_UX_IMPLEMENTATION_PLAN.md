# Dashboard UX Implementation Plan

## Objective

Make the dashboards answer two separate operator questions:

1. **Agent Introspection Health**: can the hourly analysis pipeline be trusted?
2. **Agent Introspection**: which observed agent behaviours should be investigated
   in the selected time range?

The existing Agent Introspection route remains the canonical insight dashboard.
The new Agent Introspection Health dashboard is a separately managed canonical
dashboard. Both use the global SigNoz time selector. Projection panels show the
active canonical seven-day analysis window filtered by the selected SigNoz
display range. The default and only implemented analysis horizon is seven days.

## Scope and boundaries

- Create the Health dashboard and remove operational panels from Agent
  Introspection.
- Rebuild the insight dashboard around project attribution, actionable patterns,
  detector coverage, and detector effectiveness.
- Add concise persisted panel descriptions that state the question each panel
  answers and identify the selected time range where it affects interpretation.
- Improve project attribution only from explicit, validated telemetry evidence;
  unresolved data remains unresolved.
- Reanalyse the source-retained seven-day window into a new immutable analysis
  dataset before promoting an attribution-contract generation.
- Do not add custom time controls or 14-, 30-, or 60-day projections. Those
  horizons require separate source-retention and projection-contract proof.
- Do not surface diagnostic pipeline fields in Pipeline health, review activity,
  or lifecycle activity in either dashboard. `Outcome` remains an agreed column
  in Recent scan runs.

## Canonical dashboards

### Agent Introspection Health

| Panel | Type | Question answered | Data contract |
| --- | --- | --- | --- |
| Pipeline health | Table | Is the pipeline healthy, when did it last complete, and how long did it take? | Latest terminal `introspection.pipeline.snapshot` in the selected range: `Pipeline state`, `Last completed scan`, `Last scan duration` |
| Recent scan runs | Table | Have up to 24 recent completed scans in the selected time range run successfully and at a stable cost? | Up to 24 terminal pipeline snapshots in the selected range, newest first, with `Started at`, `Duration`, `Outcome`, and `Rows processed` |

Both panels retain the global time filter. The current-state table reports an
accurate empty state when the selected range contains no terminal snapshot.

### Agent Introspection

| Panel | Type | Description | Data contract |
| --- | --- | --- | --- |
| Project data attribution | Table | How much of the active canonical seven-day analysis window is attributed to a project? | Active-generation observations: `Project attribution coverage`, `Attributed observations`, `All observations` |
| Actionable trends | Table | Which actionable patterns are present in the active canonical seven-day analysis window? | Retain the current columns pending a separate column-design decision; add an explicit description of the seven-day analysis window and selected display range |
| Observed signals by detector | Time series | Which detector families are producing observations in the active canonical seven-day analysis window? | Active-generation observations, grouped daily by human-readable detector label |
| Detector signal yield | Table | Share of distinct findings that become actionable patterns. | Active-generation current findings: `Detector`, `Actionable findings`, `All findings`, `Actionable yield` |

Use a 12-column layout with one wide table or time series per row and charts at
height 5 or 6. Avoid raw attribute keys in labels and legends. Each widget has a
concise persisted description; browser verification confirms that SigNoz renders
the descriptions as visible help. The composition visual is deferred until an
explicit chart-design decision; it is not implemented by this plan.

## Capability and identity preflight

Before a live dashboard write, record the installed SigNoz support for table,
bar, graph, pie, value, row, variables, global time, layout height, widget
`description`, and dashboard API operations. The installed version supports the
required panel types and widget description field. Browser proof must confirm
the rendered description before it is treated as a user-facing subtitle.

Health dashboard bootstrap is two-phase:

1. Create exactly one dashboard through the supported local API after proving
   creation semantics and recording the returned route ID and nested UUID.
2. Read it back, record those values in the canonical dashboard model and
   generated asset, and compare them before every later identity-preserving
   update. Abort on any route or UUID mismatch.

## Project attribution contract

1. Keep direct validated trace `cwd` attribution as the highest-confidence
   method.
2. Persist append-only `thread_project_evidence` from a trace only when its
   explicit `thread.id`, source trace ID, source timestamp, source-contract
   fingerprint, attribution-contract version, and locally validated project
   identity are available. Its immutable ID is derived from those provenance
   fields. Exact duplicates coalesce; a second valid project identity for the
   same thread is retained as conflicting evidence.
3. A mapping is valid only when every evidence record for its thread, within the
   fixed reanalysis window, resolves to one project identity. Direct validated
   trace `cwd` takes precedence; otherwise use a valid unique thread mapping;
   otherwise use the existing unambiguous conversation-to-thread mapping and
   then that thread mapping. Conflicting, missing, out-of-window, or invalid
   evidence resolves to `unresolved`.
4. Persist the attribution method on derived events as `trace_cwd`,
   `thread_cwd`, `conversation_thread_cwd`, or `unresolved`.
5. Do not derive a project from prompts, tool arguments, names, labels, or path
   heuristics. Do not mutate immutable observations or findings.
6. Before changing upstream telemetry, determine and record the owning emitter
   and its canonical attributes. If this repository owns it, emit explicit
   `cwd` and `thread.id` on relevant trace and log records and test that
   contract. If it does not, preserve unresolved results and do not claim an
   attribution-coverage improvement from this rollout.

## Bounded attribution reanalysis

Implement an explicit reanalysis command rather than changing normal hourly
scans. It must:

1. Expose a separate `analysis-reanalyse-attribution` command with required
   explicit UTC start/end bounds. It does not call normal-scan watermark logic
   and does not alter watermarks or legacy facts.
2. Require the approved source schema, retained-source proof for those exact
   bounds, and fresh source/semantic-contract fingerprints.
3. Extract the bounded source window under the attribution contract and build an
   isolated immutable fact set containing observations, evidence, memberships,
   findings, and trend facts.
4. Stage the resulting analysis generation from that isolated fact set, drain its
   events, and require remote verification before it becomes current.
5. Atomically promote the new generation only after all validations succeed.
   Abort without changing the active generation if source retention, schema
   approval, attribution evidence, persistence, or remote verification fails.

## Implementation sequence

1. Capture the SigNoz capability and identity preflight evidence, including the
   live widget-description rendering proof and Health-dashboard creation/readback
   contract. Stop if either is unavailable.
2. Extend the dashboard model so each canonical dashboard has a stable UUID,
   title, description, panel set, layouts, generated asset, verifier, and tests.
   Bootstrap the Health dashboard once, record its route ID and UUID, and then
   manage it only through identity-preserving updates.
3. Move pipeline health into the new Health dashboard, reduce it to the three
   agreed columns, and replace scan duration with the 24-row recent-scan table.
4. Rebuild Agent Introspection with the four agreed retained insight panels,
   plain-language query aliases, descriptions, legends, and taller layouts.
   Keep all insight queries on `GLOBAL_TIME` and select only the active analysis
   generation. Add no composition or status-distribution visual until its chart
   design has explicit approval.
5. Add a canonical label map for detector, category, and status values. All
   recognized values map deterministically; unfamiliar values remain visibly
   labelled as their raw contract value. Use this map in every dashboard query,
   legend, and test.
6. Add the attribution evidence model, resolver, source-contract fields,
   attribution event attributes, and tests. Add upstream telemetry instrumentation
   only when this repository is confirmed as the owning emitter; otherwise retain
   unresolved attribution and continue without claiming coverage improvement.
7. Add the fail-closed bounded attribution reanalysis workflow and comprehensive
   migration, generation-promotion, bounds, and source-retention tests.
8. Approve the source schema, run bounded reanalysis, remotely verify and
   activate its generation, then run a normal hourly-compatible scan.
9. Update both existing/new dashboards through the local API with backups,
   preserve their identities, and relock them.
10. Browser-verify every panel at **Last 1 week**, including readable tables,
   useful populated states or accurate empty states, legend labels, descriptions,
   chart height, and the relationship between the selected time range and each
   panel.

## Verification

- Dashboard-builder tests prove exact panel identities, titles, descriptions,
  layouts, global-time usage, plain-language aliases, query contracts, generated
  assets, and truthful canonical-window wording for both **Last 1 week** and a
  wider selected display range.
- Attribution tests prove precedence, unique mapping, conflict handling,
  conversation mapping, invalid local paths, worktree identity, and no heuristic
  attribution.
- Reanalysis tests prove source/schema gating, immutable legacy facts, fresh
  facts, atomic promotion, and remote-verification failure safety.
- Run formatting, Ruff, `uv run mypy src`, the full test suite, SQLite integrity checks,
  source-schema validation, generation stage/activation verification, a
  succeeded or no-data scan, dashboard API identity comparison, and browser QA.

## Deferred horizon review

Reconsider selectable 14-, 30-, and 60-day horizons only after each has proven
source retention, a separately bounded projection, and an approved data
contract. The global SigNoz time selector remains the user control; no custom
range controls are introduced by this implementation.
