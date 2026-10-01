#!/usr/bin/env bash
set -euo pipefail

uv sync --locked --dev
uv run mypy src || true
bun install --cwd dashboard --frozen-lockfile
