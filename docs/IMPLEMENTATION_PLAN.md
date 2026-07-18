# Agent Introspection Reliability Plan

## Operating purpose

The dashboard guides a local operator through three questions:

1. Can the pipeline be trusted now?
2. Which observed agent behaviours require attention?
3. What has entered the explicit review and intervention loop?

The canonical repository is this project. Deterministic analysis uses a seven-day
window and scheduled scans run hourly in UTC slots.

## Pipeline contract

Each terminal scan emits one `introspection.pipeline.snapshot` with a separate
terminal status and freshness state. A healthy state requires a fresh succeeded
or no-data scan with available logs and traces. Late scans are degraded; failed,
stale, clock-skewed, missing, or unavailable source state is unhealthy.

Logs, traces, hydration, source-contract validation, and detector persistence
are fail-closed. Any failure rolls back observations, evidence, memberships,
findings, trends, watermarks, and projection events. The terminal snapshot and
scan status are then persisted atomically with safe fixed-category fields only.

Per-stream lag is calculated from the scan finish time and each stream's newest
source timestamp. A no-data stream is not applicable; a future source timestamp
is clock skew and is never clamped.

## Analysis generations

Projection telemetry is scoped by an immutable analysis generation. A generation
contains the seven-day window, approved source-contract fingerprint, detector and
normalisation contract hashes, semantic hash, and linked immutable outbox events.

`analysis-generation stage` rebroadcasts only validated SQLite observations and
current trend facts in the bounded window. It does not re-read raw source data or
run detectors. `analysis-generation activate GENERATION_ID` promotes a staged
generation only after all linked projections and its activation marker are both
locally delivered and remotely verified.

The dashboard obtains the active generation from the unfiltered activation
marker. Projection panels use that one generation; operational panels do not.
No active generation produces a safe `generation_unavailable` pipeline result
and no projection telemetry.

## Historical convergence

Existing observations, evidence, memberships, findings, and outbox records are
preserved. Their membership and outbox state have already been reconciled, so a
raw historical reanalysis is not required for this rollout.

After migration, create and activate one generation from the validated local
seven-day facts. This supplies canonical dashboard projections while preserving
the original observation and trend timestamps. The immutable semantic hash
contains the source contract, source-query/extraction code, and the detector,
normalisation, trend-rule, identity, and outcome-model contracts. Stage a new
generation only after a material source-query/extraction, source-contract,
detector, normalisation, trend-rule, identity, or outcome-model change. Normal
projection scans require the active generation to match both current contracts
before extraction. Validate the bounded result before promotion.

## Canonical dashboard

| Panel | Data contract | Operator decision |
| --- | --- | --- |
| Pipeline health | Latest terminal pipeline snapshot | Trust, repair, or wait for the pipeline |
| Scan duration (ms) | Terminal pipeline snapshot time series | Detect scan cost or performance drift |
| Project identity coverage | Active-generation observations | Decide whether project comparison is trustworthy |
| Actionable trends requiring review | Active-generation current findings | Select a concrete behaviour for review |
| Current trend context | Active-generation current finding states | Judge signal distribution and urgency |
| Observed signal mix by detector | Active-generation observations | See which detector families dominate |
| Detector signal yield | Active-generation actionable versus all findings | Assess detector usefulness |
| Review activity | Latest accepted review aggregate | See reviewed classification and proposal throughput |

Project concentration is withheld until the current active generation has at
least 80% resolved identity coverage, 100 resolved observations, and three
distinct projects. Identity resolution uses only allowlisted explicit source
fields; unresolved observations remain unresolved.

## Review activity and lifecycle roadmap

Review telemetry uses immutable session changes and activity snapshots. Activity
counts accepted classification and proposal facts only. Capability probes,
exported sessions, absent token fields, and proposal state transitions do not
become review activity. An absent current snapshot is unavailable; a persisted
zero is factual.

The removed lifecycle views can return only after these gates are met:

| Future panel | Data and purpose | Reintroduction gate |
| --- | --- | --- |
| Pending review queue | Concurrent accepted review sessions and age | Three concurrent sessions across two snapshots, or a 168-hour service-level breach |
| Review outcomes | Terminal accepted proposal outcomes by intervention type | Ten terminal proposals across two intervention types |
| Review token ledger | Immutable accepted run token fields | Five accepted runs across two sessions |
| Post-application observation change | Application generation plus exact seven-day baseline and follow-up observation windows | Five closed evaluable applications; aggregate view at ten |

An application stores its immutable application generation. Its baseline is the
preceding seven calendar days and its follow-up is the next seven. Both windows
need at least 152 of 168 successful or no-data source slots, proven fourteen-day
source retention, persisted window bounds, and an unchanged active generation.
Otherwise the result is not evaluable.

## Rollout sequence

1. Run migrations and verify SQLite integrity.
2. Stage and remotely activate the initial analysis generation.
3. Update the existing dashboard entity in place from the generated asset.
4. Run a normal scan and confirm terminal pipeline and review snapshots.
5. Validate the eight panels in the in-app browser over the seven-day range.
6. Record any material contract change, stage its replacement generation, and
   promote it only after delivery and remote-verification evidence.

## Required evidence

- passing format, lint, type, and test suites;
- migration backup and SQLite foreign-key checks;
- stage and activation identifiers plus remote event verification;
- a succeeded or no-data normal scan and terminal snapshot;
- an hourly schedule status with the configured interval;
- dashboard update proof for the existing route and browser confirmation for all
  eight panels;
- a completion review with no objective-relevant unresolved issue.
