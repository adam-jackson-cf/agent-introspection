# Tool failures

The Tool failures view of the dashboard, in its Behaviour group.

## Sub-features

- Tool calls and the tool failure rate.
- Failure signatures and the tasks affected by failures.
- Repeated calls: identical calls in one task, with attempts, longest consecutive run and failures.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/tools` in a browser, or pick Tool failures under Behaviour in the navigation. From a terminal, `agent-introspection dashboard view tools` prints what the page shows as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox, serves the dashboard on a free port, and saves this view's `/api/view` response as `state/verify/view-tools.json` in the evidence directory. The [install journey](../../tests/journeys/test_install_journey.py) drives it and checks that the response has no errors.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts), [dashboard/server/views.test.ts](../../dashboard/server/views.test.ts).

## Gotchas

- Failure signatures group by the full-line hash, so two failures that share a prefix stay apart.
- Policy-blocked calls (omp status `blocked`) are neither explicit outcomes nor failures.
- Codex CLI tool calls are under-counted by task: only 54% fall inside an exported turn span (93% for app-server, 99.7% for exec; measured 2026-10-01), so tasks affected, repeats, recovery and recurrence undercount Codex CLI.
- omp policy refusals are stored as the outcome `policy_blocked` in `tool_calls`, but no view shows them yet.
