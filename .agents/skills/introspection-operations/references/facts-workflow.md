# Facts workflow

## Objective

Change the facts projection, views, or signal support registry without losing history or drifting from SigNoz.

## Required actions

1. Edit only the repo-owned sources: `facts_sql/*.sql` and `facts_sql/signal_support.toml`.
2. Run `agent-introspection facts install`; it recreates loaders, views, snapshots, and registry tables, and validates every route predicate in ClickHouse.
3. When the span or log projection changed, run `agent-introspection facts backfill --days 90` so the change applies to everything SigNoz still retains.
4. Never drop `introspection.spans`, `introspection.logs`, or `introspection.session_projects`: history older than 90 days, and all synced hook history, exists only there.
5. Verify in the Pipeline view that parity, recombination, sanitization, and the coverage grid still pass.

## Done when

- The install and any backfill completed without errors.
- The Pipeline view checks pass for a 90-day window.
