#!/usr/bin/env bash
set -euo pipefail

fix=false
stage=false

usage() {
  printf 'Usage: %s [--fix] [--stage]\n' "$0"
}

while (($#)); do
  case "$1" in
    --fix)
      fix=true
      ;;
    --stage)
      stage=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if "$stage" && ! "$fix"; then
  printf '%s\n' '--stage requires --fix.' >&2
  exit 2
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

# Canary fixtures deliberately violate rules; git pathspec keeps them out of every linter.
fixtures_exclude=':(exclude)tests/fixtures/**'

lint_pathspecs=('*.py' '*.ts' '*.tsx' '*.mts' '*.cts' '*.md' '*.markdown' '*.yml' '*.yaml')
if "$stage"; then
  # --stage re-adds whole files the fixers touched, so it is only exact when
  # the working tree already matches the index for every lintable file.
  unstaged="$(git diff --name-only -- "${lint_pathspecs[@]}")"
  if [[ -n "$unstaged" ]]; then
    printf '%s\n' '--stage refuses to run: these files have unstaged changes that it would stage:' >&2
    printf '%s\n' "$unstaged" | sed 's/^/  /' >&2
    printf '%s\n' 'Stage or discard them first, or run without --stage.' >&2
    exit 2
  fi
fi

bash scripts/check-quality-gate-parity.sh
uv sync --locked --dev
uv lock --check
uv run python scripts/check-lock-parity.py
uv run python scripts/check-skill-metadata.py
uv run python scripts/check-test-subprocess-timeouts.py
# Slow or networked steps run in CI only (GitHub Actions sets CI=true).
rule_coverage_args=(--root . --catalog docs/guardrails/enaible-rules.json
  --ledger docs/guardrails/coverage-ledger.json)
if [[ "${CI:-}" == "true" ]]; then
  uv run python scripts/check-runner-failure-propagation.py scripts/run-ci-quality-gates.sh
  uv run python scripts/check-dependency-audit.py
  uv run python scripts/validate-rule-coverage.py "${rule_coverage_args[@]}"
else
  uv run python scripts/validate-rule-coverage.py "${rule_coverage_args[@]}" --structure-only
fi

scope_file="$(mktemp)"
coverage_dir="$(mktemp -d)"
trap 'rm -rf "$scope_file" "$coverage_dir"' EXIT
uv run python scripts/list-python-scope.py > "$scope_file"

python_files=()
while IFS= read -r -d '' file; do
  python_files+=("$file")
done < "$scope_file"

typescript_files=("oxlint.config.ts")
while IFS= read -r -d '' file; do
  [[ -f "$file" ]] || continue
  if [[ "$file" != "oxlint.config.ts" ]]; then
    typescript_files+=("$file")
  fi
done < <(git ls-files -z -- '*.ts' '*.tsx' '*.mts' '*.cts' "$fixtures_exclude")

markdown_files=()
while IFS= read -r -d '' file; do
  [[ -f "$file" ]] || continue
  markdown_files+=("$file")
done < <(git ls-files -z -- '*.md' '*.markdown' "$fixtures_exclude")

yaml_files=()
while IFS= read -r -d '' file; do
  [[ -f "$file" ]] || continue
  yaml_files+=("$file")
done < <(git ls-files -z -- '*.yml' '*.yaml' "$fixtures_exclude")

# Prettier refuses symlinks given explicitly; the linked target is checked on its own.
prettier_files=()
for file in "${markdown_files[@]}" "${yaml_files[@]}"; do
  [[ -L "$file" ]] || prettier_files+=("$file")
done
lint_files=("${python_files[@]}" "${typescript_files[@]}" "${markdown_files[@]}" "${yaml_files[@]}")
lint_hashes=()
if "$fix" && "$stage"; then
  for file in "${lint_files[@]}"; do
    lint_hashes+=("$(git hash-object "$file")")
  done
fi

if "$fix"; then
  uv run ruff format --force-exclude "${python_files[@]}"
  uv run ruff check --fix --force-exclude "${python_files[@]}"
else
  uv run ruff format --check --force-exclude "${python_files[@]}"
  uv run ruff check --no-fix --force-exclude "${python_files[@]}"
fi

uv run python scripts/check-python-mypy.py
uv run python scripts/check-python-suppressions.py
uv run python scripts/check-python-quality.py
uv run python scripts/check-python-scalability.py
uv run python scripts/check-python-fanout.py --source-root src --source-root scripts \
  --source-root tests --source-root .agents/skills --root-package agent_introspection
# COVERAGE_FILE keeps the tracked .coverage artifact untouched.
COVERAGE_FILE="$coverage_dir/.coverage" uv run pytest --cov=agent_introspection \
  --cov-report="json:$coverage_dir/coverage.json"
uv run python scripts/check-coverage-slice.py "$coverage_dir/coverage.json"

if ((${#typescript_files[@]})); then
  if "$fix"; then
    bunx --bun oxlint@1.80.0 --config oxlint.config.ts --fix "${typescript_files[@]}"
  else
    bunx --bun oxlint@1.80.0 --config oxlint.config.ts "${typescript_files[@]}"
  fi
fi

if ((${#markdown_files[@]})); then
  if "$fix"; then
    bunx --bun markdownlint-cli2@0.23.2 --fix "${markdown_files[@]}"
  else
    bunx --bun markdownlint-cli2@0.23.2 "${markdown_files[@]}"
  fi
fi

if ((${#prettier_files[@]})); then
  if "$fix"; then
    bunx --bun prettier@3.6.2 --write "${prettier_files[@]}"
  else
    bunx --bun prettier@3.6.2 --check "${prettier_files[@]}"
  fi
fi

bun install --cwd dashboard --frozen-lockfile
bun scripts/check-typescript-limits.ts
bun run --cwd dashboard check
bun run --cwd dashboard test
bun run --cwd dashboard lint
bun run --cwd dashboard format:check
bun run --cwd dashboard build

if "$stage"; then
  for index in "${!lint_files[@]}"; do
    file="${lint_files[$index]}"
    if [[ "$(git hash-object "$file")" != "${lint_hashes[$index]}" ]]; then
      git add -- "$file"
    fi
  done
fi
