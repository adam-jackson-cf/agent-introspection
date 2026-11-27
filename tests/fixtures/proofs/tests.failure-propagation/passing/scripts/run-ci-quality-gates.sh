#!/usr/bin/env bash
set -euo pipefail

case "$1" in
  static) uv sync --locked --dev ;;
  t1) uv run pytest -m "not boundary" ;;
  t2) uv run pytest -m boundary ;;
  hook)
    uv sync --locked --dev
    uv run pytest
    ;;
esac
