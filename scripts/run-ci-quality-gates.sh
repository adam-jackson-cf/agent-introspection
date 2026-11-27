#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s static|t1|t2|proofs|hook\n       %s check <name> [path ...]\n' "$0" "$0"
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fixtures_exclude=':(exclude)tests/fixtures/**'

static_checks=(
  gate_parity
  lock_parity
  skill_metadata
  subprocess_timeouts
  dependency_audit
  ruff_format
  ruff
  mypy
  python_suppressions
  python_quality
  python_scalability
  python_fanout
  oxlint
  markdownlint
  prettier
  typescript_limits
  tsc
  dashboard_format
  dashboard_build
)

tracked() {
  local file
  while IFS= read -r -d '' file; do
    [[ -f "$file" && ! -L "$file" ]] && printf '%s\0' "$file"
  done < <(git ls-files -z -- "$@" "$fixtures_exclude")
}

python_scope() {
  uv run python scripts/list-python-scope.py
}

scanned=()
scan() {
  scanned=()
  local file
  while IFS= read -r -d '' file; do
    scanned+=("$file")
  done < <("$@")
}

check_gate_parity() { bash scripts/check-quality-gate-parity.sh; }
check_lock_parity() { uv run python scripts/check-lock-parity.py "$@"; }
check_skill_metadata() { uv run python scripts/check-skill-metadata.py "$@"; }
check_subprocess_timeouts() { uv run python scripts/check-test-subprocess-timeouts.py "$@"; }
check_dependency_audit() { uv run python scripts/check-dependency-audit.py "$@"; }

check_ruff_format() {
  local files=("$@")
  ((${#files[@]})) || { scan python_scope; files=("${scanned[@]}"); }
  uv run ruff format --check --force-exclude "${files[@]}"
}

check_ruff() {
  local files=("$@")
  ((${#files[@]})) || { scan python_scope; files=("${scanned[@]}"); }
  uv run ruff check --no-fix --force-exclude "${files[@]}"
}

check_mypy() { uv run python scripts/check-python-mypy.py "$@"; }
check_python_suppressions() { uv run python scripts/check-python-suppressions.py "$@"; }
check_python_quality() { PYTHONDONTWRITEBYTECODE=1 uv run python scripts/check-python-quality.py "$@"; }
check_python_scalability() { PYTHONDONTWRITEBYTECODE=1 uv run python scripts/check-python-scalability.py "$@"; }
check_python_fanout() { PYTHONDONTWRITEBYTECODE=1 uv run python scripts/check-python-fanout.py "$@"; }

check_oxlint() {
  local files=("$@")
  ((${#files[@]})) || { scan tracked '*.ts' '*.tsx' '*.mts' '*.cts'; files=("${scanned[@]}"); }
  bunx --bun oxlint@1.80.0 --config oxlint.config.ts "${files[@]}"
}

check_markdownlint() {
  local files=("$@")
  ((${#files[@]})) || { scan tracked '*.md' '*.markdown'; files=("${scanned[@]}"); }
  bunx --bun markdownlint-cli2@0.23.2 "${files[@]}"
}

check_prettier() {
  local files=("$@")
  ((${#files[@]})) || { scan tracked '*.md' '*.markdown' '*.yml' '*.yaml'; files=("${scanned[@]}"); }
  bunx --bun prettier@3.6.2 --check "${files[@]}"
}

check_typescript_limits() { bun scripts/check-typescript-limits.ts "$@"; }
check_tsc() { bun run --cwd dashboard check; }
check_dashboard_format() { bun run --cwd dashboard format:check; }
check_dashboard_build() { bun run --cwd dashboard build; }

prepare() {
  uv sync --locked --dev
  uv lock --check
  bun install --cwd dashboard --frozen-lockfile
}

run_static() {
  local name
  for name in "${static_checks[@]}"; do
    "check_$name"
  done
}

run_t1() {
  uv run pytest -m "not boundary"
  bun run --cwd dashboard test
}

run_t2() {
  uv run pytest -m boundary
}

run_proofs() {
  uv run python scripts/validate-rule-coverage.py --root . --catalog docs/guardrails/enaible-rules.json \
    --ledger docs/guardrails/coverage-ledger.json
}

(($#)) || { usage >&2; exit 2; }
mode="$1"
shift
case "$mode" in
  static | t1 | t2 | proofs)
    (($# == 0)) || { usage >&2; exit 2; }
    prepare
    "run_$mode"
    ;;
  hook)
    (($# == 0)) || { usage >&2; exit 2; }
    prepare
    run_static
    run_t1
    run_t2
    ;;
  check)
    (($#)) || { usage >&2; exit 2; }
    name="$1"
    shift
    if ! declare -F "check_$name" >/dev/null; then
      printf 'Unknown check: %s\n' "$name" >&2
      exit 2
    fi
    "check_$name" "$@"
    ;;
  -h | --help)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
