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
  shellcheck
  shfmt
  dashboard_format
  dashboard_build
)

tracked() {
  local file
  git ls-files -z -- "$@" "$fixtures_exclude" | while IFS= read -r -d '' file; do
    if [[ -f "$file" && ! -L "$file" ]]; then
      printf '%s\0' "$file"
    fi
  done
}

python_scope() {
  uv run python scripts/list-python-scope.py
}

shell_scope() {
  local file first
  tracked '*' | while IFS= read -r -d '' file; do
    case "${file##*/}" in
      *.sh | *.bash) printf '%s\0' "$file" ;;
      *.*) ;;
      *)
        first=""
        IFS= read -r first <"$file" || true
        if [[ "$first" =~ ^#!.*[/[:space:]](ba|da|k)?sh([[:space:]]|$) ]]; then
          printf '%s\0' "$file"
        fi
        ;;
    esac
  done
}

dashboard_tool() {
  local tool="$1"
  shift
  bun "dashboard/node_modules/.bin/$tool" "$@"
}

scanned=()
scan() {
  scanned=()
  local list file status=0
  list="$(mktemp)"
  "$@" >"$list" || status=$?
  while IFS= read -r -d '' file; do
    scanned+=("$file")
  done <"$list"
  rm -f "$list"
  return "$status"
}

check_gate_parity() { bash scripts/check-quality-gate-parity.sh; }
check_lock_parity() { uv run python scripts/check-lock-parity.py "$@"; }
check_skill_metadata() { uv run python scripts/check-skill-metadata.py "$@"; }
check_subprocess_timeouts() { uv run python scripts/check-test-subprocess-timeouts.py "$@"; }
check_dependency_audit() { uv run python scripts/check-dependency-audit.py "$@"; }

check_ruff_format() {
  local files=("$@")
  ((${#files[@]})) || {
    scan python_scope
    files=("${scanned[@]}")
  }
  uv run ruff format --check --force-exclude "${files[@]}"
}

check_ruff() {
  local files=("$@")
  ((${#files[@]})) || {
    scan python_scope
    files=("${scanned[@]}")
  }
  uv run ruff check --no-fix --force-exclude "${files[@]}"
}

check_mypy() { uv run python scripts/check-python-mypy.py "$@"; }
check_python_suppressions() { uv run python scripts/check-python-suppressions.py "$@"; }
check_python_quality() { uv run python scripts/check-python-quality.py "$@"; }
check_python_scalability() { uv run python scripts/check-python-scalability.py "$@"; }

check_python_fanout() {
  uv run python scripts/check-python-fanout.py --source-root src --source-root scripts \
    --source-root tests --source-root .agents/skills --root-package agent_introspection "$@"
}

check_oxlint() {
  local files=("$@")
  ((${#files[@]})) || {
    scan tracked '*.ts' '*.tsx' '*.mts' '*.cts'
    files=("${scanned[@]}")
  }
  dashboard_tool oxlint --config oxlint.config.ts --deny-warnings "${files[@]}"
}

check_markdownlint() {
  local files=("$@")
  ((${#files[@]})) || {
    scan tracked '*.md' '*.markdown'
    files=("${scanned[@]}")
  }
  bunx --bun markdownlint-cli2@0.23.2 "${files[@]}"
}

check_prettier() {
  local files=("$@")
  ((${#files[@]})) || {
    scan tracked '*.md' '*.markdown' '*.yml' '*.yaml'
    files=("${scanned[@]}")
  }
  dashboard_tool prettier --check "${files[@]}"
}

check_typescript_limits() { bun scripts/check-typescript-limits.ts "$@"; }
check_tsc() {
  local files=("$@")
  ((${#files[@]})) || {
    scan tracked '*.ts' '*.tsx' '*.mts' '*.cts'
    files=("${scanned[@]}")
  }
  local project status=0
  project="$(mktemp -d)"
  bun -e 'const { resolve } = require("node:path"); console.log(JSON.stringify({ extends: resolve(process.argv[1]), files: process.argv.slice(2).map((path) => resolve(path)), include: [] }))' \
    tsconfig.json "${files[@]}" >"$project/tsconfig.json"
  bun dashboard/node_modules/.bin/tsc --project "$project/tsconfig.json" || status=$?
  rm -rf "$project"
  return "$status"
}

check_shellcheck() {
  local files=("$@")
  ((${#files[@]})) || {
    scan shell_scope
    files=("${scanned[@]}")
  }
  uv run shellcheck --format=gcc "${files[@]}"
}

check_shfmt() {
  local files=("$@")
  ((${#files[@]})) || {
    scan shell_scope
    files=("${scanned[@]}")
  }
  uv run shfmt --diff "${files[@]}"
}
check_dashboard_format() { bun run --cwd dashboard format:check; }
check_dashboard_build() { bun run --cwd dashboard build; }

prepare() {
  uv sync --locked --dev
  uv lock --check
  bun install --cwd dashboard --frozen-lockfile
  bun install --cwd scripts/ux-probe --frozen-lockfile
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

(($#)) || {
  usage >&2
  exit 2
}
mode="$1"
shift
case "$mode" in
  static | t1 | t2 | proofs)
    (($# == 0)) || {
      usage >&2
      exit 2
    }
    prepare
    "run_$mode"
    ;;
  hook)
    (($# == 0)) || {
      usage >&2
      exit 2
    }
    prepare
    run_static
    run_t1
    run_t2
    ;;
  check)
    (($#)) || {
      usage >&2
      exit 2
    }
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
