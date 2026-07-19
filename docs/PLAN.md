# Agent Introspection System Plan

## Objective

Operate a local, hourly Agent Introspection system that extracts approved
telemetry, persists deterministic analysis in SQLite, and provides an actionable
SigNoz dashboard without applying repository changes.

## Invariants

- This repository is the canonical implementation target.
- The actionable analysis horizon is seven days.
- The scheduler permits one succeeded or no-data scan per UTC hourly slot and
  uses a lease to prevent overlap. It uses one calendar-hour trigger, runs at
  user-session load, coalesces missed hour boundaries after wake, and
  terminalizes interrupted runs before resuming persisted watermarks. Bounded
  source work converts a stalled scan into a terminal failure before it can
  block later hourly slots.
- Source-query structural contracts require explicit approval and fail closed on
  drift.
- Raw source data never becomes derived telemetry. Hydration remains allowlisted
  and bounded to shortlisted source identifiers.
- Observations, evidence, memberships, findings, trends, review facts, and
  outbox events remain durable and immutable where their history requires it.
- Proposals require an explicit approval decision and a separate application
  request. The system never applies a proposal itself.

## Analysis flow

1. Verify SQLite, network perimeter, source-contract approval, and scan lease.
2. Extract bounded logs and traces, then hydrate only shortlist fields.
3. Run deterministic detectors and persist observations, evidence, findings,
   memberships, trends, and source watermarks atomically.
4. Emit generation-scoped observation and trend projections only when a
   remotely verified active analysis generation exists.
5. Drain preceding outbox work, then atomically persist the terminal pipeline
   snapshot and terminal review-activity snapshot.

On a source, hydration, contract, or processing failure, the analytical
transaction is rolled back. The scan records only safe terminal operational
facts and does not emit observations or trend projections.

## Analysis generation flow

1. Stage an immutable generation from the validated local seven-day facts.
2. Deliver every linked projection event and verify exact event IDs remotely.
3. Deliver and remotely verify the activation marker.
4. Record immutable activation evidence and move the sole current-generation
   cursor in one transaction.

An unchanged semantic contract does not create another generation. A material
source-contract, source-query/extraction, detector, normalisation, trend-rule,
identity, or outcome-model change requires a newly staged and promoted generation.
Normal projection scans require an active generation whose source and runtime
semantic contracts match before source extraction begins.

## Manual review flow

`introspection-review` is a manually invoked bounded classification workflow.
It verifies health, source-contract, and capability proof before candidate export;
preserves envelope provenance; validates accepted imports; and never changes a
repository, finding, or proposal during classification.

Review activity contains accepted classification and proposal facts only. It
distinguishes factual zero, unavailable, and not applicable states. Capability
probes are excluded.

## Dashboard

The existing SigNoz dashboard entity remains the canonical route. It has eight
panels: pipeline health, scan duration, project identity coverage, actionable
trends, current trend context, observed signal mix, detector signal yield, and
review activity.

Operational panels read terminal snapshots. Projection panels select the active
analysis generation using an activation-marker lookup outside the dashboard time
filter. The review panel selects the latest review aggregate outside the time
filter; no aggregate produces an unavailable state rather than a synthetic zero.

Detailed rollout, historical convergence, panel contracts, and lifecycle
reintroduction gates are in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md).
