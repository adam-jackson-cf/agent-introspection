# Development

Use this guide to run the same checks as pre-commit and CI before calling a change done.

## Purpose

The Python package and the independent dashboard package share one quality suite,
`scripts/run-ci-quality-gates.sh`. Pre-commit runs it with `--fix --stage` (auto-fix and
re-stage); CI runs it check-only.

## Preconditions

- Python 3.12 or newer and `uv`
- Bun, for the dashboard package

## Steps

1. Run the Python checks individually:

   ```sh
   uv sync
   uv run ruff check --no-fix .
   uv run ruff format --check .
   uv run python scripts/check-python-mypy.py
   uv run pytest
   ```

2. Install the Git pre-commit trigger and run the complete suite:

   ```sh
   uv run pre-commit install
   uv run pre-commit run --all-files
   ```

3. Or run the suite directly, including the dashboard package:

   ```sh
   bash scripts/run-ci-quality-gates.sh
   ```

4. The dashboard companion is an independent Bun package in `dashboard/`. Its package
   commands, all included in the suite except `start`, are:

   ```sh
   bun install --cwd dashboard --frozen-lockfile
   bun run --cwd dashboard check
   bun run --cwd dashboard test
   bun run --cwd dashboard lint
   bun run --cwd dashboard format:check
   bun run --cwd dashboard build
   bun run --cwd dashboard start
   ```

5. Auto-fix is opt-in for manual runs:
   `bash scripts/run-ci-quality-gates.sh --fix` formats and fixes, and
   `--fix --stage` also re-stages the files it changed. The pre-commit hook runs
   `--fix --stage`, so a commit auto-fixes and re-stages lint changes; it refuses to
   stage over unstaged changes. CI runs the check-only suite.

6. After any Python change, reinstall the standalone CLI, because the scheduled sync
   (a launchd agent on macOS, a systemd user timer on Linux) and every activity hook
   run that copy: `uv tool install --force --reinstall .`

## Guardrails

`scripts/run-ci-quality-gates.sh` is the single runner for pre-commit and CI. Besides
Ruff, mypy (`src`, `scripts`, `tests` and each `.agents` skill script), pytest with
coverage, oxlint, markdownlint, Prettier and the dashboard checks, it runs the
repository-owned checkers in `scripts/`:

- `check-python-quality.py`, `check-python-scalability.py` and `check-python-fanout.py`
  enforce structure, class shape, subprocess seams and fan-out limits.
- `check-python-suppressions.py` fails on every `noqa`, `type: ignore` or Ruff/mypy
  ignore setting, and on a Ruff exclude that hides a maintained file. Fix the code
  instead of suppressing the finding.
- `check-coverage-slice.py`, `check-lock-parity.py`, `check-skill-metadata.py`,
  `check-test-subprocess-timeouts.py` and `check-typescript-limits.ts` cover the
  remaining rules.
- `validate-rule-coverage.py` checks `docs/guardrails/coverage-ledger.json` against
  the rule catalog `docs/guardrails/enaible-rules.json`. Locally it validates the
  ledger structure only; under `CI=true` it also runs every rule canary
  (about 40 seconds).
- `CI=true` additionally runs `check-runner-failure-propagation.py` (proves a
  failing step fails the runner) and `check-dependency-audit.py` (needs network).
  GitHub Actions sets `CI=true`; export it to reproduce the CI run locally.

Script layout: command scripts directly under `scripts/` use kebab-case names, and
importable helper modules live in `scripts/lib/` with snake-case names. Flat
`tests/test_*.py` files count as the `unit` test group. Canary fixtures under
`tests/fixtures/guardrails/` deliberately violate rules and are excluded from every
linter and from pytest collection. To add or change a rule, update its checker, its
fixtures and its ledger row together, then run the full validator:

```sh
uv run python scripts/validate-rule-coverage.py --root . \
  --catalog docs/guardrails/enaible-rules.json \
  --ledger docs/guardrails/coverage-ledger.json
```

Test tiers: untagged tests are T1 and `pytest.mark.boundary` files are T2. Files marked
`pytest.mark.live`, and dashboard tests in files named `*.live.ts`, are T3: they need
the local SigNoz ClickHouse, so neither the hook nor CI runs them. Run them by hand with
`bash scripts/run-ci-quality-gates.sh t3` on a SigNoz host, or start the Live tests
workflow (`.github/workflows/live-tests.yml`) on a self-hosted runner labelled `signoz`.

## What To Check

- `scripts/run-ci-quality-gates.sh` exits zero.
- Python naming, structure, and lint limits follow the
  [`python-conventions`](../../.agents/skills/python-conventions/SKILL.md) skill.

## Design notes

The notes below record why parts of this area work the way they do. They came from code comments, which the repository no longer allows.

- Pytest treats `ResourceWarning` and `PytestUnraisableExceptionWarning` as errors. A leaked SQLite connection surfaces only as an unraisable `ResourceWarning` at garbage collection.
- `check-python-mypy.py` fails when a maintained Python file, including an untracked one, is outside `[tool.mypy]` targets, so a new package cannot dodge coverage before it is committed. Skill scripts under `.agents/skills/*/scripts` share module basenames and hold non-importable names, so mypy checks each one in its own run.
- `scripts/render-measures.py` writes the registry-generated doc sections and `dashboard/src/harnesses.generated.ts`, so the dashboard never copies harness keys, labels, or services by hand. The renderers pad tables and lay out code as Prettier would (print width 80), so a regenerated file passes the format check. `--check` exits non-zero when a committed file differs from the registry.
- `scripts/ux-probe/probe.ts` reads link text from navigation links only. Per-row table links are row identifiers, and the probe never prints them, so no session ID reaches the evidence. The probe uses the regexes in `docs/ux/rubrics/ux-page.yaml` verbatim, so edit them as code.
- Markdownlint skips line length under `docs/ux/feature-audit/` because each audit row holds a full formation chain.
- `tests/conftest.py` points `HOME` at an empty directory for every test, so no test touches the real home. It also puts an `omp` stand-in on `PATH`, so no test runs the real omp.
- `install-signoz.sh` must run under macOS's system bash 3.2. `tests/test_signoz_install_script.py` keeps only system directories on `PATH`, so macOS runs it under that bash.
- `tests/test_command_shape_parity.py` selects over inline `values()` rows, so it writes nothing to the ClickHouse it runs against.
- The fresh-install stub collector (`scripts/fresh-install/otlp_stub.py`) accepts exported traces and metrics but stores only log records, so the sandbox cannot show them. It counts every request by path in `state/collector.json`.
- `scripts/fresh-install/sandbox.sh` sets each `uv` and `bun` cache variable only when it finds the cache. An empty bun cache variable makes bun cache into the working directory.
- The sandbox runs the stub collector on `uv`'s own Python. A Homebrew Python started over SSH accepted no loopback connections on macOS, so the collector looked down.
- `sandbox.sh destroy` deletes only a directory that `create` recorded as its own sandbox.

## Related Docs

- [README](../../README.md): install and first run.
- [Dashboard Companion](dashboard-companion.md): server configuration.
