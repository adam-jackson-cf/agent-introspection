# Facts Store

Use this guide to understand how producer telemetry becomes the facts the dashboard and
findings read, and how the signal registry controls what is shown.

## Purpose

The dashboard reads ClickHouse-materialized facts, under the
[standing decisions](#standing-decisions) below. Refreshable materialized views copy
curated producer spans and logs from SigNoz into the durable `introspection` database
every minute, with raw prompt, command, argument, and output text and identity keys
removed.

Logs exported late are loaded as long as their event is at most 7 days old: the minute
loader selects by arrival (observed) time within that limit, and the hourly sweep
re-reads the last 3 days by event time. A log whose event is older than 7 days when it
arrives is never loaded. This limit is accepted; `facts backfill` can recover such rows
by hand.

Hook events and session attribution records (see [Hook Events](hook-events.md))
are not read from a local inbox: every machine sends them over OTLP to SigNoz, and
the loaders `load_hook_events` and `load_session_projects` (every minute, selecting by
`observed_timestamp` over the last 30 minutes) plus their hourly sweeps (last 3 days)
copy them into `introspection.hook_events` and `introspection.session_projects`. The
session loader keeps the newest `resolved_at` per session. Consequences:

- These records sit in SigNoz for its 90-day log retention; a record that arrives more
  than 90 days after it was produced is lost. The facts tables keep what was loaded.
- Accepted batches can still be lost; the Pipeline "Hook spool by host" panel compares
  sent with received. The check is described under
  [Delivery](hook-events.md#delivery).

## Signal support registry

[`signal_support.toml`](../../src/agent_introspection/facts_sql/signal_support.toml)
defines each dashboard signal once and records, per harness, the route that reaches it,
whether that route is aligned or differs, or why it is not emitted. `facts install`
validates it and loads it into `introspection.signal_*` tables; every panel's info note
is rendered from those tables.

Parity is judged over the harnesses this machine uses: config `[harnesses] enabled`
(registry harness keys, or `codex` for the three Codex surfaces; absent means every
harness). `facts install` rejects unknown keys and loads the list into
`introspection.harnesses`. The visibility rule is in the
[harness support matrix](data-gaps.md#harness-support-matrix). A signal no harness produces is listed under `[[excluded]]`.
The findings detectors, evidence packs, and evaluations count only enabled harnesses.

## Standing decisions

Other docs cite these by ID (for example "decision D4").

| ID  | Decision                                                                                                 |
| --- | -------------------------------------------------------------------------------------------------------- |
| D1  | Facts are materialized inside ClickHouse (database `introspection`), not in SQLite.                      |
| D2  | There is no SigNoz re-emission (outbox), read-back, or proof register.                                   |
| D3  | Measures are per call where no logical request ID exists, and inferred values are allowed when labelled. |
| D4  | Store only normalized forms of prompts, commands, and tool output. Raw text is never copied.             |
| D5  | The React companion is built as question-led views.                                                      |

Under D4, prompt text and identity keys (`user.email`, account, organization, and user
IDs) are dropped from spans and logs, and `prompt_length` is kept. omp tool intent text
is dropped. Span status messages keep only the first error-like line, normalized like
`x.failure_signature`, and span names show home directories as `~`. The kept `x.*`
fields are listed in [Signal Catalog](signal-catalog.md#2-normalized-fact-rows).

Codex spans are kept by allowlist (`session_task.*`, `*sampling_request`,
`turn/start|interrupt|steer`, and `handle_responses` with usage); everything else Codex
emits is plumbing and is not loaded.

## Retention and backups

- SigNoz keeps 90 days of raw traces and logs. The `introspection` tables have no TTL
  and hold history beyond that. Trace history before 2026-09-10 had already expired
  under an earlier 15-day TTL and cannot be recovered.
- Never drop `hook_events` (the only copy of activity-hook history) or
  `session_projects` (it keeps attribution for sessions the harnesses have since
  pruned). `facts install` recreates loaders and views without touching data.
- The `introspection` tables live in the SigNoz ClickHouse volume. Only history older
  than 90 days is irreplaceable; back it up before any SigNoz upgrade or stack
  recreation once the store holds more than 90 days.

## Steps

```sh
uv run agent-introspection facts install             # tables, loaders, views, snapshots, registry
uv run agent-introspection facts backfill --days 90  # re-project everything SigNoz retains
uv run agent-introspection facts status              # loader state and per-harness freshness
```

`facts sync` (the Mac's scheduled job, `facts schedule install`) runs in this order:
`spool sync` (attribution scan into the spool, status record, OTLP drain), then
`SYSTEM REFRESH VIEW` and `SYSTEM WAIT VIEW` for `load_hook_events` and
`load_session_projects`, then prompt labels, findings, and evaluations. Other machines
run only `agent-introspection spool sync`, from a systemd user timer. `facts backfill`
re-projects hook events and session attribution from SigNoz with the same family
filters.

## What To Check

- `facts status` shows every loader fresh for every harness.
- The dashboard Pipeline view shows loaders, freshness, parity, and the coverage grid;
  the [`introspection-operations`](../../.agents/skills/introspection-operations/SKILL.md)
  skill covers health checks and signal changes.

## Related Docs

- [Dashboard Data Gaps](data-gaps.md): what cannot be shown, per harness
  and signal.
- [SigNoz Connection](signoz-connection.md): grants the facts store needs.
