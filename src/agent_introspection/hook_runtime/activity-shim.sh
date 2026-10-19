#!/bin/sh
# Activity-hook shim: activity-shim.sh PRODUCER EVENT
#
# Reads the whole hook envelope from stdin, hands it to a detached
# `agent-introspection hook PRODUCER EVENT` through a pipe (no temporary file), and
# exits 0 at once with no output, so the harness never waits on, or takes a
# decision from, the normalizer. The envelope is held only in this shell's memory
# and the pipe; `printf` is a shell builtin, so it never appears in a process list.
#
# The CLI is found at $AGENT_INTROSPECTION_BIN, else on PATH, else at
# ~/.local/bin/agent-introspection. When none exists the hook does nothing.
#
# Unless AGENT_INTROSPECTION_CONFIG is already set, a `config-path` file beside this
# shim names the config file the CLI reads (`harness configure` writes it for a
# non-default `--config`).

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
