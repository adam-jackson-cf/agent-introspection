---
name: "introspection-signals"
description: "Use when changing a projection, fact view, or registry entry: adding, changing, closing (including with a new hook event), or excluding a dashboard signal, or adding a harness, then reinstalling or backfilling. Changes the Agent Introspection facts store safely under the harness parity rule."
---

# Signals

Checking health first is `introspection-health`; installing, harness setup, and
producer onboarding are `introspection-onboarding`.

## Workflow

[Change](references/change-workflow.md): edit the projections, views, or registry
under the harness parity rule, apply them (render docs, gates, reinstall, install,
backfill), and verify. Adding a harness starts from the onboarding skill's producer
onboarding workflow.

## Output

- The changes made, with evidence (command results, Pipeline view state).
- Registry changes with the parity matrix counts, and whether install or backfill ran.
