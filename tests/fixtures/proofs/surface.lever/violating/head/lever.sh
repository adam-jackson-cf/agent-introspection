#!/usr/bin/env bash
set -euo pipefail

data=""
evidence=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --data-dir) data=$2; shift 2 ;;
    --evidence-dir) evidence=$2; shift 2 ;;
    *) exit 2 ;;
  esac
done
mkdir -p "$evidence"
if [ "${FAKE_LEVER_DOCTOR:-}" = broken ]; then
  echo "LEVER DOCTOR FAILED: the fixture doctor is broken" >&2
  exit 3
fi
mkdir -p "$data"
echo driven >"$data/state"
cp "$data/state" "$evidence/state"
: >"$evidence/ports"
