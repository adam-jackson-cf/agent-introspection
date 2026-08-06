# Dashboard UX Implementation Plan

## Objective

Make the dashboards answer two operator questions:

1. Is the hourly pipeline healthy?
2. Which observed behaviours should be investigated in the selected source-event
   time range?

The Agent Introspection route is the canonical insight dashboard. The Agent
Introspection Health dashboard is the canonical operational dashboard. Both use
the global SigNoz time selector, and both describe data in source-event time.

## Scope and boundaries

- Keep the Health dashboard focused on pipeline health and recent scan runs.
- Keep the insight dashboard focused on project attribution, actionable
  patterns, detector coverage, and detector effectiveness.
- Add concise persisted panel descriptions that state the question each panel
  answers and identify the selected time range where it affects interpretation.
- Use only explicit, validated source-event attribution.
- Do not add custom time controls.
- Do not surface diagnostic pipeline fields in either dashboard.

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
| Project data attribution | Table | How much of the selected source-event time range is attributed to a project? | Latest activity versions with `Project attribution coverage`, `Attributed observations`, `All observations` |
| Actionable trends | Table | Which actionable patterns are present in the selected source-event time range? | Retain the current columns and describe the selected source-event time range |
| Observed signals by detector | Time series | Which detector families are producing observations in the selected source-event time range? | Latest activity versions, grouped daily by human-readable detector label |
| Detector signal yield | Table | Share of distinct findings that become actionable patterns. | Latest activity versions and findings with `Detector`, `Actionable findings`, `All findings`, `Actionable yield` |

Use a 12-column layout with one wide table or time series per row and charts at
height 5 or 6. Avoid raw attribute keys in labels and legends. Each widget has a
concise persisted description.

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

1. The source emitter attaches all four attributes to the same relevant span:
   `agent.project.id`, `agent.project.name`, `agent.project.root`, and
   `agent.project.kind`.
2. A trace is attributed only when every span carrying project metadata carries
   one complete, identical tuple. The consumer rejects partial, conflicting, or
   invalid metadata. A trace with no project metadata is `unresolved`.
3. `agent.project.id` is the canonical grouping key. `agent.project.name` is
   required for every attributed dashboard event. The backend persists the
   source-supplied name with the project identity and emits both attributes on
   derived telemetry.
4. `agent.project.root` is a normalized absolute project root and
   `agent.project.kind` is `git` or `non_git`. They support local project
   identity persistence but are not dashboard dimensions.
5. Do not derive a project from prompts, tool arguments, names, labels, or path
   heuristics. Do not mutate immutable observations or findings.
6. Before enabling a producer, verify that it emits the complete span tuple.
   Producers without this contract remain `unresolved`.

## Implementation sequence

1. Capture the SigNoz capability and identity preflight evidence, including the
   live widget-description rendering proof and Health-dashboard creation/readback
   contract. Stop if either is unavailable.
2. Extend the dashboard model so each canonical dashboard has a stable UUID,
   title, description, panel set, layouts, generated asset, verifier, and tests.
3. Move pipeline health into the Health dashboard, reduce it to the three
   agreed columns, and replace scan duration with the 24-row recent-scan table.
4. Rebuild Agent Introspection with the four agreed retained insight panels,
   plain-language query aliases, descriptions, legends, and taller layouts.
   Keep all insight queries on `GLOBAL_TIME` and the selected source-event time
   range. Add no composition or status-distribution visual until its chart
   design has explicit approval.
5. Add a canonical label map for detector, category, and status values. All
   recognized values map deterministically; unfamiliar values remain visibly
   labelled as their raw contract value. Use this map in every dashboard query,
   legend, and test.
6. Add the attribution evidence model, resolver, source-contract fields,
   attribution event attributes, and tests. Add upstream telemetry instrumentation
   only when this repository is confirmed as the owning emitter; otherwise
   unresolved attribution continues.
7. Update both existing dashboards through the local API with backups, preserve
   their identities, and relock them.
8. Browser-verify every panel at **Last 1 week**, including readable tables,
   useful populated states or accurate empty states, legend labels, descriptions,
   chart height, and the relationship between the selected time range and each
   panel.

## Verification

- Dashboard-builder tests prove exact panel identities, titles, descriptions,
  layouts, global-time usage, plain-language aliases, query contracts, generated
  assets, and truthful source-event-time wording for both **Last 1 week** and a
  wider selected display range.
- Attribution tests prove precedence, unique mapping, conflict handling,
  conversation mapping, invalid local paths, worktree identity, and no heuristic
  attribution.
- Run formatting, Ruff, `uv run mypy src`, the full test suite, SQLite integrity checks,
  source-schema validation, a succeeded or no-data scan, dashboard API identity
  comparison, and browser QA.
