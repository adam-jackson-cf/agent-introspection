#!/usr/bin/env bash
set -euo pipefail

fix=false
stage=false

while (($#)); do
  case "$1" in
    --fix) fix=true ;;
    --stage) stage=true ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
  shift
done

if "$stage" && ! "$fix"; then
  echo "--stage requires --fix" >&2
  exit 2
fi

uv sync --locked --dev

if "$fix"; then
  lint_files=()
  lint_hashes=()
  while IFS= read -r -d '' file; do
    lint_files+=("$file")
    lint_hashes+=("$(git hash-object "$file")")
  done < <(find src tests -type f -name '*.py' -print0)

  uv run ruff check . --fix
  uv run ruff format .
else
  uv run ruff check .
  uv run ruff format --check .
fi

uv run mypy src
uv run pytest
bash scripts/check-quality-gate-parity.sh

if "$stage"; then
  for index in "${!lint_files[@]}"; do
    file="${lint_files[$index]}"
    if [[ "$(git hash-object "$file")" != "${lint_hashes[$index]}" ]]; then
      git add -- "$file"
    fi
  done
fi
