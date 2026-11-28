# Requests and corrections

The Requests and corrections view of the dashboard, in its Learning group.

## Sub-features

- Labelled tasks and task types.
- Corrections by the next prompt, correction kinds and frustrated follow-ups.
- Effort payoff by task type.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/intent` in a browser, or pick Requests and corrections under Learning in the navigation. From a terminal, `agent-introspection dashboard view intent` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-intent.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts).

## Gotchas

- Prompt labels need `[labels] openrouter_key_command`; without it no task is labelled and the view stays empty.
