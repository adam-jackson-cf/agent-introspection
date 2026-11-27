#!/usr/bin/env bash
set -euo pipefail

user="$(whoami)"
printf 'hello %s\n' "$user"
