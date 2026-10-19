#!/usr/bin/env bash
# install-signoz.sh: the opt-in, guided install of a new self-hosted SigNoz on this
# machine for Agent Introspection. Run it only when the user asks for a new SigNoz.
#
# Usage: install-signoz.sh [--dry-run] [--dir PATH] [--tag TAG]
#
#   --dry-run   run every check and print the plan; change nothing
#   --dir PATH  install directory, absent or empty
#               (default ${XDG_DATA_HOME:-$HOME/.local/share}/agent-introspection/signoz)
#   --tag TAG   SigNoz release for the signoz/signoz image (default DEFAULT_TAG below)
#
# It never reconfigures an existing SigNoz. It refuses (exit 3) when:
# - `agent-introspection signoz detect` finds a SigNoz or ClickHouse, or any of ports
#   4317, 4318, 8123, and 8080 in use (without that command, its own port probe);
# - any SigNoz or ClickHouse container (running or stopped), a Compose project named
#   signoz, or a signoz-* volume exists, which `docker compose up` would reuse.
# A missing requirement (including Docker Compose older than COMPOSE_MINIMUM below), a
# Docker context that is not this machine, or a non-empty --dir stops it with exit 1;
# a bad argument with exit 2.
#
# Then it downloads the pinned foundryctl (checked against the SHA-256 pinned below),
# writes casting.yaml pinning the SigNoz and collector images, runs `foundryctl gauge`
# and `forge`, copies docker-compose.clickhouse-http.yaml next to the generated
# compose.yaml, starts the stack with both files, waits for ClickHouse on
# 127.0.0.1:8123, and says that the config.toml [signoz] defaults reach it. It prints no
# secret.
# The override publishes ClickHouse HTTP, OTLP, and the UI on 127.0.0.1 only, so this
# SigNoz serves this machine alone until the user widens it (the override says how).
#
# Sources, read 2026-10-13:
# - https://signoz.io/docs/install/docker/ : the self-hosted Docker install is Foundry
#   (foundryctl). `foundryctl forge` writes pours/deployment/compose.yaml, and
#   `docker compose up -d` starts it. Ports: UI 8080, OTLP gRPC 4317, OTLP/HTTP 4318.
# - https://github.com/SigNoz/signoz/blob/v0.145.0/deploy/README.md : the install.sh
#   script and the deploy/ Compose files are deprecated; v0.145.0 no longer ships them.
# - https://github.com/SigNoz/signoz/releases/tag/v0.145.0 : latest stable SigNoz.
# - https://github.com/SigNoz/signoz-otel-collector/releases/tag/v0.144.13 : latest
#   stable collector, the image Foundry's `latest` default resolved to on this date.
# - https://github.com/SigNoz/foundry/releases/tag/v0.3.0 : foundryctl archives and
#   checksums. At that tag, docs/reference/casting-file.md sets images with
#   spec.<molding>.spec.image, and docs/examples/docker/compose shows the generated
#   stack: ClickHouse is service and container signoz-telemetrystore-clickhouse-0-0
#   (HTTP 8123 inside the Compose network only), the collector is service `ingester`
#   (4317 and 4318 on every interface), and the UI is service signoz-signoz-0 (8080
#   on every interface).
# - https://docs.docker.com/reference/compose-file/merge/ : the `!override` tag the
#   override uses to replace a port list needs Docker Compose 2.24.4 or later.

set -euo pipefail

readonly DEFAULT_TAG="v0.145.0"
readonly COLLECTOR_TAG="v0.144.13"
readonly FOUNDRY_VERSION="v0.3.0"
readonly COMPOSE_MINIMUM="2.24.4"
readonly PORTS="4317 4318 8123 8080"
# The generated services the override names: ClickHouse, the collector, the UI.
readonly OVERRIDE_SERVICES="signoz-telemetrystore-clickhouse-0-0 ingester signoz-signoz-0"
readonly OVERRIDE="docker-compose.clickhouse-http.yaml"
readonly CLICKHOUSE_PING="http://127.0.0.1:8123/ping"
readonly WAIT_SECONDS=300

