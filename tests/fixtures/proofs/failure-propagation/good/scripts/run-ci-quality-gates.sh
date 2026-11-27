#!/usr/bin/env bash
set -euo pipefail

uv sync --locked --dev
uv run mypy src
bun install --cwd dashboard --frozen-lockfile
