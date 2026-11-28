---
name: "introspection-health"
description: "Use when checking health (doctor, preflight, loaders, freshness, parity, sanitization, coverage grid, inbox, prompt labels, producer drift), before changing the facts store, or before trusting findings or dashboard figures. Checks the Agent Introspection pipeline without weakening or bypassing a failed check."
---

# Health

Installing, harness setup, and producer onboarding are `introspection-onboarding`;
changing a projection, fact view, or signal is `introspection-signals`; reading the
dashboard is `introspection-analysis`; findings, proposals, and evaluations are
`introspection-improvement`.

## Workflow

[Health](references/health-workflow.md): before any change, and before trusting
findings; starts with `agent-introspection doctor`. Never weaken or bypass a failed
check.

## Output

- The checks made, with evidence (command results, Pipeline view state).
- Each failed check with the view, harness, and signal it affects.
