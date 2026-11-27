#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s static|t1|t2|install|proofs|hook\n       %s check <name> [path ...]\n' "$0" "$0"
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

fixtures_exclude=':(exclude)tests/fixtures/**'

hook_modes=(static t1 t2)

static_checks=(
  gate_parity
  install_guidance
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

check_gate_parity() { uv run python scripts/check-quality-gate-parity.py "${1:-.}"; }
check_install_guidance() {
  local guide=README.md installer=scripts/fresh-install/sandbox.sh
  if (($#)); then
    guide="$1/README.md"
    installer="$1/sandbox.sh"
  fi
  uv run python scripts/check-install-guidance.py --guide "$guide" --installer "$installer" \
    --section "Quick Install"
}

check_hook_installed() { uv run python scripts/check-hook-installed.py .pre-commit-config.yaml; }
check_lock_parity() {
  if (($#)); then
    uv run python scripts/check-lock-parity.py "$@"
  else
    uv run python scripts/check-lock-parity.py --exclude "$fixtures_exclude" .
  fi
}
check_skill_metadata() {
  uv run python scripts/check-skill-metadata.py --schema .agents/skill-metadata.schema.json "$@"
}
check_subprocess_timeouts() { uv run python scripts/check-test-subprocess-timeouts.py "$@"; }
check_dependency_audit() {
  uv run python scripts/check-dependency-audit.py --policy .github/dependency-audit.json "$@"
}

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
check_python_quality() { uv run python scripts/check-python-quality.py "$@"; }
check_python_scalability() { uv run python scripts/check-python-scalability.py "$@"; }

check_python_fanout() {
  uv run python scripts/check-python-fanout.py --source-root src --source-root scripts \
    --source-root tests --source-root .agents/skills --root-package agent_introspection "$@"
}

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
  uv run pre-commit install
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

run_install() {
  local root log status=0
  root="$(bash scripts/fresh-install/sandbox.sh create)"
  bash scripts/fresh-install/sandbox.sh verify "$root" || status=$?
  if ((status)); then
    for log in "$root"/state/verify/*; do
      if [[ -f "$log" ]]; then
        printf '== %s\n' "$log" >&2
        cat "$log" >&2
      fi
    done
  fi
  bash scripts/fresh-install/sandbox.sh destroy "$root"
  return "$status"
}

check_proofs() { uv run python scripts/validate-rule-coverage.py --root "${1:-.}"; }

run_proofs() {
  check_proofs
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
  install)
    (($# == 0)) || { usage >&2; exit 2; }
    run_install
    ;;
  hook)
    (($# == 0)) || { usage >&2; exit 2; }
    prepare
    for mode in "${hook_modes[@]}"; do
      "run_$mode"
    done
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
