#!/usr/bin/env bash
set -euo pipefail

case "$1" in
  static) uv run mypy src || true ;;
  t1) uv run pytest -m "not boundary" ;;
  t2) uv run pytest -m boundary ;;
  hook) uv run pytest ;;
esac
