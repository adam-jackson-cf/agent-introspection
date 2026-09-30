#!/usr/bin/env bash
set -euo pipefail

runner_pattern='scripts/run-ci-quality-gates\.sh'

# Match only active YAML: drop full-line and trailing comments before looking
# for a pre-commit hook entry and a workflow step run command that invoke the
# runner directly, not merely mention it.
active_config() {
  sed -E 's/(^|[[:space:]])#.*$//' "$1"
}

require_invocation() {
  local file="$1" key="$2"
  if ! active_config "$file" \
    | grep -Eq "^[[:space:]]*(-[[:space:]]+)?${key}:[[:space:]]+(bash[[:space:]]+)?(\./)?${runner_pattern}([[:space:]]|$)"; then
    printf '%s: no active %s invokes %s\n' "$file" "$key" 'scripts/run-ci-quality-gates.sh' >&2
    exit 1
  fi
}

require_invocation .pre-commit-config.yaml entry
require_invocation .github/workflows/ci-quality-gates.yml run
