#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

python_files=()
python_list="$(mktemp)"
uv run python scripts/list-python-scope.py >"$python_list"
while IFS= read -r -d '' file; do
  python_files+=("$file")
done <"$python_list"
rm -f "$python_list"

uv run ruff format --force-exclude "${python_files[@]}"
uv run ruff check --fix --force-exclude "${python_files[@]}"
bun run --cwd dashboard format
