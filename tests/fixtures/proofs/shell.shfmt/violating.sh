#!/usr/bin/env bash
set -euo pipefail

greet()
{
	case "$1" in
	-h|--help) printf 'usage: greet NAME\n' ;;
	*) printf 'hello %s\n' "$1" ;;
	esac
}

[[ $# -gt 0 ]] &&
	greet "$1"
