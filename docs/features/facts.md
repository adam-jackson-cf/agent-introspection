# Facts store and sync

The `agent-introspection facts` commands that build the facts store in SigNoz's ClickHouse and keep it current.

## Sub-features

- `install`, `backfill [--days N]` (default 90) and `status`.
- `verify` builds the store in a scratch database to check every statement; `verify --compare` checks each live snapshot against a full read of its view.
- `refresh <name>` refreshes one loader or snapshot now; `findings` refreshes findings; `classify` labels exported prompts now.
- `sync` sends the spool, refreshes the hook-events loader, labels prompts, refreshes findings and evaluates due proposals, in that order.
- `schedule install|remove|status` runs `facts sync --log` every minute (launchd or a systemd user timer).

## How to get to it (user POV)

`setup` runs `install`, `backfill` and `schedule install`. Run the others by hand on the SigNoz host. `sync --log` writes its summary and errors to the rotated sync log instead of stdout.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and runs the [install journey](../../tests/journeys/test_install_journey.py) steps. Each step writes its output to `state/verify/NN-<step>.log` in the evidence directory. The `facts-verify` and `facts-sync` steps run those commands, and the lever checks that a hook record reached the facts store. The `schedule-remove` step removes the schedule.

Outcome tests: [tests/test_facts.py](../../tests/test_facts.py), [tests/test_cli_sync.py](../../tests/test_cli_sync.py), [tests/test_facts_schedule.py](../../tests/test_facts_schedule.py).

## Gotchas

- `verify` exits 30 when it does not pass.
- `classify` and the labelling step in `sync` are off without `[labels] openrouter_key_command`.
- SigNoz raw traces and logs start on 2026-08-24. Data for 2026-09-06 to 2026-09-12 is missing (2026-09-10 partial), and nothing was exported on 2026-09-26 and 2026-09-27; none of it can be recovered.
