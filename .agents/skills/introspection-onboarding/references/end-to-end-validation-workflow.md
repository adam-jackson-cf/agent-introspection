# End-to-end validation workflow

## Objective

Verify that one fresh supported producer session-context event or lifecycle boundary remains correlated and correctly attributed from the managed adapter or proxy through the canonical dashboard query.

## Guidance

- Load the canonical session-context contract before validating fields or tuple completeness.
- Start a fresh supported producer session-context event or lifecycle boundary only when runtime validation is part of the request.
- Query source telemetry and the configured destination through the exact canonical `(producer, correlation_id)` pair. The correlation ID must equal the native session identifier established by the current capability proof.
- Compare the managed inbox record, immutable context ledger, accepted project evidence, source activity bounds, canonical activity ID, latest activity version, canonical outbox event ID, and resulting agent-project tuple. Project evidence is an accepted interval, except Codex CLI `session_context`, which is an accepted context record.
- Require every correlated source activity to match exactly one accepted project interval, except Codex CLI `session_context`, which must match exactly one accepted context record, for the same producer and correlation ID.
- Reject missing, duplicate, partial, conflicting, inferred, static, or uncorrelated results. An open interval is valid only when its source time precedes any recorded end.
- Verify the dashboard source-time query selects the same latest canonical activity version and paired project ID and project name.
- Record the producer, session-context or lifecycle boundary, correlation key, queried fields, activity ID, activity version, outbox event ID, project tuple, and dashboard result.
- Complete this workflow only when the managed adapter or proxy, ledger, source telemetry, canonical activity version, remote event, and dashboard query satisfy one contract end to end.
