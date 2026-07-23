#!/usr/bin/env bash
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$script_dir/session-context-runtime.sh" codex-app-server "$@"
