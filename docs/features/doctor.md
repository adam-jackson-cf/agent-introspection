# Doctor

The `agent-introspection doctor` command: read-only checks of every part of the pipeline on this machine.

## Sub-features

- Checks the config, SigNoz, the collector, each enabled harness, the spool, the schedule, prompt labels, the dashboard and the dashboard service.
- `--live` also posts an empty OTLP/HTTP logs request to the hooks endpoint, which stores nothing.
- `--drift` also compares each enabled producer's telemetry with its saved fingerprint (see [producer discovery](producers.md)).

## How to get to it (user POV)

Run `agent-introspection doctor`. Each check is `pass`, `warn`, `fail`, `off` or `info`. The JSON report goes to stdout; every check that is not a pass, with its fix, goes to stderr. `setup` runs doctor as its last step.

## Driving it with sandbox.sh lever

`bash scripts/fresh-install/sandbox.sh lever --data-dir DIR --evidence-dir DIR` installs the committed `HEAD` in a sandbox and runs the [install journey](../../tests/journeys/test_install_journey.py) steps. Each step writes its output to `state/verify/NN-<step>.log` in the evidence directory. The `doctor` step runs `agent-introspection doctor` and checks that no check fails.

Outcome tests: [tests/test_doctor.py](../../tests/test_doctor.py), [tests/test_doctor_harness.py](../../tests/test_doctor_harness.py).

## Gotchas

- The command exits 30 when any check fails; a `warn` does not fail it.
- On an additional machine, the SigNoz, prompt-label and dashboard checks report that they do not run there.
- A config that does not load fails the config check, and the other checks use the defaults.
