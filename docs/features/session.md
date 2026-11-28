# Session drill-down

The drill-down into one session that exemplar tables link to.

## Sub-features

- Tasks, with their task type and correction kind when labelled.
- Token usage by model and effort.
- Tool calls (first 500) and model-call outcomes.
- User signals (first 500) and head counts of tasks, calls, failed calls and signals.
- A response that belongs to an earlier selection is dropped, so the page never shows another session.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Follow a session link from an exemplar table, or open `http://127.0.0.1:<port>/session?harness=<harness>&session=<id>`. From a terminal, `agent-introspection dashboard session <harness> <id>` prints it as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and serves the dashboard on a free port; the [install journey](../../tests/journeys/test_install_journey.py) drives it. The lever never calls `/api/session`, so it does not prove this page.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts), [dashboard/src/app-state.test.ts](../../dashboard/src/app-state.test.ts).

## Gotchas

- The drill-down needs both the harness and the session ID in its URL; a link without either shows nothing.
- `/api/session` refuses a harness this machine does not enable and a session ID over 256 characters.
