# Development

Use this guide to run the same checks as pre-commit and CI before calling a change done.

## Purpose

The Python package and the independent dashboard package share one check-only quality
suite, `scripts/run-ci-quality-gates.sh`, used by both pre-commit and CI.

## Preconditions

- Python 3.12 or newer and `uv`
- Bun, for the dashboard package

## Steps

1. Run the Python checks individually:

   ```sh
   uv sync
   uv run ruff check .
   uv run ruff format --check .
   uv run mypy src
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

5. After any Python change, reinstall the standalone CLI, because the launchd job and
   every activity hook run that copy: `uv tool install --force --reinstall .`

## What To Check

- `scripts/run-ci-quality-gates.sh` exits zero.
- Python naming, structure, and lint limits follow the
  [`python-conventions`](../../.agents/skills/python-conventions/SKILL.md) skill.

## Related Docs

- [README](../../README.md): install and first run.
- [Dashboard Companion](dashboard-companion.md): server configuration.
