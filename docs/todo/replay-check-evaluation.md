# Replay check instead of a 14-day evaluation

Track: deprioritised: improvement-loop fix cycle

## Problem

An applied fix is evaluated over a 14-day window, so feedback on an environment-class fix takes two weeks.
This was spike SP2 of the 2026-11 ambition review and has not been run.

## Evidence

The proposal evaluation runs in `facts sync` through `evaluation.evaluate_due`
(`src/agent_introspection/cli_facts.py`). A candidate finding for the spike was
`zsh: can't create temp file for here document: operation not permitted` (`codex_exec`, 15 tasks in
2026-11). Re-check it is still actionable before starting.

## Proposed change

Reproduce an environment-class failure under `codex exec` in the same sandbox, apply the fix, and rerun
the reproduction, all within 30 minutes. If it works, offer the replay as an evaluation route for
environment-class findings.

## Acceptance check

The replay fails before the fix and passes after it, within 30 minutes.
