# Activity hooks and spool

The activity hooks that record what harness telemetry lacks, and the spool that ships those records to the collector.

## Sub-features

- `agent-introspection hook <producer> <event>` turns one native hook envelope from stdin into records for Claude Code or omp.
- Records wait as files in the spool (`~/.local/share/agent-introspection/hook-inbox`).
- `spool sync` sends the spool and a status record to the OTLP route; `spool status` counts pending and rejected files without changing them.

## How to get to it (user POV)

`setup` and `harness configure` install the hooks, so the harness runs them on its own. Each hook run also sends one batch of the spool. `facts sync` sends the rest every minute on the SigNoz host. Run `agent-introspection spool status` to see the backlog.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and runs the [install journey](../../tests/journeys/test_install_journey.py) steps. Each step writes its output to `state/verify/NN-<step>.log` in the evidence directory. The `hook-event` step pipes one Claude Code `PreToolUse` envelope (session `fresh-1`) to the installed shim. After `facts-sync`, the lever checks that the record reached the facts store.

Outcome tests: [tests/test_hooks.py](../../tests/test_hooks.py), [tests/test_cli_spool.py](../../tests/test_cli_spool.py), [tests/test_spool.py](../../tests/test_spool.py).

## Gotchas

- A hook always exits 0, so it never blocks the harness. It logs a failure's error class to `~/.local/share/agent-introspection/hooks.log` instead.
- Signals from the hooks have no value for activity before the hooks were installed.
- Hook events exist from 2026-09-30 13:40 UTC. omp sessions started before the extension was registered, sessions running during the 2026-10-12 10:14 plugin migration, and headless runs without the plugin have no hook rows, so hook-routed signals are empty there, never 0.