# SHA-256 of each foundryctl v0.3.0 archive, from the release's checksums file.
foundry_sha256() {
  case "$1" in
    darwin_amd64) echo "8012e7854454102501fc9d8ab2f4d02ce210bc00b4613e0801b82cae04cf548c" ;;
    darwin_arm64) echo "2b3e8d7324044ff49cda9774d120bdb03558cff7b82142f2073153fdaeddf0ec" ;;
    linux_amd64) echo "7555f91ee87a9d0d517009fc859ef64be76ad8b045446b2fe4360b896dc5838d" ;;
    linux_arm64) echo "97d5017b97f163f466216f0680bdaf80d561180ed47cd675544fda283ebc284e" ;;
    *) return 1 ;;
  esac
}

usage() {
  printf 'Usage: %s [--dry-run] [--dir PATH] [--tag TAG]\n' "$(basename "$0")"
}

die() {
  printf 'install-signoz: %s\n' "$*" >&2
  exit 1
}

refuse() {
  printf 'install-signoz: refused: an existing SigNoz or its leftovers were found:\n' >&2
  printf '  - %s\n' "$@" >&2
  printf '%s\n' "Agent Introspection never reconfigures an existing SigNoz. Connect to it" \
    "instead: agent-introspection signoz detect, then agent-introspection setup --dry-run." >&2
  exit 3
}

