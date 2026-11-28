# Findings and fixes

The Findings and fixes view of the dashboard, in its Learning group.

## Sub-features

- Findings that `facts sync` promotes, with their state, from the workflow store.
- Proposals with their state (fix cycle).
- Enforcement-tier audit: whether repeats are addressed at the strongest deterministic tier (fix cycle).
- Post-intervention recurrence, shown only when `[experimental] intervention_outcomes = true` (fix cycle).

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/interventions` in a browser, or pick Findings and fixes under Learning in the navigation. From a terminal, `agent-introspection dashboard view interventions` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-interventions.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server/interventions.test.ts](../../dashboard/server/interventions.test.ts), [dashboard/src/views/Interventions.test.ts](../../dashboard/src/views/Interventions.test.ts).

## Gotchas

- The server reads `[experimental]` once at start: restart the service (`agent-introspection dashboard install`) after turning outcomes on.
- The flag only gates the dashboard. `facts sync` still evaluates due proposals when it is off.
- With outcomes off, the view runs no ClickHouse queries.
- The view reads the workflow store at `INTROSPECTION_WORKFLOW_DB`, not ClickHouse, for findings and proposals.
- Findings start on 2026-09-29, when promotion from the facts began; the retired ledger before that is archived at `/Volumes/UGreen-External/archive/agent-introspection-2026-09-28/`.
