# omp policy refusals view

Track: priority: CLI data access / problem and friction surfacing

## Problem

omp tool calls that jev-guard refuses are stored as the `policy_blocked` outcome and kept out of tool
failure rates, but no dashboard view or CLI command shows them. A user cannot see how often policy
blocks the agent, or which rules fire, although a refusal is friction.

## Evidence

- `src/agent_introspection/facts_sql/003_views.sql:540`: `'blocked'` becomes `'policy_blocked'`.
- `dashboard/server/views.test.ts:21`: asserts that no view SQL contains `policy_blocked`.
- `src/agent_introspection/facts_sql/signal_support.toml` `tools.failure_rate` omp note: the outcome
  stays out of the denominator.
- Policy and rule appear only in the status text (`Jev guard blocked … (<rule>)`).

## Proposed change

Add a policy-refusals panel to the Verification and guardrails page: refusals per day and per tool for
omp, and the share of omp tool calls refused. Register a `guard.policy_blocked` signal with omp aligned
and the other harnesses `not applicable`. Replace the `views.test.ts` assertion with one that pins where
`policy_blocked` may appear. Policy and rule names need the `jev.decision` span from
[Jev decision models page](jev-decision-models-page.md).

## Acceptance check

With omp enabled, the panel shows the same refusal count as
`SELECT count() FROM introspection.tool_calls_snapshot WHERE outcome = 'policy_blocked'` over the window,
and the page stays visible with Codex and Claude Code enabled.
