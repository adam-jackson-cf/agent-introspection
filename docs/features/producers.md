# Producer discovery

The commands that find SigNoz and explore the services that send it telemetry, used to onboard a new producer.

## Sub-features

- `signoz detect`: finds SigNoz and ClickHouse containers and the local ports 4317, 4318, 8123 and 8080.
- `producers discover`: lists every service in SigNoz over the window, classed.
- `producers profile <service>`: lists one service's span, event and key names with counts, and drafts a candidate profile (`--write PATH` saves it as TOML).
- `producers fingerprint [service...]`: records each service's names with counts (`--save` keeps them for `doctor --drift`).

## How to get to it (user POV)

Run the commands on the SigNoz host. `discover`, `profile` and `fingerprint` take `--days N`. `fingerprint` defaults to the enabled harnesses and saves to `~/.local/share/agent-introspection/fingerprints/`.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and runs the [install journey](../../tests/journeys/test_install_journey.py) steps. Each step writes its output to `state/verify/NN-<step>.log` in the evidence directory. The `signoz-detect` step runs `agent-introspection signoz detect` and accepts any exit code. The lever does not run the `producers` commands.

Outcome tests: [tests/test_signoz_detect.py](../../tests/test_signoz_detect.py), [tests/test_cli_producers.py](../../tests/test_cli_producers.py).

## Gotchas

- `signoz detect` never changes SigNoz, and the `producers` commands are read-only unless given `--write` or `--save`.
- `profile --write` refuses to replace an existing file without `--force`.
