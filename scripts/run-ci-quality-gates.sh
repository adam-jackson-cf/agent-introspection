#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s static|t1|t2|proofs|hook\n       %s check <name> [path ...]\n' "$0" "$0"
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export PYTHONDONTWRITEBYTECODE=1

fixtures_exclude=':(exclude)tests/fixtures/**'
t1_selection=(-m "not boundary")
t2_selection=(-m boundary)
pytest_report=(-p no:cacheprovider -o junit_family=xunit1)
collect_timeout_seconds=300
report_dir=""
t1_started=""

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
  failure_propagation
  test_growth
  coverage_ratchet
  identity_artefacts
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

diff_base() {
  if [[ -n "${PROOFS_BASE_REF:-}" ]]; then
    git merge-base HEAD "$PROOFS_BASE_REF"
  else
    git rev-parse HEAD
  fi
}

diff_check() {
  local script=$1
  shift
  if (($#)); then
    uv run python "scripts/$script" "$@"
  else
    uv run python "scripts/$script" --repo . --base "$(diff_base)"
  fi
}

check_failure_propagation() {
  uv run python scripts/check-runner-failure-propagation.py "${1:-scripts/run-ci-quality-gates.sh}" \
    static t1 t2 hook
}

check_test_run() { uv run python scripts/check-test-run.py "$@"; }
check_test_budget() { uv run python scripts/check-test-budget.py "$@"; }
check_test_growth() { diff_check check-test-growth.py "$@"; }
check_coverage_ratchet() { diff_check check-coverage-ratchet.py "$@"; }
check_identity_artefacts() { diff_check check-identity-artefacts.py "$@"; }
check_golden_changes() { diff_check check-golden-changes.py "$@"; }
check_verification_surface() { uv run python scripts/check-verification-surface.py "$@"; }
check_lever_isolation() { uv run python scripts/check-lever-isolation.py "$@"; }

check_scenario() {
  (($# == 2)) || { usage >&2; return 2; }
  local name=$1 fixture=$2 work base line code=0 args=()
  work="$(mktemp -d)"
  base="$(uv run python scripts/build-scenario-repo.py "$fixture" "$work/repo")"
  while IFS= read -r line; do
    line=${line//\{repo\}/$work/repo}
    args+=("${line//\{base\}/$base}")
  done <"$fixture/argv"
  "check_$name" "${args[@]}" || code=$?
  rm -rf "$work"
  return "$code"
}
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

make_report_dir() {
  if [[ -z "$report_dir" ]]; then
    report_dir="$(mktemp -d)"
    trap 'rm -rf "$report_dir"' EXIT
  fi
}

collect_tier() {
  local tier=$1
  shift
  uv run python scripts/test-evidence.py collect --tier "$tier" --out "$report_dir/$tier-listing.json" \
    --timeout-seconds "$collect_timeout_seconds" -- "$@"
}

normalise_report() {
  uv run python scripts/test-evidence.py normalise "$@"
}

run_t1() {
  make_report_dir
  t1_started="$(date +%s)"
  collect_tier t1 "${t1_selection[@]}"
  COVERAGE_FILE="$report_dir/.coverage" uv run pytest "${pytest_report[@]}" \
    --junitxml="$report_dir/t1-pytest.xml" --cov=agent_introspection --cov-report= "${t1_selection[@]}"
  normalise_report pytest --tier t1 "$report_dir/t1-pytest.xml" "$report_dir/t1-pytest.json"
  bun run --cwd dashboard test --reporter=junit --reporter-outfile="$report_dir/t1-bun.xml"
  normalise_report bun --tier t1 --prefix dashboard/ "$report_dir/t1-bun.xml" "$report_dir/t1-bun.json"
  check_test_run --listing "$report_dir/t1-listing.json" "$report_dir/t1-pytest.json" "$report_dir/t1-bun.json"
}

run_t2() {
  make_report_dir
  collect_tier t2 "${t2_selection[@]}"
  uv run pytest "${pytest_report[@]}" --junitxml="$report_dir/t2-pytest.xml" "${t2_selection[@]}"
  normalise_report pytest --tier t2 "$report_dir/t2-pytest.xml" "$report_dir/t2-pytest.json"
  check_test_run --listing "$report_dir/t2-listing.json" "$report_dir/t2-pytest.json"
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
    check_test_budget --since "$t1_started" "$report_dir/t1-pytest.json" "$report_dir/t1-bun.json"
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
