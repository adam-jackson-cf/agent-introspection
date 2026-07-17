# Agent Introspection Application

## Summary

Create `/Users/adamjackson/Projects/inflight/signoz-introspection` as a Git-versioned Python application that:

- Mines Codex telemetry from the existing local SigNoz instance.
- Reduces telemetry deterministically before using an LLM.
- Stores observations, trends, proposals and approval history in SQLite indefinitely.
- Runs deterministic scans hourly through `launchd`.
- Uses GPT-5.6 Luna only for bounded semantic classification.
- Uses GPT-5.5 high only for actionable trend analysis and proposal drafting.
- Emits duplicate-tolerant derived telemetry into SigNoz.
- Provides a custom SigNoz dashboard.
- Never applies a proposal without a separate explicit user request.
- Creates its companion agent skill strictly through `$create-skill`.

## Technology

Use:

- Python `>=3.12` and `uv`.
- Standard-library `sqlite3`, `argparse`, `tomllib`, `subprocess`, `json`, `hashlib`, `zoneinfo` and `urllib`.
- OpenTelemetry SDK with OTLP protobuf over HTTP to `localhost:4318`.
- Existing `docker exec signoz-clickhouse clickhouse-client` access.
- `ruff`, `mypy` and `pytest`.

Do not add PostgreSQL, a ClickHouse driver, another container or another database service.

## Capability Gates

Before implementing dependent functionality:

1. Verify SigNoz health, Compose location, Docker host, ClickHouse client and collector listeners.
2. Bind 8080, 4317, and 4318 to 127.0.0.1 only and disable OrbStack LAN port exposure.
3. Provision the required root user through Infisical runtime injection, then enable all three impersonation settings:
   - `SIGNOZ_IDENTN_IMPERSONATION_ENABLED=true`
   - `SIGNOZ_IDENTN_TOKENIZER_ENABLED=false`
   - `SIGNOZ_IDENTN_APIKEY_ENABLED=false`
4. Verify `/api/v1/global/config` reports impersonation enabled.
5. Inventory ClickHouse databases, tables, columns, timestamp units, retention, event names and attribute keys.
6. Store an approved schema fingerprint in SQLite.
7. Fail closed on subsequent schema drift; do not maintain compatibility queries.
8. Verify the installed SigNoz UI supports dashboard JSON import/export, identity-preserving updates, variables, ClickHouse panels and locking.
9. Verify `multi_agent_v1`, GPT-5.6 Luna medium, GPT-5.5 high and model telemetry correlation using one schema-only model-specific Codex background subagent canary per model.
10. Store model capability proof for thirty days or until the tool/schema version changes.
11. If a required capability is unavailable, stop and report the required rescope. Do not substitute another model or data source.

## CLI

Install one `agent-introspection` command:

- `doctor`: verify capabilities and capture schemas.
- `health`: check/start SigNoz and recheck health.
- `scan`: extract telemetry and run deterministic detectors.
- `candidates export`: create a bounded review session.
- `classification import --input-json -`: validate and store Luna output.
- `proposal create --input-json -`: validate and store GPT-5.5 output.
- `proposal list|show|decide|mark-applied`.
- `telemetry drain`: retry pending derived events.
- `dashboard verify`.
- `db check|backup|maintenance|restore`.
- `schedule install|status|remove`.

Return structured JSON on stdout, diagnostics on stderr and stable non-zero exit codes.

## SQLite

Store the database at:

```text
~/.local/share/agent-introspection/introspection.sqlite3
```

Create tables for:

- scan runs and source schema snapshots
- source watermarks
- project identities and aliases
- observations and evidence
- findings and finding membership
- trend evaluations
- semantic classifications
- proposals and immutable proposal events
- review sessions, model runs and model budget ledger
- OTLP outbox
- scheduler leases
- migrations

Enable WAL, foreign keys and a busy timeout. Use immutable-table triggers for historical records. Commit observations and watermarks in one transaction.

Maintenance:

- `quick_check` before every scan
- weekly `integrity_check`, `ANALYZE` and online backup
- backup before every migration
- transactional, fail-closed migrations
- manual `VACUUM` only after a verified backup and more than 25% free pages
- tested restore command
- no observation, finding or proposal archival

## Identity Rules