dry_run=false
dir="${XDG_DATA_HOME:-$HOME/.local/share}/agent-introspection/signoz"
tag="$DEFAULT_TAG"
while (($#)); do
  case "$1" in
    --dry-run) dry_run=true ;;
    --dir)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 2; }
      dir="$2"
      shift
      ;;
    --tag)
      [[ $# -ge 2 && -n "$2" ]] || { usage >&2; exit 2; }
      tag="$2"
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'install-signoz: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done
if [[ ! "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  printf 'install-signoz: --tag must look like %s\n' "$DEFAULT_TAG" >&2
  exit 2
fi
[[ "$dir" == /* ]] || dir="$PWD/$dir"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
deployment="$dir/pours/deployment"

platform() {
  local os arch
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in
    x86_64 | amd64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *) die "unsupported architecture: $arch" ;;
  esac
  case "$os" in
    darwin | linux) ;;
    *) die "unsupported operating system: $os (macOS and Linux only)" ;;
  esac
  printf '%s_%s\n' "$os" "$arch"
}

# Whether dotted version $1 is at least $2; a suffix such as -desktop.1 is ignored.
version_at_least() {
  local -a have want
  local i
  IFS=. read -r -a have <<<"${1%%[!0-9.]*}"
  IFS=. read -r -a want <<<"$2"
  for i in 0 1 2; do
    ((${have[i]:-0} > ${want[i]:-0})) && return 0
    ((${have[i]:-0} < ${want[i]:-0})) && return 1
  done
  return 0
}

require_tools() {
  local tool compose
  for tool in docker curl tar install; do
    command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
  done
  command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 \
    || die "sha256sum or shasum is required"
  docker compose version >/dev/null 2>&1 || die "the Docker Compose v2 plugin (docker compose) is required"
  compose="$(docker compose version --short 2>/dev/null)" || compose=""
  version_at_least "${compose#v}" "$COMPOSE_MINIMUM" \
    || die "Docker Compose $COMPOSE_MINIMUM or later is required for $OVERRIDE (found ${compose:-no version}); update Docker"
  docker info >/dev/null 2>&1 || die "the Docker daemon does not answer (docker info); start it first"
  [[ -f "$script_dir/$OVERRIDE" ]] || die "$OVERRIDE is missing next to this script"
}

# Agent Introspection runs only against a SigNoz on this machine.
require_local_docker() {
  local host="${DOCKER_HOST:-}"
  if [[ -z "$host" ]]; then
    host="$(docker context inspect --format '{{.Endpoints.docker.Host}}' 2>/dev/null)" \
      || die "could not read the Docker context endpoint"
  fi
  case "$host" in
    unix://* | npipe://* | tcp://127.0.0.1:* | tcp://localhost:* | "tcp://[::1]:"*) ;;
    *) die "the Docker daemon is at $host, not on this machine" ;;
  esac
}

# Bash's /dev/tcp: a refused loopback connection fails at once.
port_in_use() {
  (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

# Appends a finding per SigNoz, ClickHouse, or busy port that `signoz detect` reports,
# or returns 1 when the CLI or its `signoz detect` command is unavailable.
findings_from_detect() {
  local report port
  command -v agent-introspection >/dev/null 2>&1 || return 1
  report="$(agent-introspection signoz detect 2>/dev/null)" || return 1
  [[ "$report" == *'"signoz_present":'* ]] || return 1
  if [[ "$report" == *'"signoz_present":true'* ]]; then
    findings+=("agent-introspection signoz detect: a SigNoz or ClickHouse is present")
  fi
  for port in $PORTS; do
    if [[ "$report" == *"\"$port\":true"* ]]; then
      findings+=("port $port on this machine is in use (agent-introspection signoz detect)")
    fi
  done
}

findings_from_ports() {
  local port
  for port in $PORTS; do
    if port_in_use "$port"; then
      findings+=("port $port on this machine is in use")
    fi
  done
}

# Containers in any state, a Compose project, and volumes `docker compose up` would
# reuse. A listing that fails stops the install rather than passing the check.
findings_from_docker() {
  local containers projects volumes name
  containers="$(docker ps -a --format '{{.Names}} {{.Image}}')" || die "docker ps -a failed"
  projects="$(docker compose ls --all --quiet)" || die "docker compose ls failed"
  volumes="$(docker volume ls --quiet)" || die "docker volume ls failed"
  while IFS=' ' read -r name _; do
    [[ -n "$name" ]] && findings+=("container $name (running or stopped)")
  done < <(printf '%s\n' "$containers" | grep -iE 'signoz|clickhouse' || true)
  if printf '%s\n' "$projects" | grep -qx 'signoz'; then
    findings+=("Docker Compose project signoz")
  fi
  while IFS= read -r name; do
    [[ -n "$name" ]] && findings+=("Docker volume $name")
  done < <(printf '%s\n' "$volumes" | grep -E '^signoz-' || true)
}

check_existing() {
  findings=()
  if ! findings_from_detect; then
    printf 'agent-introspection signoz detect is unavailable; probing ports %s directly.\n' "$PORTS"
    findings_from_ports
  fi
  findings_from_docker
  if ((${#findings[@]})); then
    refuse "${findings[@]}"
  fi
}

check_dir() {
  if [[ -e "$dir" ]]; then
    [[ -d "$dir" ]] || die "--dir $dir exists and is not a directory"
    [[ -z "$(ls -A "$dir")" ]] || die "--dir $dir is not empty; choose a new or empty directory"
  fi
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

download_foundry() {
  local archive="foundry_${target}.tar.gz" work actual
  work="$(mktemp -d)"
  curl -fsSL --retry 3 -o "$work/$archive" \
    "https://github.com/SigNoz/foundry/releases/download/$FOUNDRY_VERSION/$archive"
  actual="$(sha256_of "$work/$archive")"
  if [[ "$actual" != "$expected_sha256" ]]; then
    rm -rf "$work"
    die "SHA-256 mismatch for $archive; nothing was installed"
  fi
  tar -xzf "$work/$archive" -C "$work"
  mkdir -p "$dir/bin"
  install -m 0755 "$work/foundry_${target}/bin/foundryctl" "$dir/bin/foundryctl"
  rm -rf "$work"
}

write_casting() {
  cat >"$dir/casting.yaml" <<EOF
# Written by Agent Introspection's install-signoz.sh. Images are pinned; Foundry
# $FOUNDRY_VERSION pins ClickHouse, its keeper, and Postgres.
apiVersion: v1alpha1
kind: Installation
metadata:
  name: signoz
spec:
  deployment:
    flavor: compose
    mode: docker
  signoz:
    spec:
      image: signoz/signoz:$tag
  ingester:
    spec:
      image: signoz/signoz-otel-collector:$COLLECTOR_TAG
EOF
}

forge() {
  (
    cd "$dir"
    bin/foundryctl gauge -f casting.yaml --no-ledger --no-updater
    bin/foundryctl forge -f casting.yaml -p pours --no-ledger --no-updater
  )
  [[ -f "$deployment/compose.yaml" ]] || die "foundryctl forge did not write $deployment/compose.yaml"
  local service
  for service in $OVERRIDE_SERVICES; do
    grep -q "^  $service:" "$deployment/compose.yaml" \
      || die "the generated compose.yaml has no $service service for $OVERRIDE"
  done
}

copy_override() {
  cp "$script_dir/$OVERRIDE" "$deployment/$OVERRIDE"
}

compose_up() {
  docker compose -f "$deployment/compose.yaml" -f "$deployment/$OVERRIDE" up -d
}

wait_for_clickhouse() {
  local waited=0
  until [[ "$(curl -fsS --noproxy '*' --max-time 2 "$CLICKHOUSE_PING" 2>/dev/null)" == "Ok." ]]; do
    ((waited < WAIT_SECONDS)) || die "ClickHouse did not answer $CLICKHOUSE_PING within ${WAIT_SECONDS}s; see: docker compose -f $deployment/compose.yaml ps"
    sleep 5
    waited=$((waited + 5))
  done
}

step_number=0
step() {
  local description="$1" action="$2"
  step_number=$((step_number + 1))
  printf '%d. %s\n' "$step_number" "$description"
  if ! "$dry_run"; then
    "$action"
  fi
}

print_next() {
  cat <<EOF

The [signoz] defaults of Agent Introspection's config.toml reach this SigNoz, so it
needs no [signoz] table: clickhouse_url = "http://127.0.0.1:8123" (the CLI and the
dashboard both read it), clickhouse_user = "default", and otlp_endpoint =
"http://127.0.0.1:4318". This ClickHouse's default user has no password and is
published on loopback only, so no password key is needed.

SigNoz listens on 127.0.0.1 only: the UI at http://127.0.0.1:8080, OTLP on 4317 and
4318, and ClickHouse HTTP on 8123. An additional machine that sends hook records here
needs 4318 reachable; the comments in $deployment/$OVERRIDE say how.

Next:
  agent-introspection setup --dry-run
  agent-introspection setup
EOF
}

main() {
  target="$(platform)"
  expected_sha256="$(foundry_sha256 "$target")" || die "no pinned foundryctl checksum for $target"
  require_tools
  require_local_docker
  check_existing
  check_dir

  printf 'SigNoz %s (collector %s, Foundry %s) into %s\n' "$tag" "$COLLECTOR_TAG" "$FOUNDRY_VERSION" "$dir"
  printf 'Checks passed: Docker on this machine; no SigNoz, ClickHouse, or leftovers; ports %s free.\n' "$PORTS"
  if "$dry_run"; then
    printf 'Plan (dry run: nothing is changed):\n'
  fi
  step "Download foundryctl $FOUNDRY_VERSION ($target), check its pinned SHA-256, install it to $dir/bin" download_foundry
  step "Write $dir/casting.yaml (signoz/signoz:$tag, signoz/signoz-otel-collector:$COLLECTOR_TAG)" write_casting
  step "foundryctl gauge and forge: $deployment/compose.yaml" forge
  step "Copy $OVERRIDE next to it (ClickHouse HTTP 8123, OTLP 4317 and 4318, and the UI 8080 on 127.0.0.1 only)" copy_override
  step "docker compose -f $deployment/compose.yaml -f $deployment/$OVERRIDE up -d" compose_up
  step "Wait up to ${WAIT_SECONDS}s for ClickHouse at $CLICKHOUSE_PING" wait_for_clickhouse
  print_next
}

main
