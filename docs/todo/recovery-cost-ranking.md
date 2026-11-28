# Recovery-cost ranking of findings

Track: priority: CLI data access / problem and friction surfacing

## Problem

Findings are ranked by `impact` alone. A failure the agent recovers from on the next call ranks as high
as one that derails the task, so self-recovering failures reach the top of the list. This was spike SP3
of the 2026-11 ambition review and has not been run.

## Evidence

- `src/agent_introspection/candidates.py:33-35`: `ORDER BY` the subject's `impact`, then `LIMIT 1`.
- `src/agent_introspection/evidence.py:47-64`: the evidence pack already computes each failure's `next_step`, but ranking does not read it.

## Proposed change

Rank findings by recovery cost: the tokens and wall time between a failure and the next successful call
in the same task, from `tool_calls.duration_ms` and model-call tokens. Compare the new ranking with the
`impact` ranking on live findings before switching.

## Acceptance check

At least 2 of the top 5 findings change, and the user prefers the new list in a blind comparison.
