# Reasoning effort

The Reasoning effort view of the dashboard, in its Health group.

## Sub-features

- Reasoning tokens and the reasoning share of output.
- Requested effort and the heavy-effort task share.
- Tasks, task duration, model steps, reasoning tokens and delegations per task.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/effort` in a browser, or pick Reasoning effort under Health in the navigation. From a terminal, `agent-introspection dashboard view effort` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-effort.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts).

## Gotchas

- Claude Code reasoning is summed per turn by the `Stop` hook. Tasks from before the activity hooks were installed show no value, not zero.
- Codex CLI TUI turns often omit `codex.turn.token_usage.*`, so per-task reasoning and output run about 55% below the usage totals (measured over 30 days to 2026-10-01).
