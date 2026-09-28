# Agent Introspection

Agent Introspection mines existing Codex telemetry from the local SigNoz instance, applies deterministic reduction and detectors, and retains observations, trends, proposals, approval history, model provenance, and derived-telemetry delivery state in SQLite.

It never applies a proposal. Approval records a decision only; entering `applying` requires a separate explicit user request.

## Requirements

- Python 3.12 or newer and `uv`
- OrbStack with the existing SigNoz Compose project
- `docker --context orbstack` access to `signoz-clickhouse`
- OTLP protobuf over HTTP on `localhost:4318`

## Development

```sh
uv sync
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Install the Git pre-commit trigger and run the complete quality suite with:

```sh
uv run pre-commit install
uv run pre-commit run --all-files
```

Both pre-commit and CI use `scripts/run-ci-quality-gates.sh`. Run it directly for
the same check-only quality suite, including the independent dashboard package:

```sh
bash scripts/run-ci-quality-gates.sh
```

## Dashboard facts (v3)

The dashboard is moving to ClickHouse-materialized facts; see the
[Dashboard v3 Plan](docs/dashboard-v3-plan.md). Refreshable materialized views copy
curated producer spans and logs from SigNoz into the durable `introspection`
database every minute, with raw prompt/command text and identity keys removed.
The views `usage_events`, `task_outcomes`, `tool_calls`, and `user_signals`
normalize omp, Codex, and Claude Code telemetry.

```sh
uv run agent-introspection facts install          # create/refresh tables, loaders, views
uv run agent-introspection facts backfill --days 40  # copy retained SigNoz history
uv run agent-introspection facts status           # loader state and per-harness freshness
```

The companion and pipeline sections below describe the proof-gated approach
archived at tag `archive/proof-gated-dashboard`; they are replaced phase by phase.

## Dashboard companion

The repository-owned React companion is an independent Bun package in
`dashboard/`; it is not managed by the root package manifest. Its package
commands, which are included in the canonical quality suite, are:

```sh
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard check
bun run --cwd dashboard test
bun run --cwd dashboard lint
bun run --cwd dashboard format:check
bun run --cwd dashboard build
bun run --cwd dashboard start
```

The server binds only to `127.0.0.1:4173` and serves the five direct routes:
`/pipeline`, `/provider`, `/usage`, `/tools`, and `/recurrence`. It requires
OrbStack running with the existing SigNoz deployment stored at
`/Volumes/UGreen-External/Docker`, a healthy loopback UI at `127.0.0.1:8080`,
and collector listeners at `127.0.0.1:4317` and `127.0.0.1:4318`.

For each dashboard request, the Bun backend prepares the registry once and
uses a bounded 14.5-second application deadline with a 15-second transport
deadline. It launches the worktree's `.venv/bin/python` directly; that development
environment must already be available. On cancellation or deadline it
cooperatively terminates Python, cancels its owned ClickHouse query, and reaps
the process group. Noncooperative descendants are killed; cleanup failures remain
observable.

Every route deliberately retains its designed measurement, control, and
evidence-table places. Until authoritative production measurement/control data
and its proof gate are available, the companion shows its missing-data image and
`Missing data` there; it does not show a fake metric, zero, `No data`, or
`Unavailable`. `Unavailable` is reserved for a deployed contract that later
becomes incomplete or unqueryable, and static unsupported producer capabilities
remain `Not applicable`.

The primary sections follow
[`show-me-signoz-dashboard-mocks.html`](docs/mock/show-me-signoz-dashboard-mocks.html):
32 primary panels retain the mock's titles, order, spans, and desktop dimensions.
The remaining 23 widget positions follow under `Additional measurements`, keeping
all 55 canonical positions. Each panel's information button (`View contract details`)
opens its canonical title, contract, blockers, and producer proofs.

When the existing twelve central application bindings for P1–P4, P10–P12, and
Canonical scan evidence are qualified through `application_evidence.bindings`,
Pipeline health renders their real metrics in the existing mock-derived layout.
P5–P9 and the other four routes retain native qualification. A failed refresh
removes the previous result and shows `Query failed — outcome unknown`.

Calculation semantics and canonical signals are fixed. Authorized Python work may
only remove redundant execution work and must prove exact complete-report outcome
equivalence on identical captured inputs, filters, evaluation time, and
qualification context. A changed Python fingerprint requires genuine renewal of
central application-binding evidence for those same twelve bindings; it does not
authorize promotion of any of the 129 native rows, weakened gates, proof-policy
changes, installed production-emitter changes, or metric-definition edits.

The recovery diagnosed stopped OrbStack with an unchanged qualified fingerprint.
Restarting the existing backend restored transport, but current calculations also
needed redundant execution work removed to finish within the unchanged deadline.
The refined calculator and genuinely renewed evidence now restore all twelve
central attachments: eleven displayed measurements and P11's legitimate
`Unavailable` state for incomplete immutable observations. Current HTTP and
browser verification, including failed-refresh clearing and recovery, is recorded
in the [performance plan](docs/retired/dashboard-performance-improvement-plan.md#executed-python-scope-and-current-recovery).
The Completion time section remains unchanged.

Populated Pipeline health panels use value-first cards, compact counts, scaled
durations and byte units, and charts of the supplied series. The duration chart
shows `bucketed mean scan duration`; it does not invent p50/p95 history.
Exact values, nanosecond identities, range basis, measurement reasons, and
provenance remain available through `View contract details`. Tables use bounded
scroll regions and 20-row pages; `Previous` and `Next` retain access to every
returned row.

## CLI

```sh
uv run agent-introspection doctor
uv run agent-introspection health
uv run agent-introspection scan
uv run agent-introspection candidates export
uv run agent-introspection classification import --input-json -
uv run agent-introspection proposal list
uv run agent-introspection telemetry drain
uv run agent-introspection telemetry reconcile-observations --scan-run-id <failed-scan-id>
uv run agent-introspection dashboard verify
uv run agent-introspection db check
uv run agent-introspection schedule status
```

All command results are structured JSON on stdout. Diagnostics are written to stderr and failures use stable non-zero exit codes.

The installed user LaunchAgent runs at minute zero of each hour and once at user-session load. Missed hour boundaries coalesce into one run after wake. Scheduled mode permits one successful or no-data scan per UTC hourly slot, terminalizes interrupted runs before recovery, and uses a shared lease to prevent overlap. Each ClickHouse query has a ten-minute limit and each scan has a fifteen-minute deadline, so a stalled source produces a terminal failure instead of blocking later hourly slots.

Normal scans persist canonical activities and monotonic attribution versions, reconcile
session context at source-event time, and deliver deterministic activity events to SigNoz.
The insight dashboard selects the latest activity version within its source-time range.

## Pipeline measurement cutover

`[pipeline].measurement_start` accepts a quoted RFC3339 timestamp with an explicit
UTC offset, normalized to UTC millisecond precision. The authorized production
boundary is active:

```toml
[pipeline]
measurement_start = "2026-09-08T08:32:42.802Z"
```

An active boundary selects `measurement_start < cohort_time <= end` in the existing
central remote authority. Requested report bounds remain unchanged; crossing
ranges disclose the effective lower bound, and wholly pre-cutover backend reports
are `Unavailable`. The companion retains `Missing data` when no deployment qualifies.
Complete immutable histories of admitted identities and required
pre-display-range ownership remain available for validation. Historical records,
native SigNoz retention, production scheduling, and the authority store are not reset.
See the [proof register](docs/dashboard-metric-proof-register.md#current-production-acquisition-and-verification)
for the retained acquisition, independent oracle, and application verification.

## Codex Desktop Attribution

Codex Desktop attribution uses documented global `SessionStart` and `SessionEnd`
hooks. The hook emits only the native session ID, absolute workspace, lifecycle
event, and timestamp to the local session-context runtime. The shared runtime
resolves the canonical Git workspace; the hook never inspects or forwards
prompts, responses, or transcripts. User hooks require interactive
review/trust and are never automatically approved.
Codex's active configuration root is `$CODEX_HOME` when it is set to a non-empty absolute path; otherwise it is `~/.codex`. Global hooks live at `<codex-root>/hooks.json`, and trust state lives at `<codex-root>/config.toml`.

**Producer boundary is not qualified.** These global hooks also run in Codex CLI,
and their documented native envelope does not distinguish CLI from Desktop.
A real CLI run emitted a `codex-app-server` end record; the normal scanner
quarantined it. The two newly added global Desktop registrations were rolled back,
preserving unrelated hooks, trust settings, runtime bytes, and evidence.
Do not install the Desktop adapter into a configuration root shared with Codex CLI
until an authoritative producer boundary is available.

Changing projects within a live Codex Desktop thread is unsupported by design.
Attribution uses the workspace supplied at the thread's lifecycle boundary.
Supporting mid-thread project changes would require complex inspection or
interposition of app-server protocol traffic. This is considered rare, so the
additional complexity is not justified.

## Local SigNoz

The canonical Compose override is `ops/signoz/docker-compose.override.yaml`. It binds the UI and OTLP listeners to loopback, disables tokenizer and API-key authentication, and enables impersonation for this single-user workstation. Root-user values are injected from the connected Infisical project at runtime:

```sh
infisical run --env=dev -- docker --context orbstack compose \
  --project-directory "$HOME/.local/share/codex-observability/signoz/deploy/docker" \
  up --detach --force-recreate signoz
```

Never start this configuration without the loopback-only override and disabled OrbStack LAN port exposure.
