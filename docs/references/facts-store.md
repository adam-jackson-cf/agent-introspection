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

Hook events (see [Hook Events](hook-events.md)) are not read from a local inbox:
every machine sends them over OTLP to SigNoz, and the loader `load_hook_events`
(every minute, selecting by `observed_timestamp` over the last 30 minutes) plus its
hourly sweep (last 3 days) copy them into `introspection.hook_events`. Consequences:

- These records sit in SigNoz for its 90-day log retention; a record that arrives more
  than 90 days after it was produced is lost. The facts tables keep what was loaded.
- A collector acknowledgement is not durable storage: a collector restart before the
  ClickHouse write can drop a batch even though the sender deleted its files. This
  exposure is accepted and detected, not prevented. Over the last 24 hours, each host's
  sent count is the sum of positive increments between consecutive spool status
  heartbeats (`sent_total`, per family; a decrease is a counter reset, and the
  post-reset value counts as its own increment), ordered by each heartbeat's own
  producer timestamp. It is compared with the records SigNoz holds for that host (the
  `host.name` resource, counted by arrival `observed_timestamp` like the loaders), and
  heartbeats from the last 5 minutes are ignored so in-flight batches do not read as
  lost. The Pipeline "Hook spool by host" panel shows received / sent per family and
  flags a host when received falls short of sent by more than 2 records or 2%. The
  heartbeat ordering and the first-window caveat are in
  [Delivery](hook-events.md#delivery).
- Readers select these families by `service.name`, `event.name`, and the body's
  `schema`. Older rows under the same service name come from a retired pipeline and are
  never loaded.

## Sanitization

Home paths are redacted (`/Users/<name>`, `/home/<name>`, `/root` become `~`) in the
hook record and the SQL projections. Rows loaded before the home
redaction widening (the `HOME_PATH_CUTOFF` constant, currently 2026-10-13 00:00 UTC, in
`dashboard/server/record-route.ts`, used by the Pipeline view's sanitization query) can
still hold a Linux home path. They are not rewritten; the sanitization row counts home paths only for rows loaded at or after the cutoff, and the facts store
stays on this machine.

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
tables only after every route predicate validates (see [Install and verification](#install-and-verification)).

## Fact views and snapshots

The views `usage_events`, `task_outcomes`, `tool_calls`, `user_signals`, `model_calls`,
and `task_labels` normalize omp, Codex, and Claude Code telemetry, joined with the
activity-hook events that close what a producer's own telemetry lacks (see
[Hook Events](hook-events.md)). Each view's last 91 days are held in two tables read
through one `<view>_snapshot` view:

- `<view>_recent` holds the last 8 days, the days whose facts can still change (late
  logs up to 7 days, span and log sweeps over 3 days, prompt labels over 2 days). It is
  rebuilt every 10 minutes after the minute loaders, at its own minute (see the schedule
  map below), reading one extra day of spans and
  logs so rows near its start are computed with their neighbours.
- `<view>_history` holds the older days and is rebuilt once a day, between 00:35 and 01:35 UTC, one view at a time.
- `<view>_snapshot` takes rows from history before the start of today minus 7 days (UTC)
  and from recent after it. Both tables overlap that boundary, so between rebuilds no
  row is missed or repeated.

Refreshers are staggered so heavy queries rarely overlap; ClickHouse kills a
query when total server memory passes its limit. Every refresher is created `EMPTY`,
so creating one runs nothing, and `facts install` then refreshes the minute loaders,
the histories, then the recents one at a time (each retried once on failure). Sweeps are not run at install; each waits for its slot.
A failed scheduled refresh retries after a backoff counted from the end of the failed
attempt: minute loaders after 30 to 60 seconds (three times), recents after 10 minutes
(twice), sweeps after an hour (once), and histories after 70 minutes (three times),
which clears the whole 00:35 to 01:35 block, so only histories are isolated from one
another. The server does not retry the out-of-schedule refreshes `facts install` runs;
install retries each failed one once itself, attempts every refresher, skips a
snapshot whose upstream failed, and reports all failures together. `facts refresh
<refresher>` fills one table without a full rebuild. Within each
10-minute period (UTC):

| Minute | Runs                                                                   |
| ------ | ---------------------------------------------------------------------- |
| `:x0`  | the minute loaders only                                                |
| `:x1`  | `usage_events` recent                                                  |
| `:x2`  | `task_outcomes` recent                                                 |
| `:x3`  | `task_labels` recent (right after `task_outcomes`, which it waits for) |
| `:x4`  | `tool_calls` recent                                                    |
| `:x5`  | one `_history` refresher a day, 00:35 to 01:35                         |
| `:x6`  | `user_signals` recent                                                  |
| `:x7`  | `model_calls` recent                                                   |
| `:x8`  | `model_call_attempts` recent                                           |
| `:x9`  | one hourly sweep each: `:09` spans, `:19` logs, `:29` hook events      |

A view that reads another snapshot (`task_labels` on `task_outcomes`) must have a later
offset, because ClickHouse waits for a dependency only when its offset is earlier in the
same period. Loader and sweep queries share the backfill memory settings
(`LOAD_SETTINGS`: 1.5 GiB ceiling, spilling to disk).

Each table is replaced whole on every rebuild, so neither holds duplicates or rows the
facts no longer produce. The dashboard trails SigNoz by up to about 10 minutes, and
windows starting more than 90 days ago read the live views. Older rows can be up to a
day stale only when their facts change more than a week late: a task whose spans end
more than a week after it started, or hook events delivered more than a week late.
`task_labels` reads the `task_outcomes` snapshot, so its view (`004_labels.sql`) is
created after the other snapshots, and its rebuilds wait for those of `task_outcomes`.

## Install and verification

`facts install` first builds the whole store in a scratch `introspection_verify_<time>_<pid>`
database of its own (also `facts verify`; scratch databases over an hour old are dropped), so SQL that ClickHouse rejects fails before the live
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
(registry harness keys, or `codex` for the three Codex surfaces; absent means Claude
Code and Codex). `facts install` rejects unknown keys and loads the list into
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
- Never drop `hook_events` (the only copy of activity-hook history). `facts install`
  recreates loaders and views without touching data. It drops only the objects of the
  removed project attribution (`session_projects`, its view, and its two loaders).
- The `introspection` tables live in the SigNoz ClickHouse volume. Only history older
  than 90 days is irreplaceable; back it up before any SigNoz upgrade or stack
  recreation once the store holds more than 90 days.

## Steps

```sh
uv run agent-introspection facts install             # tables, loaders, views, snapshots, registry
uv run agent-introspection facts backfill --days 90  # re-project everything SigNoz retains
uv run agent-introspection facts status              # loader state and per-harness freshness
```

`facts sync` (the SigNoz host's scheduled job from `facts schedule install`: a launchd
agent on macOS, a systemd user timer on Linux) runs in this order:
`spool sync` (status record and OTLP drain), then `SYSTEM REFRESH VIEW` and
`SYSTEM WAIT VIEW` for `load_hook_events`, then prompt labels, findings, and
evaluations. Additional machines without SigNoz run only `agent-introspection spool
sync`, from the onboarding skill's systemd user timer. `facts backfill` re-projects hook
events from SigNoz with the same family filter.

## What To Check

- `facts status` shows every loader fresh for every harness.
- The dashboard Pipeline view shows loaders, freshness, parity, and the coverage grid;
  the [`introspection-operations`](../../.agents/skills/introspection-operations/SKILL.md)
  skill covers health checks and signal changes.

## Related Docs

- [Dashboard Data Gaps](data-gaps.md): what cannot be shown, per harness
  and signal.
- [SigNoz Connection](signoz-connection.md): grants the facts store needs.
