# Verification and guardrails

The Verification and guardrails view of the dashboard, in its Behaviour group.

## Sub-features

- Tool approval decisions and rejected tool calls.
- Sandbox outcomes and quality-gate bypasses.
- Command churn, verification runs and failures caught later in the task.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/guardrails` in a browser, or pick Verification and guardrails under Behaviour in the navigation. From a terminal, `agent-introspection dashboard view guardrails` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-guardrails.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server/views.test.ts](../../dashboard/server/views.test.ts).

## Gotchas

- Verification queries read only rows that carry the command shape, so commands recorded before the shape existed do not count.
- The command shape, verification kinds and gate-run flag are stored from commit `f563c3d` (2026-10-12), so verification panels leave earlier hook rows out rather than reading them as no verification.
