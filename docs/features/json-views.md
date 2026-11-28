# Dashboard JSON commands

The `agent-introspection dashboard` commands that read what the dashboard shows as JSON, without a browser.

## Sub-features

- `dashboard views`: each view's signals, the features turned on, the enabled harnesses and the hidden signals.
- `dashboard view <id>`: one view's datasets, errors, route contributions and freshness.
- `dashboard overview`: every view's dataset sizes, errors, missing routes and freshness, plus the first 20 active findings and `findings_total`.
- `dashboard session <harness> <id>`: one session's [drill-down](session.md).

## How to get to it (user POV)

Run the commands on the machine that serves the dashboard. `view` and `overview` take `--days N` (default 7, at most 92), or `--start` and `--end` together, and `--harness` for one enabled harness. `view` and `session` take `--rows N` to keep the first N rows of each dataset; the response then names each cut dataset under `truncated`.

## Driving it with sandbox.sh lever

The lever does not run these commands. It reads the same `/api/view` responses with `curl`; the [install journey](../../tests/journeys/test_install_journey.py) checks they have no errors.

Outcome tests: [tests/test_dashboard_api.py](../../tests/test_dashboard_api.py).

## Gotchas

- `INTROSPECTION_DASHBOARD_URL` overrides the URL, but it must be `http` on `127.0.0.1`.
- When no dashboard answers, the error names `agent-introspection dashboard install --repo <checkout>`.
- `overview` keeps going past a failed view and reports it under `failed`.
