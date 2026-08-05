#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf '%s\n' 'usage: codex-app-server.sh THREAD_ID WORKSPACE EVENT_TYPE OCCURRED_AT' >&2
  exit 64
}

contains_ascii_control() {
  case $1 in
    *[[:cntrl:]]*) return 0 ;;
    *) return 1 ;;
  esac
}

validate_occurred_at() {
  python3 - "$1" <<'PY'
import re
import sys
from datetime import datetime

timestamp = sys.argv[1]
if not re.fullmatch(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
    timestamp,
):
    raise SystemExit(1)

try:
    datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
except ValueError:
    raise SystemExit(1)
PY
}

[ "$#" -eq 4 ] || usage

thread_id=$1
workspace=$2
event_type=$3
occurred_at=$4

if [ -z "$thread_id" ] || contains_ascii_control "$thread_id"; then
  usage
fi
case $event_type in
  session_start|workspace_changed|session_end) ;;
  *) usage ;;
esac
if contains_ascii_control "$occurred_at" || ! validate_occurred_at "$occurred_at"; then
  usage
fi
if contains_ascii_control "$workspace"; then
  usage
fi
case $workspace in
  /*) ;;
  *) usage ;;
esac
[ -d "$workspace" ] || usage

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$script_dir/session-context-runtime.sh" codex-app-server "$thread_id" "$event_type" "$occurred_at" "$workspace"
