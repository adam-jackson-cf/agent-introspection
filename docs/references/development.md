# Development

Use this guide to run the same checks as the commit hook and CI before calling a change done.

## Purpose

The Python package, the independent dashboard package, the scripts and the docs share one
check-only entrypoint, `scripts/run-ci-quality-gates.sh`. The commit hook and every CI job
run it, and it never changes a file. Fixing is a separate, explicit command:
`bash scripts/fix-quality-gates.sh`. [`proofs.toml`](../../proofs.toml) declares which
control enforces each rule, where it runs, and the canaries that prove it.

## Preconditions

- Python 3.12 or newer and `uv`.
- Bun at the version pinned by `packageManager` in `dashboard/package.json` and
  `scripts/ux-probe/package.json` (`bun@1.3.0`). `mise.toml` pins the same version, and
  `check lock_parity` fails when they differ; CI reads the pin with `bun-version-file`.
  Activate mise in your shell (`eval "$(mise activate zsh)"`, or `bash`), then run
  `mise trust` and `mise install` in the checkout, so `bun` resolves to 1.3.0 in every terminal that
  commits. `bun --version` must print `1.3.0`. The T1 time budget selects its
  `[[test_budget.machine]]` entry by a machine id that includes the `bun` on `PATH`,
  and the only approved entry names `bun1.3.0`; another Bun gives an unknown machine,
  whose run reports timing but enforces no limit. Check the id with
  `uv run python scripts/check-test-budget.py --print-machine-id`.
- Docker, only for the `install` mode, and a SigNoz host, only for the `t3` mode.
- `origin/main` fetched, for the checks that compare the branch with its merge base.

## Steps

1. Install the commit hook. Every gate mode also runs this as part of its setup step:

   ```sh
   uv sync --locked --dev
   uv run pre-commit install
   ```

   Every commit then runs `bash scripts/run-ci-quality-gates.sh hook`. Never bypass it
   with `--no-verify`; fix the failure instead.

2. Run the mode you need. Each mode except `check` and `install` first syncs the locked
   Python and Bun dependencies and installs the hook:

   | Command                                                        | Runs                                                                    |
   | -------------------------------------------------------------- | ----------------------------------------------------------------------- |
   | `bash scripts/run-ci-quality-gates.sh static`                  | Every static check in order; stops at the first failure                 |
   | `bash scripts/run-ci-quality-gates.sh t1`                      | T1 pytest with the coverage floor, then the dashboard `bun test`        |
   | `bash scripts/run-ci-quality-gates.sh t2`                      | T2 pytest                                                               |
   | `bash scripts/run-ci-quality-gates.sh t3`                      | T3 pytest and `*.live.ts` against a local SigNoz ClickHouse             |
   | `bash scripts/run-ci-quality-gates.sh hook`                    | `static`, `t1` (then the T1 time budget) and `t2`, judging staged paths |
   | `bash scripts/run-ci-quality-gates.sh install`                 | The lever twice (`check lever_isolation`), then the journey tier        |
   | `bash scripts/run-ci-quality-gates.sh proofs`                  | The `proofs.toml` validator with every canary                           |
   | `bash scripts/run-ci-quality-gates.sh check <name> [path ...]` | One check, over the given paths or its whole-repository scope           |

   `check` names are the `check_<name>` functions in the entrypoint, for example
   `check ruff src/agent_introspection/facts.py` or `check structure --staged`.

3. Fix what a tool can fix, then run the check again:

   ```sh
   bash scripts/fix-quality-gates.sh
   ```

   It runs `ruff format`, `ruff check --fix` over the Python scope and
   `bun run --cwd dashboard format`. Ruff's own config keeps `fix = false`, so no check
   fixes anything. Other fixers run by hand on the file the check names:
   `uv run shfmt -w <file>` and
   `bun dashboard/node_modules/.bin/oxlint --config oxlint.config.ts --fix <file>`.

4. A local `static` run compares test growth, the coverage ratchet and identity artefacts
   with `HEAD`. To check a branch as CI does, compare with the default branch:

   ```sh
   PROOFS_BASE_REF=origin/main bash scripts/run-ci-quality-gates.sh static
   ```

   `structure`, `test_placement` and `test_evasion` always judge the branch against
   `origin/main` (the hook judges staged paths). A clone without `origin/main` fails
   them with a `git merge-base` error rather than skipping them.

5. The dashboard companion is an independent Bun package in `dashboard/`. The gate covers
   every command below except `start`:

   ```sh
   bun install --cwd dashboard --frozen-lockfile
   bun run --cwd dashboard test
   bun run --cwd dashboard format:check
   bun run --cwd dashboard build
   bun run --cwd dashboard start
   ```

   The gate runs oxlint, Prettier and `tsc` from `dashboard/node_modules`, pinned in
   `dashboard/package.json` and locked in `dashboard/bun.lock`. `tsc` checks every tracked
   TypeScript file through the root `tsconfig.json`, which `dashboard/tsconfig.json`
   extends.

