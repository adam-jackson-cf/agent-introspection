# End-to-end validation workflow

## Objective

Verify that scheduler-processed session-context records retain canonical identity and correlate with source telemetry through the same explicit producer/session key.

## Required actions

1. Use the session-context validation workflow after a fresh conditionally supported producer lifecycle event.
2. Query source telemetry and the configured telemetry destination for that event.
3. Confirm the source telemetry exposes the same explicit producer/session correlation key as the authoritative lifecycle session ID.
4. Compare the resulting event and `agent.project` values with the managed inbox record through that matching correlation key.
5. Reject a missing, duplicate, partial, conflicting, inferred, static-attribute, or uncorrelated result.
6. Keep Claude Code attribution unresolved because it lacks an authoritative event timestamp and its installed trace/log telemetry does not document the correlation key; do not configure or validate it through a fallback path.
7. Record the producer, session boundary, correlation key, queried fields, and result.

## Done when

- The scheduler-preserved event matches the canonical managed inbox record through the same explicit producer/session correlation key.
