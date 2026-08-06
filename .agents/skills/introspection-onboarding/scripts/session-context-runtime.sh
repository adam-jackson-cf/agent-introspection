#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C

contains_ascii_control() {
  case $1 in
    *[[:cntrl:]]*) return 0 ;;
    *) return 1 ;;
  esac
}
publish_rejection() {
  local reason_code=$1 rejection_id inbox tmp
  rejection_id=$(printf '%s\0%s\0%s\0%s\0%s' "$producer" "$session_id" "$event_type" "$occurred_at" "$reason_code" | shasum -a 256)
  rejection_id=${rejection_id%% *}
  inbox=${HOME:?HOME must be set}/.local/share/agent-introspection/session-context-inbox
  mkdir -p "$inbox"
  tmp=$(mktemp "$inbox/.${rejection_id}.XXXXXX")
  printf '{"rejection_id":"%s","producer":"%s","producer_surface":"session-context-inbox","correlation_id":"%s","lifecycle_event":"%s","occurred_at":"%s","reason_code":"%s","source_adapter":"session-context"}\n' "$rejection_id" "$(json_escape "$producer")" "$(json_escape "$session_id")" "$(json_escape "$event_type")" "$(json_escape "$occurred_at")" "$reason_code" >"$tmp"
  mv -f "$tmp" "$inbox/$rejection_id.json"
}
resolve_git_common_dir() {
  python3 - "$1" <<'PY'
import subprocess
import sys

workspace = sys.argv[1]
result = subprocess.run(
    ["git", "-C", workspace, "rev-parse", "--path-format=absolute", "--git-common-dir"],
    check=False,
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
)
if result.returncode != 0 or not result.stdout.endswith(b"\n"):
    print("Git common directory could not be resolved", file=sys.stderr)
    raise SystemExit(1)

common_dir = result.stdout[:-1]
if not common_dir or any(byte < 32 or byte == 127 for byte in common_dir):
    print("Git common directory must not contain ASCII control characters", file=sys.stderr)
    raise SystemExit(1)

sys.stdout.buffer.write(common_dir)
PY
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

json_escape() {
  local value=$1
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  value=${value//$'\b'/\\b}
  value=${value//$'\f'/\\f}
  value=${value//$'\n'/\\n}
  value=${value//$'\r'/\\r}
  value=${value//$'\t'/\\t}
  printf '%s' "$value"
}

if [ "$#" -ne 5 ]; then
  printf '%s\n' 'usage: session-context-runtime.sh PRODUCER SESSION_ID EVENT_TYPE OCCURRED_AT WORKSPACE' >&2
  exit 64
fi

producer=$1
session_id=$2
event_type=$3
occurred_at=$4
workspace=$5

case $producer in
  claude-code|codex-cli|codex-app-server|omp) ;;
  *) printf '%s\n' 'producer must be claude-code, codex-cli, codex-app-server, or omp' >&2; exit 64 ;;
esac
if [ -z "$session_id" ] || contains_ascii_control "$session_id"; then
  printf '%s\n' 'session ID must be non-empty and contain no ASCII control characters' >&2
  exit 64
fi
case $event_type in
  session_start|workspace_changed|session_end) ;;
  *) printf '%s\n' 'event type must be session_start, workspace_changed, or session_end' >&2; exit 64 ;;
esac
if contains_ascii_control "$occurred_at" || ! validate_occurred_at "$occurred_at"; then
  printf '%s\n' 'occurred-at must be an RFC 3339 timestamp with an offset and no ASCII control characters' >&2
  exit 64
fi
if [ -z "$workspace" ]; then publish_rejection missing_workspace; exit 64; fi
if contains_ascii_control "$workspace"; then publish_rejection invalid_workspace; exit 64; fi
case $workspace in
  /*) ;;
  *) publish_rejection invalid_workspace; exit 64 ;;
esac
if [ ! -d "$workspace" ]; then publish_rejection invalid_workspace; exit 64; fi
if ! git -C "$workspace" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  publish_rejection non_git_workspace
  exit 65
fi
common_dir=$(resolve_git_common_dir "$workspace") || { publish_rejection git_resolution_failed; exit 65; }
if [ ! -d "$common_dir" ]; then publish_rejection git_resolution_failed; exit 65; fi
case ${common_dir##*/} in
  .git) ;;
  *) publish_rejection git_resolution_failed; exit 65 ;;
esac
root=$(cd "$common_dir/.." && pwd -P) || { publish_rejection git_resolution_failed; exit 65; }
if contains_ascii_control "$root"; then publish_rejection git_resolution_failed; exit 65; fi
project_name=${root##*/}
project_id=$(printf 'git\0%s' "$root" | shasum -a 256)
project_id=${project_id%% *}
event_id=$(printf '%s\0%s\0%s\0%s\0%s' "$producer" "$session_id" "$event_type" "$occurred_at" "$root" | shasum -a 256)
event_id=${event_id%% *}

inbox=${HOME:?HOME must be set}/.local/share/agent-introspection/session-context-inbox
mkdir -p "$inbox"
tmp=$(mktemp "$inbox/.${event_id}.XXXXXX")
printf '{"event_id":"%s","producer":"%s","session_id":"%s","event_type":"%s","occurred_at":"%s","agent":{"project":{"id":"%s","name":"%s","root":"%s","kind":"git"}}}\n' \
  "$(json_escape "$event_id")" "$(json_escape "$producer")" "$(json_escape "$session_id")" "$(json_escape "$event_type")" "$(json_escape "$occurred_at")" "$(json_escape "$project_id")" "$(json_escape "$project_name")" "$(json_escape "$root")" >"$tmp"
mv -f "$tmp" "$inbox/$event_id.json"