6. After any Python change, reinstall the standalone CLI, because the scheduled sync
   (a launchd agent on macOS, a systemd user timer on Linux) and every activity hook
   run that copy: `uv tool install --force --reinstall .`

## Test tiers

The tier selectors live once, in `scripts/run-ci-quality-gates.sh`.

| Tier    | Selector                                                              | What belongs there                                                                                    |
| ------- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| T1      | `not boundary and not deferred and not live`                          | Untagged tests: no process, socket or loopback call; each test gets its own temporary `HOME`          |
| T2      | `(boundary or deferred) and not (boundary and deferred) and not live` | Files with a module-level `pytestmark = pytest.mark.boundary`, which drive a process, build or socket |
| journey | `boundary and deferred and not live`                                  | `tests/journeys/`: drive the product through the lever; only the `install` mode runs them             |
| T3      | `live`                                                                | Tests against a local SigNoz ClickHouse; only the `t3` mode runs them                                 |

- One file holds one tier. `deferred` marks a test over its tier's time limit that runs
  with T2 under an approved exception.
- T3 also holds the dashboard tests in files named `*.live.ts`, which `bun test` discovery
  skips. Neither the hook nor CI runs T3. Run it with
  `bash scripts/run-ci-quality-gates.sh t3` on a SigNoz host, or start the Live tests
  workflow (`.github/workflows/live-tests.yml`), which runs that mode by hand on a
  self-hosted runner labelled `signoz`; its status is never a required merge status.
- The dashboard's `bun test` is T1. `dashboard/bunfig.toml` preloads `t1-guard.ts`, so a
  test that calls `Bun.spawn`, `fetch`, `Bun.serve` or Node `child_process` or `net` fails.
- The T1 coverage floor is `fail_under` in `pyproject.toml` `[tool.coverage.report]`,
  enforced by pytest-cov on T1 only.
- The T1 time budget blocks only the commit: `hook` mode runs `check-test-budget.py`
  after T1 on its run reports, and CI never measures timing. The machine's
  `[[test_budget.machine]]` entry sets `wall_limit_s` (T1 wall clock) and
  `per_test_limit_s`; a run over either fails the commit, and `deferred` tests are
  skipped. Fix a slow test rather than moving its tier. A new machine or a raised limit
  needs five baseline T1 runs and the owner's approval in that entry.
- Each tier writes JUnit reports, `scripts/run-evidence.py` normalises them, then
  `check-test-run.py` fails an empty or all-skipped tier or a missing listed test, and
  `check-test-evasion.py` judges every skip.
- Tests substitute only the adapter seams listed in `proofs.toml` `[layout]`
  `adapter_seams`. A new third-party call gets a one-call seam function and a seams entry
  (a subprocess seam also goes in `[python_quality.subprocess_wrappers]`); a test of the
  seam itself is T2 against the real third party.
- Product code takes no test-only parameters or environment flags, and resolves home
  paths at call time (`hooks.inbox_dir()`, `schedule.plist_file()`,
  `config.default_config_path()`). Tests rely on the per-test `HOME` and never write the
  real home.

## Guardrails

`static` runs Ruff, mypy, oxlint, `tsc`, markdownlint, Prettier, ShellCheck, shfmt and the
dashboard format and build checks, plus the repository-owned checkers in `scripts/`:

- Gate wiring: `check-quality-gate-parity.py` (every hook and CI point of a claim runs
  the same entrypoint command), `check-hook-installed.py`,
  `check-runner-failure-propagation.py` (a failing step fails every mode) and
  `validate-rule-coverage.py` (the `proofs` mode).
- Supply chain: `check-dependency-audit.py` audits every tracked `uv.lock` and `bun.lock`
  outside `tests/fixtures` (needs network); `check-lock-parity.py` keeps manifests, locks
  and the Bun pin in step; `check-install-guidance.py` keeps the README Quick Install in
  step with `scripts/fresh-install/sandbox.sh`; `check-skill-metadata.py` validates skill
  front matter.
- Python: `check-python-suppressions.py` fails on every `noqa`, `type: ignore` or
  Ruff/mypy ignore setting, and on a Ruff exclude that hides a maintained file. Fix the
  code instead of suppressing the finding. `check-python-quality.py`,
  `check-python-scalability.py` and `check-python-fanout.py` read `proofs.toml`
  `[python_quality]`: a new command script needs an entry in `script_names`, a function
  that starts a process does only that and is listed in `subprocess_wrappers`, and
  environment reads in analyzer-like code go through a function in `environment_seams`.
