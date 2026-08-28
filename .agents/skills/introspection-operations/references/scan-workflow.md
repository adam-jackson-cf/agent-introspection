# Scan workflow

## Objective

Run one bounded scan that reconciles accepted session context into canonical activity versions and delivers their deterministic telemetry.

## Required actions

1. For scheduled mode, enforce the configured UTC interval slot, then acquire the configured scan lease and resume from persisted watermarks.
2. Replay the managed context inbox transactionally and preserve accepted intervals and quarantined rejections.
3. Read bounded source batches and persist exact source membership, canonical activities, monotonic attribution versions, latest-version findings, and trend evaluations.
4. Deliver canonical activity-version events through the immutable outbox and verify their stable remote event IDs.
5. Report context, source, activity, version, finding, trend, watermark, outbox, deferred-work, and rejection counts.

## Done when

- The scan records succeeded or no_data without overlapping another scan or duplicating a successful slot.
- Every resolved activity is contained by exactly one accepted producer/correlation/project interval, except Codex CLI `session_context`, which requires exactly one accepted context record at the matching producer and correlation ID.
- Persisted latest-version populations, deterministic outbox identities, and remote delivery are verified.
