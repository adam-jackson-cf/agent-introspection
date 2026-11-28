---
name: "testing-approach"
description: "Use when writing, changing, moving or deleting tests in agent-introspection."
---

# Task

## Precedence

- This repository's instruction files and the files each route names take precedence; this skill routes to them and fills gaps.
- Read the route for the task before starting it, and every file the route names, in order.

## Tiers and facts

- T1 (untagged; repository marker absence): selected by `-m "not boundary and not deferred"` and `bun test` discovery in `dashboard/`; run `bash scripts/run-ci-quality-gates.sh t1` on every change, locally and in the commit hook, and in the CI `t1` job; budget 19 s whole run (`proofs.toml` `[[test_budget.machine]]`, checked by `scripts/check-test-budget.py` in the commit hook); per test 1 s (`scripts/check-test-budget.py`).
- T2 (boundary; repository marker `boundary`): selected by `-m "(boundary or deferred) and not (boundary and deferred)"`; run `bash scripts/run-ci-quality-gates.sh t2` before merge, in the commit hook and the CI `t2` job; budget 15 min (owner default, 2026-11-28).
- T2 deferred (boundary): selected by `-m "boundary and deferred"` and the dashboard files `*.deferred.ts`; run `bash scripts/run-ci-quality-gates.sh install` in the CI `fresh-install` job on every pull request (it needs Docker).
- T3: no tests yet; ask the user where the first one goes.
- Public entry points: agent_introspection: the `agent-introspection` CLI (`agent_introspection.cli:main`) and the package's modules; dashboard: the HTTP app `createApp` in `dashboard/server.ts`.
- Layout: flat `tests/test_<area>_*.py` files and journeys in `tests/journeys/test_*.py`; dashboard tests beside their code as `*.test.ts`, `*.test.tsx` and `*.deferred.ts` (`proofs.toml` `[layout] test_file_patterns`). Fixtures: `tests/conftest.py` (`isolated_home`, `fake_omp`, `open_workflow`, `collector`, `inbox`). Support code: `tests/support/` and `dashboard/server/test-fixtures.ts`.
- Production inputs: parameters with production defaults, such as a clock argument; process runners and third-party clients are the one-call seam functions listed in `proofs.toml` `[layout] adapter_seams`.
- Name tests like `test_failed_decisions_are_recorded_with_a_reason_and_retried_later`.
- Coverage: floors in `pyproject.toml` `[tool.coverage.report]`: `fail_under = 85` (T1 pytest). Per-test limits: none configured.
- Boundary launch: `subprocess.run([...], capture_output=True, text=True, timeout=<seconds>, check=False)` inside a one-call wrapper listed for its file in `proofs.toml` `[python_quality.subprocess_wrappers]`, such as `_run_activity_shim` in `tests/test_hooks_runtime_scripts.py`.
- Golden files: `tests/fixtures/command_shape.json` and `docs/ux/feature-audit/titles.json`, verified by `bash scripts/run-ci-quality-gates.sh t1`; no regeneration command is documented, so ask the user before adding one; record each change in `docs/references/golden-changes.md` as one line under `Entries`, newest first, with the date, the golden file's path and why it changed.

## Every task

- See docs/references/development.md (Test tiers).
- Local checks: the commit hook configured in `.pre-commit-config.yaml` (installed with `bash scripts/run-ci-quality-gates.sh prepare`), which runs `bash scripts/run-ci-quality-gates.sh hook`: the static checks, all T1 and all T2. CI runs `scripts/ci static`, `scripts/ci t1`, `scripts/ci t2`, `scripts/ci proofs` and `scripts/ci install` on pull requests. See docs/references/development.md (What To Check).
- Automatic fixes: only through `bash scripts/fix-quality-gates.sh`. See docs/references/development.md (Steps).
- Never add skip, xfail, only, retry or quarantine markers, rerun until green, raise a timeout or loosen an assertion
  to get a pass, unless the user asks for it.

## Task routes

- Writing, changing, moving or deleting a test: docs/references/development.md (Test tiers), then docs/references/development.md (Guardrails), then .agents/skills/python-conventions/SKILL.md (Repository gates), then [.agents/skills/testing-approach/references/writing-tests.md](references/writing-tests.md).
- Adding or changing a T2 boundary test or journey: docs/references/development.md (Steps), then [.agents/skills/testing-approach/references/boundary-tests.md](references/boundary-tests.md).
- A test passes and fails on the same tree: [.agents/skills/testing-approach/references/flaky-tests.md](references/flaky-tests.md).
- Reviewing or deleting tests, or changing the suite's run time: docs/references/development.md (Guardrails), then [.agents/skills/testing-approach/references/suite-maintenance.md](references/suite-maintenance.md).
- Adding, changing or regenerating golden or snapshot files: docs/references/golden-changes.md (Golden changes), then [.agents/skills/testing-approach/references/golden-files.md](references/golden-files.md).

## Repository decisions

- Kept the repository rule: tests substitute only the adapter seams in `proofs.toml` `[layout] adapter_seams`, by runtime patching, so the approach rule against mocking or patching own code is not adopted (owner, 2026-11-28).
- Not adopted: recording a deferral's reason in the test file; the repository forbids comments, and the owner approves each deferral as a `proofs.toml` exception (owner, 2026-11-28).
- Not installed: the property-tests runbook (no property library; `TEST.property-seed` is approved as not applicable) and a `deferred(reason=...)` marker on the journey (owner, 2026-11-28).
- Deferred T2: `tests/journeys/test_install_journey.py`, `tests/test_command_shape_parity.py` and `dashboard/server/pipeline.deferred.ts`; run with the CI `fresh-install` job; approval and reason in `proofs.toml` `[[exception]]`.
- No root `AGENTS.md`: `tests/AGENTS.md` and `dashboard/AGENTS.md` hold the pointer to this skill, each with a `CLAUDE.md` symlink (owner, 2026-11-28).
- Installed by this skill: .agents/skills/testing-approach/SKILL.md, .agents/skills/testing-approach/references/writing-tests.md, .agents/skills/testing-approach/references/boundary-tests.md, .agents/skills/testing-approach/references/flaky-tests.md, .agents/skills/testing-approach/references/suite-maintenance.md, .agents/skills/testing-approach/references/golden-files.md, tests/AGENTS.md, tests/CLAUDE.md, dashboard/AGENTS.md, dashboard/CLAUDE.md.
