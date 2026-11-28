# History start and coverage figures re-measure

Track: priority: CLI data access / problem and friction surfacing

## Problem

Two docs give different start points for history, and the coverage-gap percentages are dated
measurements that nobody has re-run:

1. `docs/references/facts-store.md` "Retention and backups" says trace history before 2026-09-10 expired.
2. `docs/references/data-gaps.md` "Gaps in history" says raw traces and logs start on 2026-08-24, with
   2026-09-06 to 2026-09-12 missing and 2026-09-10 partial.
3. `data-gaps.md` "Gaps in coverage" gives 93%, 99.7% and 54% of Codex tool calls inside an exported turn
   span, and per-task usage 55% below totals. The 54% figure is repeated in `signal-catalog.md`.
4. `data-gaps.md` says the retired workflow ledger is archived at
   `/Volumes/UGreen-External/archive/agent-introspection-2026-09-28/`. That volume was not mounted
   during the 2026-11 audit, so the path is unverified.

## Evidence

The code cannot settle these; they need read-only ClickHouse queries on the host and a check of the
archive volume.

## Proposed change

1. Query the first day with raw traces and logs, and the missing days, then make both docs say the same.
2. Re-measure the coverage figures over a stated window and date them.
3. Mount the archive volume and confirm the path, or remove the sentence.

## Acceptance check

`facts-store.md` and `data-gaps.md` agree on the history start, and every percentage in "Gaps in
coverage" carries its measurement date.
