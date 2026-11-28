# Harness management

The `agent-introspection harness` commands that list, configure and preview producer harnesses.

## Sub-features

- `list`: every harness profile, whether it is enabled, its binary and the installed version (`--no-version` runs no binary).
- `configure <key>`: writes the harness's export settings and installs its activity hooks. `--prompt-export` also writes the opt-in prompt-export settings.
- `remove-hooks <key>`: takes the activity hooks out and keeps the export settings.
- `impact <profile>`: previews the signals that enabling a producer would hide.

## How to get to it (user POV)

`setup` runs `configure` for each enabled harness. Run the commands by hand to add a harness later or to undo its hooks. `configure` and `remove-hooks` take `--dry-run` and `--root PATH` for an extra config directory, such as another Codex home.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and runs the [install journey](../../tests/journeys/test_install_journey.py) steps. Each step writes its output to `state/verify/NN-<step>.log` in the evidence directory. After `setup`, the lever checks that Claude Code hooks and Codex export are configured. The `remove-hooks` step checks that the hooks are gone and the export settings stay.

Outcome tests: [tests/test_harness_config.py](../../tests/test_harness_config.py).

## Gotchas

- `configure` changes only the profile's managed keys and this tool's own hook entries, backs up each changed file beside it, and changes nothing on a second run.
- A TOML table that cannot be edited line by line is rewritten as one table of inline values, and its comments move above their keys.
- `configure` keeps a setting that already reaches the collector over the other transport unless given `--force`.
