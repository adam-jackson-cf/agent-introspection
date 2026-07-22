# End-to-end validation workflow

## Objective

Verify that producer-emitted project metadata reaches SigNoz and dashboard-facing Agent Introspection events intact.

## Required actions

1. Load the canonical schema through the canonical-schema workflow.
2. Start a fresh producer session in a known project.
3. Query recent source spans for the full schema tuple on each relevant span.
4. Reject absent, partial, conflicting, or invalid tuples according to the schema.
5. Verify derived dashboard-facing events contain the schema-required paired project ID and name.
6. Record the producer, session boundary, queried fields, and result.

## Done when

- Producer emission and dashboard-facing telemetry satisfy the canonical schema end to end.
