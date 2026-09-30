# Health workflow

## Objective

Establish verified readiness of the facts store without bypassing failed checks.

## Required actions

1. Run `agent-introspection facts status`: every loader and snapshot refresher is `Scheduled` with no exception, and per-harness freshness is current.
2. Run `agent-introspection facts schedule status`: the project sync job is installed and loaded, with no inbox backlog.
3. In the dashboard Pipeline view, read source parity, All = Σ harnesses, sanitization, the coverage grid, and project attribution for the window in question.
4. Stop on a failing loader, a possible break or unexplained stray in the coverage grid, a parity or recombination mismatch, any sanitization violation, or a growing inbox backlog.

## Done when

- Every required check has recorded evidence.
- Failures are surfaced explicitly with the view, harness, and signal they affect.
