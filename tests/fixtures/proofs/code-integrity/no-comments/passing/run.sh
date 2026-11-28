#!/usr/bin/env bash
set -euo pipefail
# shellcheck disable=SC2034
count=${#BASH_SOURCE[@]}
echo "a # b" 'c # d' $#
cat <<EOF
# heredoc text is data
EOF
echo done#hash "$count"
