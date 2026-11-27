# Recurring failures

The Recurring failures view of the dashboard, in its Learning group.

## Sub-features

- Recurring targets: files that recur across tasks.
- Recurring failure signatures across tasks and days.
- Actionable repeats that cross the evidence threshold for intervention.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/recurrence` in a browser, or pick Recurring failures under Learning in the navigation. From a terminal, `agent-introspection dashboard view recurrence` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-recurrence.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts), [dashboard/src/views/PipelineRecurrence.test.ts](../../dashboard/src/views/PipelineRecurrence.test.ts).

## Gotchas

- Week boundaries are cut at the Europe/London date, which the window-end label shows.
