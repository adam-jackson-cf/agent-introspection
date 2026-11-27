# Cache efficiency

The Cache efficiency view of the dashboard, in its Health group.

## Sub-features

- Input, cached input, uncached input, cache creation and output tokens.
- Input cache utilization: the share of input served from cache.
- Completed operations and usage-active sessions.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/cache` in a browser, or pick Cache efficiency under Health in the navigation. From a terminal, `agent-introspection dashboard view cache` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-cache.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts).

## Gotchas

- All is the union of the enabled harnesses' rows, and ratios are aggregated before division, so All is never an average of per-harness ratios.
