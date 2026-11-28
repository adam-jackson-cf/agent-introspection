---
name: "python-conventions"
description: "Use when writing or refactoring Python code in agent-introspection (src/, tests/, scripts/, or .agents adapters). Guides Python naming, package structure, code-object choices, and this repository's Python quality gates."
---

# Guidance

Complements Ruff `pep8-naming`; does not replace deterministic lint checks.

## Names

- Use precise domain names over generic names such as `data`, `thing`, or `processor`. The names the gate bans are listed once, in `proofs.toml` `[structure]` `banned_words` (`common`, `compat`, `fallback`, `helpers`, `legacy`, `manager`, `misc`, `new`, `old`, `stuff`, `temp`, `utils`).
- Name functions and methods by the action or question they perform.
- Name classes by the role or concept they model, not by implementation mechanics.
- Name protocols by the capability they require.
- Name modules and packages after cohesive responsibilities, not mixed tool buckets.

## Package Structure

- Keep folders grouped by responsibility and import boundary, not by incidental file type.
- Add a new package only when it owns a stable concept, API boundary, or workflow slice.
- Avoid catch-all directories unless the repo already has a specific established convention for them.

## Object Choice

- Start with a function for stateless behavior.
- Use a class when state and behavior belong together, lifecycle matters, or polymorphism is needed.
- Use a dataclass for structured data carriers with annotated fields.
- Use a Protocol for structural contracts across implementations.
- Use an Enum for a closed symbolic set.
- Do not create a god class to centralize unrelated workflows.

## Repository gates

- No comments or docstrings anywhere in the repository, tests included. Only tool
  directives are allowed (`noqa`, `type: ignore`, `fmt`, `shellcheck`,
  `@ts-expect-error`, `oxlint`/`eslint`, `prettier-ignore`, `markdownlint`), and
  `scripts/check-python-suppressions.py` still rejects every `noqa` and `type: ignore`.
  `scripts/check-code-integrity.py` owns the rule. Put a durable reason in
  `docs/references/` instead of a comment.
- Ruff (line length 100, `fix = false`): exceptions end in `Error`; no blind
  `except Exception` (BLE001); complexity limits C901, PLR0904 (at most 20 public
  methods), PLR0911 (at most 6 returns), PLR0912, PLR0913 (at most 5 arguments, keyword-only
  ones included: pass a dataclass or `**overrides`), PLR0914, PLR0915 and PLR0917. Split a
  function rather than suppress a rule.
- `mypy --strict` over `src`, `scripts` and `tests`.
- Python under `.agents/skills/*/scripts` is in the quality scope. A new command script
  under `scripts/` needs an entry in `proofs.toml` `[python_quality]` `script_names`.
- A function that starts a process does only that and is listed in `proofs.toml`
  `[python_quality.subprocess_wrappers]`; environment reads in analyzer-like code go
  through a function listed in `[python_quality.environment_seams]`.
- Tests substitute only the adapter seams in `proofs.toml` `[layout]` `adapter_seams`. A
  new third-party call gets a one-call seam function and a seams entry; a test of the
  seam itself is T2 against the real third party. Product code takes no test-only
  parameters or environment flags.
- A test file holds one tier: T1 files are untagged and start no process or socket; T2
  files set a module-level `pytestmark = pytest.mark.boundary`; journeys under
  `tests/journeys/` and tests that need a ClickHouse container add `deferred` (dashboard
  files `*.deferred.ts`) and run only in the `install` mode, each under an approved
  `proofs.toml` exception.
- `bash scripts/run-ci-quality-gates.sh hook` is the commit suite (static checks, T1 and
  T2, including the dashboard); CI runs the `static`, `t1`, `t2`, `proofs` and
  `install` modes.
  Every mode checks only and lints only files git tracks: `git add` a new file, or pass it
  explicitly, as in `bash scripts/run-ci-quality-gates.sh check ruff <path>`. Fix with
  `bash scripts/fix-quality-gates.sh`.
- Keep derivations that exist in both SQL (`src/agent_introspection/facts_sql/`) and
  Python identical, with a test that pins them (for example `hooks.py` and
  `select_logs.sql`).
