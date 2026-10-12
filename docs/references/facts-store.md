# Facts Store

Use this guide to understand how producer telemetry becomes the facts the dashboard and
findings read, and how the signal registry controls what is shown.

## Purpose

The dashboard reads ClickHouse-materialized facts; see the
[Dashboard v3 Plan](../dashboard-v3-plan.md). Refreshable materialized views copy
curated producer spans and logs from SigNoz into the durable `introspection` database
every minute, with raw prompt, command, argument, and output text and identity keys
removed.

Logs exported late are loaded as long as their event is at most 7 days old: the minute
loader selects by arrival (observed) time within that limit, and the hourly sweep
re-reads the last 3 days by event time. A log whose event is older than 7 days when it
arrives is never loaded. This limit is accepted; `facts backfill` can recover such rows
by hand.

Diagnostic text (failure signatures and span status messages) has credential
assignments (`token=`, `password=`, `credential=`, `auth=`, `private_key:`, `api_key:`,
quoted `token="..."` and JSON `"api_key": "..."` values, `Authorization: Bearer ...`),
spaced forms (`API key <value>` when the value has a digit or 16+ characters), common key
prefixes (`sk-`, `ghp_`, `xox*-`, `AKIA`) and any run of 20+ `[A-Za-z0-9_-]` characters
containing both letters and digits replaced with `[REDACTED]` in both the hook and the
SQL projections (`hooks.REDACTIONS` is the single list; the SQL mirrors it in order).
This is pattern-based and best-effort: an unlabelled short credential in free text can
still be stored, which is why only one diagnostic line, capped at 80 characters, is kept
and the facts store stays on this machine. Beside the capped text, a 16-hex-character
SHA-256 of the full normalized line (after redaction, home → `~`, digits → `N`, before the
cap) is stored (`x.failure_signature_hash` on Codex logs, `x.status_hash` on span
attributes, `failure_signature_hash` on hook events) and exposed as
`tool_calls.failure_signature_hash`, so failures sharing an 80-character prefix stay
distinct without storing more text; it is empty when there is no signature. Rows
stored before 2026-10-01 kept up to 160 bytes and no hash: `tool_calls` applies the
same keyword window and 80-character cap to them, and hashes their text when it is
under 160 bytes (the whole line), so old and new rows of one failure share text,
`failure_class`, and hash. Old lines of 160 bytes or more were truncated and keep no
hash, so the dashboard groups them by text. `facts install` replaces the registry
tables only after every route predicate validates.

The views `usage_events`, `task_outcomes`, `tool_calls`, `user_signals`, `model_calls`,
and `task_labels` normalize omp, Codex, and Claude Code telemetry, joined with the
activity-hook events that close what a producer's own telemetry lacks (see
[Hook Events](../hook-events.md)). Each view's last 91 days are held in two tables read
through one `<view>_snapshot` view:

- `<view>_recent` holds the last 8 days, the days whose facts can still change (late
  logs up to 7 days, span and log sweeps over 3 days, prompt labels over 2 days). It is
  rebuilt every 10 minutes after the minute loaders, reading one extra day of spans and
  logs so rows near its start are computed with their neighbours.
- `<view>_history` holds the older days and is rebuilt once a day at 00:30 UTC.
- `<view>_snapshot` takes rows from history before the start of today minus 7 days (UTC)
  and from recent after it. Both tables overlap that boundary, so between rebuilds no
  row is missed or repeated.

Each table is replaced whole on every rebuild, so neither holds duplicates or rows the
facts no longer produce. The dashboard trails SigNoz by up to about 10 minutes, and
windows starting more than 90 days ago read the live views. Older rows can be up to a
day stale only when their facts change more than a week late: a task whose spans end
more than a week after it started, or hook events delivered more than a week late.
`task_labels` reads the `task_outcomes` snapshot, so its view (`004_labels.sql`) is
created after the other snapshots, and its rebuilds wait for those of `task_outcomes`.

`facts install` first builds the whole store in a scratch `introspection_verify`
database (also `facts verify`), so SQL that ClickHouse rejects fails before the live
loaders are dropped. `facts verify --compare` rebuilds every snapshot now and checks
that each matches a full read of its view (row count and a hash of every column). It
leaves out the newest 15 minutes and the oldest day, and rereads every view in full, so
run it after an install or on demand.

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
