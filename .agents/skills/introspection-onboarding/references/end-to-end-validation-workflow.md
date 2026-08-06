# End-to-end validation workflow

## Objective

Verify that one fresh producer lifecycle remains correlated and correctly attributed from the managed hook through the canonical dashboard query.

## Guidance

- Load the canonical session-context contract before validating fields or tuple completeness.
- Start a fresh supported producer lifecycle only when runtime validation is part of the request.
- Query source telemetry and the configured destination through the same explicit producer/session or producer/conversation correlation key.
- Compare the managed inbox record, immutable context ledger, accepted project interval, source activity bounds, canonical activity ID, latest activity version, canonical outbox event ID, and resulting agent-project tuple.
- Require every correlated source activity to be contained by exactly one accepted project interval for the same producer and correlation ID.
- Reject missing, duplicate, partial, conflicting, inferred, static, open-ended, or uncorrelated results.
- Verify the dashboard source-time query selects the same latest canonical activity version and paired project ID and project name.
- Record the producer, lifecycle boundary, correlation key, queried fields, activity ID, activity version, outbox event ID, project tuple, and dashboard result.
- Complete this workflow only when the hook, ledger, source telemetry, canonical activity version, remote event, and dashboard query satisfy one contract end to end.
