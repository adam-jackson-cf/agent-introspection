# Agent Introspection Reliability Plan

## Operating purpose

The dashboard presents the current canonical session-context, activity-version,
outbox, and source-time system for local operators.

## Canonical runtime

- The shared runtime resolves one Git project per producer and stores an
  immutable project-context interval.
- Lifecycle records carry the canonical session-context contract:
  `producer`, `producer.surface`, `correlation.id`, `lifecycle.event`,
  `occurred_at`, `agent.project.id`, `agent.project.name`,
  `agent.project.root`, and `agent.project.kind = git`.
- A source activity is attributed only when it falls inside exactly one
  half-open interval for the same producer and correlation ID.
- Conflicting, missing, malformed, inaccessible, ambiguous, and out-of-order
  lifecycle records are rejected durably with bounded rejection records.
- Rejected records contain no prompts, responses, tool payloads, environment
  values, or arbitrary producer content.
- Canonical activity records are stored as immutable activities with
  monotonic activity versions.
- Every attribution change creates one higher activity version only when the
  canonical attribution tuple changes.
- Findings and trends are recomputed from the latest activity version after
  reconciliation.
- OTLP delivery uses the outbox in the same transaction as the activity
  version update.
- The dashboard uses source-event time for range selection and interpretation.

## Canonical dashboards

| Dashboard | Panel | Data contract | Operator decision |
| --- | --- | --- | --- |
| Agent Introspection Health | Pipeline health | Latest terminal pipeline snapshot | Trust, repair, or wait for the pipeline |
| Agent Introspection Health | Recent scan runs | Terminal pipeline snapshots in the selected range | Detect scan cost, failures, or performance drift |
| Agent Introspection | Project data attribution | Latest activity versions in the selected source-event time range | Decide whether project comparison is trustworthy |
| Agent Introspection | Actionable trends | Latest activity versions in the selected source-event time range | Select a concrete behaviour for review |
| Agent Introspection | Observed signals by detector | Latest activity versions grouped by detector | See which detector families dominate |
| Agent Introspection | Detector signal yield | Latest activity versions and findings in the selected source-event time range | Assess detector usefulness |

Project concentration is withheld until the current canonical attribution set
has enough resolved identity coverage, resolved observations, and distinct
projects to support a trustworthy comparison.

## Rollout sequence

1. Run migrations and verify SQLite integrity.
2. Update both canonical dashboard entities from their generated assets.
3. Run a normal scan and confirm the terminal pipeline snapshot.
4. Validate all six panels in the in-app browser over the selected source-event
   time range.
5. Record any material contract change, update the dashboard assets, and
   verify the resulting browser state.

## Required evidence

- passing format, lint, type, and test suites;
- migration backup and SQLite foreign-key checks;
- a succeeded or no-data normal scan and terminal snapshot;
- an hourly schedule status with the configured interval;
- dashboard update proof for both routes and browser confirmation for all six
  panels;
- a completion review with no objective-relevant unresolved issue.