- Canonical task: trace `thread.id`.
- Map log `conversation.id` to a task through shared `trace_id`.
- If only `conversation.id` exists, use a typed conversation identity.
- A trace with neither receives an episode identity but cannot count toward distinct-task thresholds.
- Turn identity: `turn.id`, then `turn_id`, scoped to its task.
- Resolve `cwd` and symlinks with `realpath`.
- Git project identity: real path from `git rev-parse --git-common-dir`, grouping worktrees.
- Non-Git identity: real path of the discovered project root.
- Repository renames require an explicit audited alias; never merge automatically.
- Normalize targets to project-relative POSIX paths.
- Calculate calendar days in `Europe/London`, including DST behavior.

## Source Queries

Use `signoz_logs.distributed_logs_v2` and `signoz_traces.distributed_signoz_index_v3`. Parameterize all bounds through `clickhouse-client`.

### Incremental Logs

```sql
SELECT
    timestamp,
    id,
    trace_id,
    span_id,
    attributes_string['event.name'] AS event_name,
    attributes_string['conversation.id'] AS conversation_id,
    attributes_string['call_id'] AS call_id,
    attributes_string['tool_name'] AS tool_name,
    attributes_string['success'] AS success_string,
    attributes_bool['success'] AS success_bool,
    attributes_string['duration_ms'] AS duration_ms,
    attributes_number['http.response.status_code'] AS status_code,
    attributes_string['decision'] AS decision,
    attributes_string['source'] AS decision_source,
    attributes_number['input_token_count'] AS input_tokens,
    attributes_number['output_token_count'] AS output_tokens,
    attributes_number['reasoning_token_count'] AS reasoning_tokens,
    attributes_number['prompt_length'] AS prompt_length
FROM signoz_logs.distributed_logs_v2
WHERE timestamp > {start_ns:UInt64}
  AND timestamp <= {end_ns:UInt64}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
  AND resource.`service.name`::String
      IN ('codex_cli_rs', 'codex-app-server')
ORDER BY timestamp, id;
```

The broad query must not select prompts, arguments, outputs or errors.

### Turn Context

```sql
SELECT
    trace_id,
    coalesce(
      nullIf(anyIf(attributes_string['turn_id'],
                   attributes_string['turn_id'] != ''), ''),
      nullIf(anyIf(attributes_string['turn.id'],
                   attributes_string['turn.id'] != ''), '')
    ) AS turn_id,
    nullIf(anyIf(attributes_string['thread.id'],
                 attributes_string['thread.id'] != ''), '') AS thread_id,
    nullIf(anyIf(attributes_string['cwd'],
                 attributes_string['cwd'] != ''), '') AS cwd,
    min(timestamp) AS started_at,
    max(timestamp) AS ended_at,
    sumIf(
      attributes_number['codex.usage.total_tokens'],
      mapContains(attributes_number, 'codex.usage.total_tokens')
    ) AS total_tokens,
    countIf(attributes_string['tool_name'] != '') AS tool_calls
FROM signoz_traces.distributed_signoz_index_v3
WHERE timestamp BETWEEN {start:DateTime64(9)} AND {end:DateTime64(9)}
  AND ts_bucket_start BETWEEN {start_bucket:UInt64} AND {end_bucket:UInt64}
  AND serviceName IN ('codex_cli_rs', 'codex-app-server')
  AND (
    name IN (
      'run_sampling_request', 'session_task.turn', 'turn/start',
      'turn/steer', 'turn/interrupt', 'handle_responses'
    )
    OR mapContains(attributes_number, 'codex.usage.total_tokens')
    OR mapContains(attributes_string, 'tool_name')
  )
GROUP BY trace_id;
```

### Candidate Predicates

Identify:

- `codex.tool_result` with string `success='false'`.
- API/WebSocket events where Boolean `success` exists and is false.
- API responses with status `>=400`.
- `codex.sandbox_outcome`.
- User-sourced `codex.tool_decision`.
- `turn/interrupt` and `turn/steer`.
- Repeated normalized operations in one task.
- Failed quality commands followed by mutations before the same command passes.
- Tool-call and token values above project/task p95 after twenty comparable episodes.

Missing Boolean values must never be interpreted as false.

### Evidence Hydration

Fetch raw fields only by shortlisted log IDs, trace IDs or call IDs.

Session JSONL is an approved secondary enrichment source, not a fallback:

- It can enrich only an existing SigNoz candidate.
- It can supply only missing assistant output or complete tool ordering.
- It must correlate to the SigNoz task, trace or turn.
- It cannot independently create or promote a finding.
- Failed correlation leaves the candidate pending.

The user has approved retaining relevant full local context. Secret-pattern scanning still replaces recognized values with `[REDACTED]`; scanner errors quarantine the evidence without storing raw content. Raw prompts and tool bodies are never emitted into derived SigNoz telemetry.

