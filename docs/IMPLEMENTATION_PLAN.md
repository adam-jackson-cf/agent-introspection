# Agent Introspection Reliability Implementation Plan

## Objective

Make the local Agent Introspection system operate unattended on an hourly cadence, keep source-schema approval stable while the telemetry population changes, and present scan performance as valid, useful SigNoz time-series data. The repository at `/Users/adamjackson/Projects/inflight/signoz-introspection` is the canonical implementation target.

## Canonical decisions

1. Source approval protects only the extraction structures and queries used by this application. Rolling event names, attribute keys, unrelated columns, and unrelated table metadata are diagnostic evidence and do not participate in approval identity.
2. Scheduled scans run in 3,600-second UTC slots. `launchd` uses `StartInterval`, `RunAtLoad`, the SQLite lease, and a slot guard that suppresses only a successful or no-data run started in the current slot. The configured scheduler interval is the single source of truth.
3. The reviewed live dashboard route `019f4da0-4a13-7c62-9ac9-fc6d850d633b` is the canonical persisted dashboard entity. Its exported JSON UUID is a separate identity field. Updates preserve the route entity and never create a second dashboard.
4. Scan performance uses a single-unit duration graph with `ts` and `value`. Source lag and rows processed are current-run operational facts in the Scan health table, avoiding a mixed-unit graph and additional panels.
5. Trend evaluation remains a seven-day comparison window. Scan cadence does not alter trend semantics.
6. Deterministic scans do not invoke a model and therefore consume zero model tokens. Bounded classification and proposal-review runs remain explicit, separately metered workflows.

## Scope and task ledger

Implementation file scope: `README.md`, `config.example.toml`, `docs/PLAN.md`, this plan, `src/agent_introspection/{capabilities,cli,config,dashboard,scan,scheduler,source}.py`, `src/agent_introspection/assets/agent-introspection.json`, `tests/test_{capabilities,cli,config,dashboard,scan,scheduler}.py`, and `.agents/skills/agent-introspection/{SKILL.md,references/step-2-scan-workflow.md}`. A newly required focused test file may be added within this list; no other file may be edited without stopping for scope review.

### T1 — Stable source-contract approval

Dependencies: none.

- Make discovery return an explicit `{"contract": ..., "diagnostics": ...}` structure.
- Define the canonical contract in `capabilities.py` with this exact projection:
  - the ClickHouse server timezone;
  - the two required database/table identities;
  - only named physical columns referenced by `LOG_QUERY`, `TRACE_QUERY`, and every `HYDRATION_QUERIES` variant, with each column's database, table, name, type, default kind, and default expression;
  - a deterministically keyed SHA-256 hash for `LOG_QUERY`, `TRACE_QUERY`, and each hydration-query identity.
- Do not fingerprint complete `create_table_query` strings, engine metadata, unrelated columns, rolling event names, or rolling attribute-key inventories.
- Keep rolling event-name and attribute-key inventories in `diagnostics` and exclude diagnostics from approval persistence.
- Make `schema_fingerprint`, `approve_schema`, and `enforce_approved_schema` accept the discovery result and operate only on its `contract` member.
- Persist the canonical contract in new `source_schema_snapshots.schema_json` records without mutating existing immutable snapshots.
- Expand behavioral coverage so diagnostics, unrelated extra columns, and unrelated table metadata do not change the fingerprint, while timezone mutation, removal or type/default mutation of each required structural class, and mutation of each query class do.
- Prove that approval followed by repeated discovery and enforcement remains stable while telemetry records change.

Acceptance criteria:

- Telemetry-only diagnostic changes and unrelated structural additions leave the fingerprint unchanged.
- A required column contract, table identity, timezone, or canonical query change produces a different fingerprint and fails closed until explicitly approved.
- The scan cannot extract data when the structural contract is unapproved.
- A live `doctor --approve-schema`, `doctor`, and scan complete without approval churn.

### T2 — Hourly scheduling contract

Dependencies: none.

- Add a positive `scheduler.interval_seconds` configuration value with the canonical default `3600`; wire it through strict TOML parsing, generated configuration, and the example configuration.
- Define a UTC slot as `floor(now.timestamp() / interval_seconds)`. Suppress a scheduled invocation only when a `succeeded` or `no_data` run has `started_at` in that same slot.
- Permit a new run at the first instant of every new slot even when the prior completion is less than 3,600 seconds old. `RunAtLoad` catches up in the current slot. Failed runs do not satisfy the guard, and the SQLite lease prevents overlap with a long-running scan.
- Build the LaunchAgent with `StartInterval` and `TZ` from configuration and pass the same configured interval to scheduled-run evaluation.
- Propagate `scheduler.lease_seconds` into `scan_lease` and lease acquisition as a distinct duration from the scheduling interval.
- Return scheduled skip evidence containing the slot start, interval seconds, and qualifying run identity/start time.
- Update scheduler, CLI, configuration, and behavioral tests for same-slot retry, consecutive-slot execution, exact boundary, `RunAtLoad` catch-up, failed-run retry, no-data suppression, timezone-aware clocks, and long-running lease exclusion.
- Update repository documentation and the project Agent Introspection skill so hourly cadence and interval semantics are canonical throughout.

Acceptance criteria:

