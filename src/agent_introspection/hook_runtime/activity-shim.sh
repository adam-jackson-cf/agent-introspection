#!/bin/sh

payload=$(cat) || exit 0
[ "$#" -eq 2 ] || exit 0

bin=${AGENT_INTROSPECTION_BIN-}
if [ -z "$bin" ]; then
  bin=$(command -v agent-introspection 2>/dev/null) || bin=
fi
if [ -z "$bin" ] && [ -n "${HOME-}" ]; then
  bin=$HOME/.local/bin/agent-introspection
fi
[ -n "$bin" ] && [ -x "$bin" ] || exit 0

if [ -z "${AGENT_INTROSPECTION_CONFIG-}" ] && [ -r "${0%/*}/config-path" ]; then
  AGENT_INTROSPECTION_CONFIG=$(cat "${0%/*}/config-path") || AGENT_INTROSPECTION_CONFIG=
  [ -n "$AGENT_INTROSPECTION_CONFIG" ] && export AGENT_INTROSPECTION_CONFIG
fi

(
  trap '' HUP
  printf '%s' "$payload" | nohup "$bin" hook "$1" "$2"
) </dev/null >/dev/null 2>&1 &

exit 0
