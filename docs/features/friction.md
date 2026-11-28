# Friction

The Friction view of the dashboard, in its Behaviour group.

## Sub-features

- Interrupted, steered and errored tasks.
- Quick follow-ups and clean completions.
- Recovery after a failed tool call.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/friction` in a browser, or pick Friction under Behaviour in the navigation. From a terminal, `agent-introspection dashboard view friction` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-friction.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts), [dashboard/src/app-state.test.ts](../../dashboard/src/app-state.test.ts).

## Gotchas

- Headless Codex exec has no user to interrupt or follow up, so its cells show the registry's reason instead of zero.
