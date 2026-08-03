# End-to-end validation workflow

## Objective

Verify that accepted project attribution remains intact across producer evidence, the local ledger, source telemetry, reanalysis, and dashboard-facing events.

## Guidance

- Load the canonical session-context contract before validating fields or tuple completeness.
- Start a fresh conditionally supported producer lifecycle event only when runtime validation is part of the request.
- Query source telemetry and the configured destination through the same explicit producer/session or producer/conversation correlation key.
- Compare the managed inbox record, immutable ledger evidence, source trace bounds, and resulting agent-project values.
- Require every correlated source interval to be contained by exactly one accepted project-evidence interval.
- Reject missing, duplicate, partial, conflicting, inferred, static, open-ended, or uncorrelated results.
- Verify dashboard-facing events contain the paired project ID and project name from the active generation.
- Record the producer, session boundary, correlation key, queried fields, generation ID, and result.
- Complete this workflow only when source and dashboard-facing telemetry satisfy the canonical contract end to end.