- The plist contains `StartInterval = 3600`, `RunAtLoad = true`, and no calendar schedule.
- A successful or no-data run started in the current UTC slot suppresses only that slot; a run started in the preceding slot never suppresses the current slot.
- Failed runs never satisfy the slot guard, while an active lease still prevents concurrent execution.
- Configuration rejects zero, negative, boolean, and unsupported scheduler values.
- `interval_seconds`, `timezone`, and `lease_seconds` reach their runtime consumers without hardcoded substitutes.
- Reinstalling the schedule produces an installed LaunchAgent whose effective configuration is hourly.

### T3 — Dashboard identity preflight and useful scan operations

Dependencies: none for preflight; live data validation depends on T4 runtime readiness.

- Export or inspect the live dashboard through the installed SigNoz UI or supported local API before editing. Record its route/entity ID, exported JSON UUID, dashboard count, schema, and supported identity-preserving update operation. Save a local backup export outside the import path.
- Treat route/entity ID `019f4da0-4a13-7c62-9ac9-fc6d850d633b` as canonical. Preserve its exported JSON UUID in the repository asset, updating the repository constant only when the export proves the field differs.
- Update the existing dashboard through the supported edit/save or update operation for that route. Do not use a create/import operation.
- Rewrite Scan performance as a single `Scan duration (ms)` graph whose query returns `ts` and `toFloat64(...) AS value` and preserves the common timestamp, bucket, service-name, event-name filters, and chronological ordering.
- Extend the Scan health table with `duration_ms`, `source_lag_ms`, and `rows_processed` from the latest completed scan so the other operational measures remain visible without mixed units or extra panels.
- Strengthen dashboard verification so every graph requires `ts` and `value`; require the Scan performance graph to expose only duration and require the Scan health query to expose the three canonical operational columns.
- Expand dashboard behavioral coverage and regenerate the checked-in dashboard asset from `build_dashboard()`.
- Keep the dashboard route/entity ID, exported JSON UUID, panel IDs, panel count, layout, and remaining panel definitions stable.

Acceptance criteria:

- Dashboard verification reports no issues.
- The generated dashboard exactly matches the checked-in JSON asset.
- The Scan performance query executes against live ClickHouse for a range containing scan telemetry and returns non-null numeric duration values.
- The Scan health query returns current duration, source lag, and rows processed values.
- The in-app browser shows a readable duration trend and current operational facts without a query or no-data error for a range containing completed scans.
- The browser remains on route `019f4da0-4a13-7c62-9ac9-fc6d850d633b`, and dashboard count proves no duplicate was created.

### T4 — Runtime rollout and proof

Dependencies: T1, T2, T3.

- Run the repository quality gates without suppression.
- Approve the live structural source contract once and run a successful deterministic scan.
- Reinstall the LaunchAgent and inspect its effective configuration.
- Back up and update the regenerated dashboard through the proven identity-preserving operation.
- Verify all dashboard panels in the in-app browser over a seven-day range, with focused proof for Scan health and Scan performance.
- Record exact command results, test counts, scan status, schema fingerprint stability, launchd status, query row counts, operational values, and browser observations without exposing secrets.

Acceptance criteria:

- Formatting, linting, type checking, and the full test suite pass.
- Two consecutive live source discoveries have the same approved fingerprint.
- A live scan ends in `succeeded` or `no_data` and leaves no failed telemetry delivery backlog.
- The effective LaunchAgent is loaded with the 3,600-second interval.
- The existing SigNoz dashboard route loads and every panel has either useful data for its defined event population or an accurate empty state; Scan health and Scan performance show current scan evidence and no duplicate dashboard exists.
- A controlled-clock scheduled-mode integration test proves same-slot suppression and consecutive-slot execution, and one real loaded LaunchAgent invocation is recorded.

## Execution order

1. Capture `git status --short`, staged and unstaged diff inventories, and hashes for unrelated changed files. Do not stage, unstage, commit, or edit outside the named implementation scope.
2. Complete the dashboard identity preflight, then implement T1, T2, and T3 as independent code slices while preserving the recorded index and unrelated changes.
3. Run focused tests for each slice, then the complete quality-gate suite.
4. Compare the post-implementation index, unrelated-file hashes, and unrelated diffs with the baseline.
5. Perform T4 against the local SigNoz and LaunchAgent runtime.
6. Run an independent completion review over the implementation, tests, runtime evidence, documentation, dashboard UX, and workspace-preservation proof. Resolve every objective-relevant issue and repeat review until the result is `ZERO_ISSUES`.

## Stop conditions

- Stop before any operation that would delete SigNoz, SQLite, user, organization, invitation, dashboard, or telemetry data.
- Stop if runtime proof requires displaying a secret rather than a presence check or masked value.
- Stop if an identity-preserving dashboard update operation cannot be proven or the operation would create a second dashboard.
- Stop if unrelated concurrent edits overlap a required file in a way that cannot be preserved.

## Verification evidence required at handoff

- Changed-file ledger mapped to T1–T4.
- Focused and full quality-gate command output with exit status and test count.
- Stable approved fingerprint evidence from consecutive discoveries.
- Successful live scan status and outbox state.
- Installed LaunchAgent interval and loaded status.
- Controlled-clock slot evidence plus one real loaded LaunchAgent invocation.
- Live ClickHouse Scan performance duration rows and latest Scan health operational values.
- In-app browser confirmation for the dashboard and every panel.
- Live dashboard route/entity ID, exported JSON UUID, before/after dashboard count, backup path, and update mechanism.
- Pre/post index state and unrelated-change comparison proving no staging, commit, or unrelated overwrite.
- Completion-review result of `ZERO_ISSUES`.
