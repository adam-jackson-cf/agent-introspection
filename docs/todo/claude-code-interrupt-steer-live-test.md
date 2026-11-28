# Claude Code interrupt and steer live test

Track: priority: CLI data access / problem and friction surfacing

## Problem

Claude Code interrupts and steers are inferred from hook timing and have never been tested live. An
interrupt is a submitted prompt with no `Stop` after it. A steer is a prompt submitted while the
interaction runs. A queued prompt may start a new interaction instead of counting as a steer, which would
undercount friction.

## Evidence

- `src/agent_introspection/facts_sql/003_views.sql:260-270`: `claude_steers` counts
  `prompt_submitted` hooks more than 1 second after the interaction starts and before it ends.
- `src/agent_introspection/facts_sql/003_views.sql:287-289`: `interrupted` and `steered`.
- `docs/references/data-gaps.md`, "Gaps from the activity hooks": "Not live-tested yet".

## Proposed change

In a scratch Claude Code session with the activity hooks installed:

1. Interrupt a running turn.
2. Send a prompt while a turn runs.
3. Queue a prompt and let it run after the turn ends.

Record the hook events and the resulting `task_outcomes` rows (counts only). Fix the SQL if a case is
misread, add a fixture test for each case, and remove the data-gaps row once it passes.

## Acceptance check

Each of the three cases produces the expected `interrupted` and `steered` values, pinned by a test.
