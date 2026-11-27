# Provider

The Provider view of the dashboard, in its Health group.

## Sub-features

- Model calls, call error rate and unknown outcomes.
- Stream disconnects and retries.
- Model call latency, time to first token and output throughput.
- Model conformance: whether the provider served the requested model.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/provider` in a browser, or pick Provider under Health in the navigation. From a terminal, `agent-introspection dashboard view provider` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-provider.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts), [dashboard/server/partial.test.ts](../../dashboard/server/partial.test.ts).

## Gotchas

- One failing query leaves the rest of the view: the response names the empty dataset in its errors.
