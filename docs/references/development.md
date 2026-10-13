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

6. After any Python change, reinstall the standalone CLI, because the launchd job and
   every activity hook run that copy: `uv tool install --force --reinstall .`

## Guardrails

`scripts/run-ci-quality-gates.sh` is the single runner for pre-commit and CI. Besides
Ruff, mypy (`src`, `scripts`, `tests` and each `.agents` skill script), pytest with
coverage, oxlint, markdownlint, Prettier and the dashboard checks, it runs the
repository-owned checkers in `scripts/`:

- `check-python-quality.py`, `check-python-scalability.py` and `check-python-fanout.py`
  enforce structure, class shape, subprocess seams and fan-out limits.
- `check-python-suppressions.py` fails when `noqa`, `type: ignore` or Ruff/mypy
  ignore settings grow past `scripts/python-suppression-baseline.json`. Fix the code;
  update the baseline only to record a reviewed removal.
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

## What To Check

- `scripts/run-ci-quality-gates.sh` exits zero.
- Python naming, structure, and lint limits follow the
  [`python-conventions`](../../.agents/skills/python-conventions/SKILL.md) skill.

## Related Docs

- [README](../../README.md): install and first run.
- [Dashboard Companion](dashboard-companion.md): server configuration.
