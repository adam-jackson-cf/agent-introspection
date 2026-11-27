#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_files=()
while IFS= read -r -d '' file; do
  python_files+=("$file")
done < <(uv run python scripts/list-python-scope.py)

uv run ruff format --force-exclude "${python_files[@]}"
uv run ruff check --fix --force-exclude "${python_files[@]}"
bun run --cwd dashboard format
