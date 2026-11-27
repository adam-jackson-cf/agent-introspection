# Session drill-down

The drill-down into one session that exemplar tables link to.

## Sub-features

- The session's tasks, model calls and tool calls for one harness.
- A response that belongs to an earlier selection is dropped, so the page never shows another session.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Follow a session link from an exemplar table, or open `http://127.0.0.1:<port>/session?harness=<harness>&session=<id>`. From a terminal, `agent-introspection dashboard session <harness> <id>` prints it as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and serves the dashboard on a free port; the [install journey](../../tests/journeys/test_install_journey.py) drives it. The sandbox sends one synthetic Claude Code hook event (session `fresh-1`), which is the session a drill-down there can open.

Outcome tests: [dashboard/src/app-state.test.ts](../../dashboard/src/app-state.test.ts), [tests/test_dashboard_api.py](../../tests/test_dashboard_api.py).

## Gotchas

- The drill-down needs both the harness and the session ID in its URL; a link without either shows nothing.
