# Setup

The `agent-introspection setup` command that installs Agent Introspection on this machine in one run.

## Sub-features

- Runs its steps in order: config, signoz, facts (install and backfill), harnesses (export settings and activity hooks), schedule, dashboard, then doctor.
- `--dry-run` reports each step and changes nothing.
- Options set config keys, such as `--clickhouse-url`, `--otlp-endpoint`, `--harness` (repeatable; default claude-code and codex), `--prompt-export`, `--openrouter-key-command`, `--backfill-days` (default 90) and `--dashboard-port`.

## How to get to it (user POV)

Run `agent-introspection setup --dry-run`, then `agent-introspection setup`, from a checkout (or pass `--repo`). It prints each step's start and end on stderr, then a summary of changed files, doctor's result and next steps. The JSON report goes to stdout.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and runs the [install journey](../../tests/journeys/test_install_journey.py) steps. Each step writes its output to `state/verify/NN-<step>.log` in the evidence directory. The `setup-dry-run` step checks that no config or hooks were written. The `setup` step checks that every step ran, and `setup-rerun` runs it again.

Outcome tests: [tests/test_setup_flow.py](../../tests/test_setup_flow.py).

## Gotchas

- Setup never installs SigNoz; the signoz step fails when it is not reachable.
- It stops at the first failing step and names it in `stopped_at`. The command exits 30 when a step or doctor fails.
- Every step is idempotent, so a rerun is a reinstall.
- A ClickHouse password command skips the dashboard step instead of failing it.