## Detectors

Implement versioned detectors for:

- tool failure
- repeated attempt
- transport instability
- sandbox friction
- turn correction
- quality-gate bypass
- command churn
- tool loop
- token outlier
- skill adherence
- scope recurrence

Fingerprint observations from:

```text
detector version
category
project identity
operation kind
target kind
normalized target
normalized failure class
```

Parse structured tool arguments and shell argv. Remove only declared volatile values such as timestamps, generated IDs, temporary roots and line positions. Preserve executable, subcommand, target, exit code and stable diagnostic code.

Store the fingerprint components, normalization version and human-readable membership explanation.

## Trends

States:

- `isolated`: retained, never proposed.
- `emerging`: repeated but below threshold.
- `actionable`: eligible for GPT-5.5 analysis.
- `dormant`: previously actionable but absent from the selected view.

Actionable means either:

- at least three occurrences across two canonical tasks and two local calendar days within seven days; or
- at least five occurrences across three canonical tasks within seven days.

Nothing is automatically deleted or resolved.

## Model Protocol

The Python application does not call a model API.

The skill creates a review session containing:

- nonce
- schema version
- ordered candidate IDs
- SHA-256 payload hash
- byte count
- reserved model budget

It then invokes verified model-specific Codex background subagents:

- GPT-5.6 Luna medium for semantic classification.
- GPT-5.5 high for actionable trend analysis and proposals.

Hard limits per interactive review:

- Luna: 40 episodes, four calls, ten episodes and 24,000 input characters per call.
- GPT-5.5: eight findings and eight calls, 48,000 input characters per call.
- Combined: twelve calls.
- Accepted Luna JSON: 8,000 characters.
- Accepted GPT-5.5 JSON: 16,000 characters.

Validate nonce, payload hash, IDs, requested model, effort and schema before importing output. Query SigNoz for actual model/token telemetry before the next call. Missing provenance or exceeded token ceilings halts further calls and defers remaining candidates unchanged.

## Intervention Decision

Always evaluate deterministic enforcement first, using the exact order:

1. Established tools of a project first.
2. New tools second.
3. Bespoke scripts third.

Record why each earlier tier cannot enforce the concept.

Only when deterministic enforcement cannot represent the behavior:

- Improve an existing skill through `$create-skill` when that workflow owns the defect.
- Create a new skill through `$create-skill` for a repeated ordered workflow without an owner.
- Add concise `AGENTS.md` guidance for cross-workflow behavior.

Choose folder guidance for localized recurrence, project guidance for repository-wide recurrence and `~/.codex/AGENTS.md` only for cross-project recurrence.

Produce one primary intervention per finding.

## Proposal and Approval

Every proposal includes:

- root cause and trend window
- occurrence, task and day counts
- representative evidence and membership rationale
- exact intervention, scope, target and intended change
- established-tool audit
- rejected alternatives
- validation and rollback criteria
- predicted success metric
- create-skill handoff fields when applicable

States:

```text
pending -> approved | rejected
approved -> applying
applying -> applied | implementation_failed
```

Approval records a decision only. A separate explicit task is required to enter `applying`. Store validation evidence before marking `applied`.

## Derived Telemetry

Use `service.name=agent-introspection`.

Every derived log includes deterministic:

```text
event.id
entity.id
entity.version
event.sequence
event.name
```

Outbox retries reuse the identical event ID and payload. Historical OTLP rows are never deleted or replaced.

Dashboard rules:

- Event totals use `uniqExact(event.id)`.
- Entity state uses `argMax(..., tuple(entity.version, timestamp))`.
- Only logs are replayed.
- Traces describe individual scan attempts.
- Metrics are current-state gauges carrying `snapshot.id`, not replayed counters.

## Dashboard Queries

All log panels use:

```sql
timestamp BETWEEN $start_timestamp_nano AND $end_timestamp_nano
AND ts_bucket_start BETWEEN $start_timestamp - 1800 AND $end_timestamp
AND resource.`service.name`::String = 'agent-introspection'
```

Examples:

```sql
-- Observations by detector
SELECT
  toStartOfDay(fromUnixTimestamp64Nano(timestamp)) AS ts,
  attributes_string['detector.id'] AS detector,
  toFloat64(uniqExact(attributes_string['event.id'])) AS value
FROM signoz_logs.distributed_logs_v2
WHERE <common-filter>
  AND attributes_string['event.name']
      = 'introspection.observation.detected'
GROUP BY ts, detector
ORDER BY ts;
```

