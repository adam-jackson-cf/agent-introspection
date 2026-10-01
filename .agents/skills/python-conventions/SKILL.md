---
name: "python-conventions"
description: "Use when writing or refactoring Python code in agent-introspection (src/, tests/, scripts/, or .agents adapters). Guides Python naming, package structure, code-object choices, and this repository's Python quality gates."
---

# Guidance

Complements Ruff `pep8-naming`; does not replace deterministic lint checks.

## Names

- Use precise domain names over generic names such as `manager`, `helper`, `utils`, `data`, `thing`, or `processor`.
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

- Ruff (line length 100): docstrings that exist follow the numpy convention (missing
  docstrings are not enforced); exceptions end in `Error`; complexity limits C901,
  PLR0911 (at most 6 returns), PLR0912, PLR0913 (at most 5 arguments, keyword-only
  ones included: pass a dataclass or `**overrides`), and PLR0915. Split a function
  rather than suppress a rule.
- `mypy --strict` over `agent_introspection`; tests are exempt from docstring rules.
- Python under `.agents/skills/*/scripts` is in the quality scope.
- `bash scripts/run-ci-quality-gates.sh` is the pre-commit and CI suite (it also checks
  the dashboard). It lints only files git tracks, so also run `uv run ruff check .`,
  `uv run ruff format --check .`, and `uv run pytest` before new files are added.
- Keep derivations that exist in both SQL (`src/agent_introspection/facts_sql/`) and
  Python identical, with a test that pins them (for example `hooks.py` and
  `select_logs.sql`).
