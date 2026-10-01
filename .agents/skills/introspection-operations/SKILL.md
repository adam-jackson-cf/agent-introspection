---
name: "introspection-operations"
description: "Use when checking health (preflight, loaders, freshness, parity, sanitization, coverage grid, inbox, prompt labels), or changing a projection, fact view, or registry entry: adding, changing, closing (including with a new hook event), or excluding a dashboard signal, then reinstalling or backfilling. Checks and changes the Agent Introspection facts store safely."
---

# Operations

Installing and harness setup are `introspection-onboarding`; findings, proposals, and
evaluations are `introspection-improvement`.

## Workflows

- [Health](references/health-workflow.md): before any change, and before trusting
  findings. Never weaken or bypass a failed check.
- [Change](references/change-workflow.md): edit the projections, views, or registry
  under the harness parity rule, apply them (render docs, gates, reinstall, install,
  backfill), and verify.

## Output

- The checks or changes made, with evidence (command results, Pipeline view state).
- Each failed check with the view, harness, and signal it affects.
- Registry changes with the parity matrix counts, and whether install or backfill ran.
