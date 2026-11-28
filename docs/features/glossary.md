# Glossary

The page that explains every signal the dashboard shows, from the signal registry.

## Sub-features

- Each view's signals with their title, question and formula.
- A per-harness table for each signal: alignment, note and capture route. System-wide signals say so instead.
- A "Not shown" section for signals no producer emits, with the reason.

## How to get to it (user POV)

The dashboard listens on `127.0.0.1` at `[dashboard] port` in the agent-introspection config (default 4173). Open `http://127.0.0.1:<port>/glossary`. From a terminal, `agent-introspection dashboard views` prints each view's signals (title, question, unit), the features turned on, the enabled harnesses and the hidden signals as JSON.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and serves the dashboard on a free port; the [install journey](../../tests/journeys/test_install_journey.py) drives it, and the registry it loads is the one this page lists.

Outcome tests: [dashboard/server.test.ts](../../dashboard/server.test.ts), [dashboard/server/features.test.ts](../../dashboard/server/features.test.ts), [tests/test_dashboard_api.py](../../tests/test_dashboard_api.py).

## Gotchas

- A signal behind an experimental feature that is off leaves the registry, so the Glossary and `agent-introspection dashboard views` omit it.
- `dashboard views` drops signals hidden on this machine from each view and lists them under `hidden`. The Glossary still lists them; the [Pipeline](pipeline.md) view names them.
