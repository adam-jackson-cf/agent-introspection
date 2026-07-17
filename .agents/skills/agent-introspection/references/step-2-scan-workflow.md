# Scan workflow

## Objective

Run an idempotent bounded scan and deliver its derived telemetry.

## Required actions

1. For scheduled mode, enforce the configured UTC interval slot, then acquire the configured scan lease and resume from persisted watermarks.
2. Read bounded source batches and persist normalized identities, evidence, observations, findings, and trend evaluations.
3. Deliver the outbox with stable event identifiers.
4. Report counts, watermarks, deferred work, and failures.

## Done when

- The scan records succeeded or no_data without overlapping another scan or duplicating a successful slot.
- Persisted results and outbox state are verified.
