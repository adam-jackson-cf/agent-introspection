#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'Usage: %s static|t1|t2|install|proofs|hook\n       %s check <name> [path ...]\n' "$0" "$0"
}

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
export PYTHONDONTWRITEBYTECODE=1

fixtures_exclude=':(exclude)tests/fixtures/**'
structure_event=--merge-base

hook_modes=(static t1 t2)
t1_tier='not boundary and not deferred'
t2_tier='boundary or deferred'
t1_selection=(-m "$t1_tier")
t2_selection=(-m "$t2_tier")
pytest_report=(-p no:cacheprovider -o junit_family=xunit1)
collect_timeout_seconds=300
report_dir=""
t1_started=""

static_checks=(
  gate_parity
  install_guidance
  lock_parity
  skill_metadata
  subprocess_timeouts
  test_placement
  test_mocking
  test_tiers
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
  structure
  failure_propagation
  test_growth
  coverage_ratchet
  identity_artefacts
  test_hooks
  test_evasion
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
  uv run python -B scripts/list-python-scope.py
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

check_structure() {
  if (($#)); then
    uv run python -B scripts/check-structure.py "$@"
  else
    uv run python -B scripts/check-structure.py "$structure_event"
  fi
}

check_test_mocking() { uv run python -B scripts/check-test-mocking.py "$@"; }
check_test_hooks() { uv run python -B scripts/check-test-hooks.py "$@"; }
check_test_tiers() { uv run python -B scripts/check-test-tiers.py --tier "t1=$t1_tier" --tier "t2=$t2_tier" "$@"; }

check_test_placement() {
  if (($#)); then
    uv run python -B scripts/check-test-placement.py "$@"
  else
    uv run python -B scripts/check-test-placement.py "$structure_event"
  fi
}

check_test_evasion() {
  if (($#)); then
    uv run python -B scripts/check-test-evasion.py "$@"
  else
    uv run python -B scripts/check-test-evasion.py "$structure_event"
  fi
}

pytest_tier() {
  local selector="$1"
  shift
  uv run pytest -p no:cacheprovider -m "$selector" "$@"
}

check_t1_isolation() { pytest_tier "$t1_tier" "$@"; }
check_user_data_guard() { uv run pytest -p no:cacheprovider "$@"; }

check_bun_t1_isolation() {
  (($#)) || { bun run --cwd dashboard test; return; }
  local files=()
  local file
  for file in "$@"; do
    [[ "$file" == /* ]] || file="$repo_root/$file"
    files+=("$file")
  done
  (cd dashboard && bun test "${files[@]}")
}

check_ruff_format() {
  local files=("$@")
  ((${#files[@]})) || {
    scan python_scope
    files=("${scanned[@]}")
  }
  uv run ruff format --check --no-cache "${files[@]}"
}

check_ruff() {
  local files=("$@")
  ((${#files[@]})) || {
    scan python_scope
    files=("${scanned[@]}")
  }
  uv run ruff check --no-fix --no-cache "${files[@]}"
}

check_mypy() { uv run python -B scripts/check-python-mypy.py "$@"; }
check_python_suppressions() { uv run python -B scripts/check-python-suppressions.py "$@"; }
check_python_quality() { PYTHONDONTWRITEBYTECODE=1 uv run python -B scripts/check-python-quality.py "$@"; }
check_python_scalability() { PYTHONDONTWRITEBYTECODE=1 uv run python -B scripts/check-python-scalability.py "$@"; }
check_python_fanout() { PYTHONDONTWRITEBYTECODE=1 uv run python -B scripts/check-python-fanout.py "$@"; }

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
check_dashboard_format() { bun run --cwd dashboard format:check; }
check_dashboard_build() { bun run --cwd dashboard build; }

prepare() {
  uv sync --locked --dev
  uv lock --check
  uv run pre-commit install
  bun install --cwd dashboard --frozen-lockfile
  bun install --cwd scripts/ux-probe --frozen-lockfile
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
  check_test_evasion --report "$report_dir/t1-pytest.xml" --report "$report_dir/t1-bun.xml"
}

run_t2() {
  make_report_dir
  collect_tier t2 "${t2_selection[@]}"
  uv run pytest "${pytest_report[@]}" --junitxml="$report_dir/t2-pytest.xml" "${t2_selection[@]}"
  normalise_report pytest --tier t2 "$report_dir/t2-pytest.xml" "$report_dir/t2-pytest.json"
  check_test_run --listing "$report_dir/t2-listing.json" "$report_dir/t2-pytest.json"
  check_test_evasion --report "$report_dir/t2-pytest.xml"
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
  install)
    (($# == 0)) || { usage >&2; exit 2; }
    run_install
    ;;
  hook)
    (($# == 0)) || {
      usage >&2
      exit 2
    }
    structure_event=--staged
    prepare
    for mode in "${hook_modes[@]}"; do
      "run_$mode"
      if [[ "$mode" == t1 ]]; then
        check_test_budget --since "$t1_started" "$report_dir/t1-pytest.json" "$report_dir/t1-bun.json"
      fi
    done
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
