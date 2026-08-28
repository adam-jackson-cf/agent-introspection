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

bash scripts/check-quality-gate-parity.sh
uv sync --locked --dev

scope_file="$(mktemp)"
trap 'rm -f "$scope_file"' EXIT
uv run python scripts/python_quality_scope.py > "$scope_file"

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
done < <(git ls-files -z -- '*.ts' '*.tsx' '*.mts' '*.cts')

markdown_files=()
while IFS= read -r -d '' file; do
  [[ -f "$file" ]] || continue
  markdown_files+=("$file")
done < <(git ls-files -z -- '*.md' '*.markdown')

lint_files=("${python_files[@]}" "${typescript_files[@]}" "${markdown_files[@]}")
lint_hashes=()
if "$fix" && "$stage"; then
  for file in "${lint_files[@]}"; do
    lint_hashes+=("$(git hash-object "$file")")
  done
fi

if "$fix"; then
  uv run ruff format "${python_files[@]}"
  uv run ruff check --fix "${python_files[@]}"
else
  uv run ruff format --check "${python_files[@]}"
  uv run ruff check "${python_files[@]}"
fi

uv run mypy src
uv run pytest

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

if "$stage"; then
  for index in "${!lint_files[@]}"; do
    file="${lint_files[$index]}"
    if [[ "$(git hash-object "$file")" != "${lint_hashes[$index]}" ]]; then
      git add -- "$file"
    fi
  done
fi
