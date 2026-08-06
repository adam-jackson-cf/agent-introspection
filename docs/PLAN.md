# Agent Introspection System Plan

## Objective

Operate a local, hourly Agent Introspection system that resolves one canonical
Git project per producer, attributes source activity by source-event time, and
presents actionable dashboards without changing repository state.

## Invariants

- The shared runtime stores an immutable project-context interval for each
  producer.
- The canonical session-context record contains:
  `producer`, `producer.surface`, `correlation.id`, `lifecycle.event`,
  `occurred_at`, `agent.project.id`, `agent.project.name`,
  `agent.project.root`, and `agent.project.kind = git`.
- Attribution is performed at source activity granularity with half-open
  intervals.
- A source activity belongs to exactly one interval for the same producer and
  correlation ID.
- Conflicting, missing, malformed, inaccessible, ambiguous, and out-of-order
  records are rejected durably with bounded rejection records.
- Rejection records contain no prompts, responses, tool payloads, environment
  values, or arbitrary producer content.
- Canonical activity records are immutable, and activity-version updates are
  monotonic per activity.
- OTLP outbox delivery is transactional with the activity-version update.
- Findings and trends are recomputed from the latest activity version.
- Dashboard queries use source-event time and the selected range only.

## Analysis flow

1. Verify SQLite, network perimeter, source-contract approval, and scan lease.
2. Extract bounded logs and traces, then hydrate only shortlist fields.
3. Run deterministic detectors and persist activities, versions, findings,
   memberships, trends, and source watermarks atomically.
4. Emit outbox events in the same transaction as the activity-version update.
5. Drain preceding outbox work, then atomically persist the terminal pipeline
   snapshot.

On a source, hydration, contract, or processing failure, the analytical
transaction is rolled back. The scan records only safe terminal operational
facts and does not emit derived telemetry.

## Manual review flow

`introspection-review` is a manually invoked bounded classification workflow.
It verifies health, source-contract, and capability proof before candidate
export; preserves envelope provenance; validates accepted imports; and never
changes a repository, finding, or proposal during classification.

## Dashboard

Agent Introspection is the canonical insight dashboard route. It has four
panels: project data attribution, actionable trends, observed signals by
detector, and detector signal yield.

Agent Introspection Health is the canonical operational dashboard. It has two
panels: pipeline health and recent scan runs. Operational panels read terminal
snapshots. Insight panels use the selected source-event time range.
