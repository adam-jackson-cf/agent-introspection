# Facts Store

Use this guide to understand how producer telemetry becomes the facts the dashboard and
findings read, and how the signal registry controls what is shown.

## Purpose

The dashboard reads ClickHouse-materialized facts; see the
[Dashboard v3 Plan](../dashboard-v3-plan.md). Refreshable materialized views copy
curated producer spans and logs from SigNoz into the durable `introspection` database
every minute, with raw prompt, command, argument, and output text and identity keys
removed.

Log loading has a supported maximum export delay of 3 days: the minute loader reads by
observed time, and the hourly sweep re-reads the last 3 days by event time. A log
exported later than that, with both timestamps older than 3 days, is never loaded. This
limit is accepted; `facts backfill` can recover such rows by hand.

Diagnostic text (failure signatures and span status messages) has credential
assignments (`token=`, `password=`, `credential=`, `auth=`, `private_key:`, `api_key:`,
quoted `token="..."` and JSON `"api_key": "..."` values, `Authorization: Bearer ...`),
spaced forms (`API key <value>` when the value has a digit or 16+ characters), common key
prefixes (`sk-`, `ghp_`, `xox*-`, `AKIA`) and any run of 20+ `[A-Za-z0-9_-]` characters
containing both letters and digits replaced with `[REDACTED]` in both the hook and the
SQL projections (`hooks.REDACTIONS` is the single list; the SQL mirrors it in order).
This is pattern-based and best-effort: an unlabelled short credential in free text can
still be stored, which is why only one capped diagnostic line is kept and the facts
store stays on this machine. `facts install` replaces the registry tables only after
every route predicate validates.

The views `usage_events`, `task_outcomes`, `tool_calls`, `user_signals`, `model_calls`,
and `task_labels` normalize omp, Codex, and Claude Code telemetry, joined with the
activity-hook events that close what a producer's own telemetry lacks (see
[Hook Events](../hook-events.md)). A snapshot table of each view holds its last 91 days,
rebuilt right after every minute load; windows starting more than 90 days ago read the
live views.

## Signal support registry

[`signal_support.toml`](../../src/agent_introspection/facts_sql/signal_support.toml)
defines each dashboard signal once and records, per harness, the route that reaches it,
whether that route is aligned or differs, or why it is not emitted. `facts install`
validates it and loads it into `introspection.signal_*` tables; every panel's info note
is rendered from those tables.

Parity is judged over the harnesses this machine uses: config `[harnesses] enabled`
(registry harness keys, or `codex` for the three Codex surfaces; absent means every
harness). `facts install` rejects unknown keys and loads the list into
`introspection.harnesses`. A signal is shown only when every enabled harness reaches it;
a signal an enabled harness does not emit (`not emitted`) is hidden on this machine and
listed in the Pipeline view. A signal no harness produces is listed under `[[excluded]]`.
The findings detectors, evidence packs, and evaluations count only enabled harnesses.

## Steps

```sh
uv run agent-introspection facts install             # tables, loaders, views, snapshots, registry
uv run agent-introspection facts backfill --days 90  # re-project everything SigNoz retains
uv run agent-introspection facts status              # loader state and per-harness freshness
```

## What To Check

- `facts status` shows every loader fresh for every harness.
- The dashboard Pipeline view shows loaders, freshness, parity, and the coverage grid;
  the [`introspection-operations`](../../.agents/skills/introspection-operations/SKILL.md)
  skill covers health checks and signal changes.

## Related Docs

- [Dashboard Data Gaps](../dashboard-data-gaps.md): what cannot be shown, per harness
  and signal.
- [SigNoz Connection](signoz-connection.md): grants the facts store needs.