- Code integrity: `check-code-integrity.py` fails on any comment or docstring in an
  owned file (tool directives such as `noqa`, `shellcheck`, `@ts-expect-error`,
  `oxlint-disable`, `prettier-ignore` and `markdownlint` are exempt), on placeholder
  bodies, and on product mocks. `static` runs `--all`; `--staged` and `--base <ref>`
  judge added lines only. Each mode takes a git pathspec after `--`.
- Structure: `check-structure.py` judges tiers, placement, naming, `PREFIX.candidates`
  and `RENAME.residue`. With no arguments it judges branch additions and renames against
  `origin/main`; `--staged` judges the commit, `--all` the whole repository, and paths
  judge those files. A new module under `src/agent_introspection` goes in one
  `[structure].tier_modules` list (owner-reviewed). A new file whose name shares a first
  token with a sibling blocks until the owner extracts the concept or records an
  exception. A rename updates every reference to the old name in the same change.
- Tests: `check-test-placement.py`, `check-test-tiers.py`, `check-test-mocking.py`,
  `check-test-hooks.py`, `check-test-subprocess-timeouts.py`, `check-test-growth.py`,
  `check-coverage-ratchet.py` and `check-identity-artefacts.py` (the files listed in
  `[identity_artefacts]`). `check-test-budget.py` runs in `hook` mode after T1.
- TypeScript: `check-typescript-limits.ts` beside oxlint.

Script layout: command scripts directly under `scripts/` use kebab-case names, and
importable helper modules live in `scripts/lib/` with snake-case names. Tests are flat
`tests/test_*.py` files, with shared support in `tests/support/`.

## Proofs

`proofs.toml` is the repository's rule contract; there is no separate ledger, and the rule
catalogue is not kept in the repository. Each `[[claim]]` names its rules, its controls,
its enforcement points and its canaries:

- the hook point: `.pre-commit-config.yaml`, ref `scripts/run-ci-quality-gates.sh hook`;
- the CI points: `.github/workflows/ci-quality-gates.yml`, ref
  `scripts/run-ci-quality-gates.sh static`, `t1` or `t2`, status `gate`;
- canaries: a passing and a violating fixture under `tests/fixtures/proofs/<claim-id>/`,
  each run through `bash scripts/run-ci-quality-gates.sh check <name> {fixture}`.

Values with no native config home sit in its settings tables (`[python_quality]`,
`[structure]`, `[layout]`, `[test_budget]`, `[surface]`, `[identity_artefacts]`, `[golden]`). Every `[[exception]]` and
`[[not_applicable]]` entry carries the owner's `approved_by`. Run the validator, as the
`proofs` mode and its CI job do:

```sh
uv run python scripts/validate-rule-coverage.py --root .
```

`--structure-only` checks every field and path without running the canaries.

To add or change a rule, change its checker, the checker's tests, its fixtures and its
claim together. `tests/fixtures/**` is outside every routine scan, so prove each fixture
by passing it explicitly (`check <name> <fixture>`), then run the `proofs` mode. A rule
that needs a base ref or repository state uses a scenario canary:
`bash scripts/run-ci-quality-gates.sh check scenario <check> <fixture-dir>` builds a
temporary Git repository from the fixture's `base/` and `head/` and runs the check with
the fixture's `argv` file.

Only the owner edits the paths `.github/CODEOWNERS` lists, among them `proofs.toml`,
the gate scripts, the workflows and `.github/dependency-audit.json`. An agent never adds
or edits a dependency-audit exception: it upgrades or replaces the dependency a
`SECURITY.dependency-audit` line names, or reports it to the owner.

## CI

`.github/workflows/ci-quality-gates.yml` runs on every push and pull request. Its jobs
call the entrypoint with full history: `static` (with `PROOFS_BASE_REF`), `t1`, `t2`,
`proofs` and `fresh-install` (the `install` mode, on Docker). The `install` mode's lever
runs `agent-introspection facts verify` against the sandbox ClickHouse, so every facts SQL
statement builds there. The aggregate `gate` job passes only when `static`, `t1`, `t2`
and `fresh-install` pass. Branch protection on `main` requires the `gate` and `proofs`
statuses and code-owner review; the `host.required-checks` claim reads that protection
back.

## What To Check

- `bash scripts/run-ci-quality-gates.sh hook` exits zero on the commit, and `proofs` exits
  zero when a rule, checker, fixture or `proofs.toml` changed.
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
- [Fresh-install sandbox](../../scripts/fresh-install/README.md): the `install` mode.
