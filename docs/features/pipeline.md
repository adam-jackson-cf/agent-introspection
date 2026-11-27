# Pipeline

The Pipeline view of the dashboard, in its Health group.

## Sub-features

- Loader refresh: whether each refreshable loader and snapshot runs without errors.
- Fact freshness: how far each harness's facts trail SigNoz.
- Source parity and All = Σ harnesses: usage_events against a direct recount, and per-harness values against the All value.
- Sanitization: whether any raw prompt, command, argument, output or identity text is stored.
- Coverage grid (`/pipeline/coverage`): whether each harness emits what the registry says it does.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/pipeline` in a browser, or pick Pipeline under Health in the navigation. From a terminal, `agent-introspection dashboard view pipeline` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-pipeline.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server/pipeline.test.ts](../../dashboard/server/pipeline.test.ts), [dashboard/server/partial.test.ts](../../dashboard/server/partial.test.ts).

## Gotchas

- The coverage grid shows harnesses this machine does not enable as not in use, and flags them when their routes still have rows.
- A route with no rows while its harness is active shows as route missing, a possible break; an idle harness shows no activity.