```sql
-- Current trend state
SELECT trend_state, count() AS value
FROM (
  SELECT
    attributes_string['entity.id'] AS finding_id,
    argMax(
      attributes_string['trend.state'],
      tuple(attributes_number['entity.version'], timestamp)
    ) AS trend_state
  FROM signoz_logs.distributed_logs_v2
  WHERE <common-filter>
    AND attributes_string['event.name']
        IN ('introspection.trend.evaluated',
            'introspection.trend.promoted')
  GROUP BY finding_id
)
GROUP BY trend_state;
```

Dashboard panels:

- latest scan health and source availability
- observations by detector and project
- current trend-state distribution
- actionable trends table
- pending proposal age and scope
- proposal outcomes by intervention type
- detector promotion and approval ratios
- post-application recurrence
- model calls, provenance and token usage
- scan duration over time, with current source lag and rows processed in scan health
- outbox backlog
- SQLite integrity, size and backup age
- project concentration

Version the dashboard JSON with a stable identity and schema version. Import, update, export, compare and lock it through the capability-verified SigNoz UI workflow. SigNoz documents distributed log tables, dashboard timestamp variables and JSON dashboard import. [Log query schema](https://signoz.io/docs/userguide/logs_clickhouse_queries/) [Trace query schema](https://signoz.io/docs/userguide/writing-clickhouse-traces-query/) [Dashboard import](https://signoz.io/docs/dashboards/import-dashboard/)

## Scheduling

Install a user LaunchAgent:

```text
Label: com.adamjackson.agent-introspection
Interval: 3600 seconds
RunAtLoad: true
KeepAlive: false
```

Set absolute CLI path, `PATH`, `DOCKER_HOST`, config path, locale, working directory and stdout/stderr paths.

Use a SQLite lease acquired with `BEGIN IMMEDIATE`, PID, heartbeat and expiry. Manual and scheduled scans share the lease. Reclaim stale leases only after both PID absence and expiry.

Scheduled mode permits one successful or no-data run per UTC interval slot. A boot or wake invocation catches up in the current slot from the stored watermark. Failed runs remain eligible for a same-slot retry, while the shared lease prevents overlap.

## Skill Creation

Generate `.agents/skills/agent-introspection` using `$create-skill` and the `multi-step-workflow` route.

Mandatory phases:

1. Preflight references, templates and relative output root.
2. Select `multi-step-workflow`.
3. Capture requirements, then stop for frozen-requirements approval.
4. Build the typed payload, then stop for source-of-truth approval.
5. Preview without writes.
6. Require checklist `PASS`.
7. Generate approved files and require written revalidation `PASS`.

The no-stage/no-commit rule before generation applies specifically to companion-skill artifacts. Do not create `generation-summary.md`.

The skill operates health, scans, bounded model review, proposal persistence and approval recording. It never applies proposals.

## Implementation Sequence

1. Initialize Git and runtime exclusions.
2. Build configuration, SQLite migrations and CLI.
3. Implement schema discovery and SigNoz health/start behavior.
4. Implement source queries, identities, normalization and detectors.
5. Implement trends, proposal state and SQLite maintenance.
6. Implement model review sessions and capability verification.
7. Implement duplicate-tolerant OTLP telemetry.
8. Verify dashboard capabilities, build/import the dashboard and test every panel.
9. Install and verify `launchd`.
10. Pass application quality and end-to-end tests.
11. Commit the validated application atomically.
12. Execute every `$create-skill` approval and validation phase.
13. Commit the validated companion skill atomically.
14. Confirm clean Git status and produce the implementation report.

## Acceptance

Verify:

- schema drift fails before extraction
- empty logs with valid traces
- empty traces with valid logs
- validated no-data scans
- idempotent overlap and duplicate OTLP replay
- worktree, symlink, renamed-path and DST identities
- broad behavioral detector scenarios
- one-off findings never produce proposals
- budget exhaustion defers work without failing scans
- arbitrary or unprovenanced model JSON is rejected
- session JSONL cannot create observations
- secret scanner uncertainty stores no raw content
- approval cannot mutate target repositories
- migration, corruption, backup and restore behavior
- dashboard filters only derived telemetry and every panel renders
- scheduled scans cannot overlap and catch up after missed days
- every create-skill gate passes
- `ruff`, `mypy` and `pytest` pass
- Git contains two atomic commits and no runtime data

The final implementation report must disclose achieved evidence, deviations, rejected alternatives, unresolved risks, deferred candidates, unverified provenance, failed capabilities and dashboard, scheduler, Git and quality status.
