#!/usr/bin/env bash
set -euo pipefail
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
case $session_id in
  ''|*$'\n'*|*$'\r'*) printf '%s\n' 'session ID must be a single non-empty line' >&2; exit 64 ;;
esac
case $event_type in
  session_start|workspace_changed|session_end) ;;
  *) printf '%s\n' 'event type must be session_start, workspace_changed, or session_end' >&2; exit 64 ;;
esac
case $occurred_at in
  ????-??-??T??:??:??*Z|????-??-??T??:??:??*+??:??|????-??-??T??:??:??*-??:??) ;;
  *) printf '%s\n' 'occurred-at must be an RFC 3339 timestamp with an offset' >&2; exit 64 ;;
esac
case $workspace in
  /*) ;;
  *) printf '%s\n' 'workspace must be an absolute path' >&2; exit 64 ;;
esac
[ -d "$workspace" ] || { printf '%s\n' 'workspace must name an existing directory' >&2; exit 64; }

common_dir=$(git -C "$workspace" rev-parse --path-format=absolute --git-common-dir 2>/dev/null) || {
  printf '%s\n' 'Git common directory could not be resolved' >&2
  exit 65
}
[ -d "$common_dir" ] || { printf '%s\n' 'Git common directory does not exist' >&2; exit 65; }
case ${common_dir##*/} in
  .git) ;;
  *) printf '%s\n' 'Git common directory must be named .git' >&2; exit 65 ;;
esac
root=$(cd "$common_dir/.." && pwd -P)
case $root in
  *[[:cntrl:]]*) printf '%s\n' 'Git root must not contain control characters' >&2; exit 65 ;;
esac
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
