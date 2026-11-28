# Test health view

Track: priority: CLI data access / problem and friction surfacing

## Problem

Nothing shows how agents run tests: how many, of which types (unit, integration, slow seam), how fast,
and whether they match CI. The idea needs a problem statement first. Agent-run tests appear in the
trace data only as tool calls, so CI parity may be out of reach; this is an inference, not checked.

## Evidence

No test-health code exists in `src`, `dashboard/src` or `docs/features`. The nearest data is
`tool_calls.verification_kinds` and `gate_run` (`003_views.sql`), shown on the Verification and
guardrails page.

## Proposed change

Write the problem statement and the questions the view must answer. Then decide whether the existing
verification signals already answer them, or which new signal is needed: volume, coverage, speed, test
type, CI parity.

## Acceptance check

To be defined with the problem statement.
