#!/usr/bin/env bash
set -euo pipefail
options="--port 1"
step install-cli 0 'uv tool install --force --reinstall .'
step setup-dry-run 0 "fixture-cli setup --dry-run $options"
step setup 0 "fixture-cli setup $options"
