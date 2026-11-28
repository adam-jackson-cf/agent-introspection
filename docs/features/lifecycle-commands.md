# Dashboard service

The `agent-introspection dashboard install|status|remove` commands that run the dashboard as a user service.

## Sub-features

- `install`: builds the dashboard in a checkout (`bun install --frozen-lockfile`, `bun run build`) and (re)starts its service on `127.0.0.1` at `[dashboard] port` (default 4173).
- `status`: whether the service file is installed and the service is loaded.
- `remove`: stops the service and deletes its file.

## How to get to it (user POV)

Run `agent-introspection dashboard install --repo <checkout>` on the SigNoz host. macOS uses a launchd agent (`dev.agent-introspection.dashboard`); Linux uses a systemd user unit (`agent-introspection-dashboard`). The service logs to `dashboard.log` beside the sync log. `setup` runs `install` unless given `--no-dashboard`.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and runs the [install journey](../../tests/journeys/test_install_journey.py) steps. Each step writes its output to `state/verify/NN-<step>.log` in the evidence directory. After `setup`, the lever checks that the service started and answers on its port. The `dashboard-remove` step runs `agent-introspection dashboard remove`.

Outcome tests: [tests/test_dashboard_service.py](../../tests/test_dashboard_service.py).

## Gotchas

- The service reads `[experimental]` once at start: run `install` again after changing it.
- `install` refuses on an additional machine and when ClickHouse needs a password command. Run the dashboard by hand then (`bun run --cwd dashboard start`) with `INTROSPECTION_CLICKHOUSE_USER` and `INTROSPECTION_CLICKHOUSE_PASSWORD` set.
- `install` writes the workflow store path (`INTROSPECTION_WORKFLOW_DB`) and the config path into the service. Run `install` again after changing either.
