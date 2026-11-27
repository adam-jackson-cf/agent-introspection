#!/usr/bin/env bash
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
CLICKHOUSE_IMAGE=${FRESH_INSTALL_CLICKHOUSE_IMAGE:-clickhouse/clickhouse-server:25.8}
SYSTEM_PATH=/usr/bin:/bin:/usr/sbin:/sbin

die() {
  printf 'sandbox: %s\n' "$*" >&2
  exit 1
}

need_root() {
  [ -n "${1-}" ] && [ -f "$1/state/sandbox" ] || die "not a sandbox root: ${1-<none>}"
  cd "$1" && pwd -P
}

free_port() {
  python3 -I -c 'import socket; s = socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'
}

write_fake() {
  cat >"$ROOT/fakebin/$1" <<EOF
#!/bin/sh
printf '%s %s\n' "$1" "\$*" >>"$ROOT/state/calls.log"
$2
EOF
  chmod 755 "$ROOT/fakebin/$1"
}

make_fakes() {
  mkdir -p "$ROOT/fakebin"
  cat >"$ROOT/fakebin/launchd.py" <<'LAUNCHD'
import os, plistlib, signal, subprocess, sys
from pathlib import Path

jobs = Path(os.environ["FRESH_INSTALL_SANDBOX"]) / "state/launchd"
jobs.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]


def stop(label: str) -> None:
    marker = jobs / label
    if marker.exists():
        pid = int(marker.read_text() or 0)
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
        marker.unlink()


if args[:1] in (["list"], ["print"]):
    label = args[-1].rsplit("/", 1)[-1]
    sys.exit(0 if (jobs / label).exists() else 113)
if args[:1] == ["bootout"]:
    stop(args[1].rsplit("/", 1)[-1])
if args[:1] == ["bootstrap"]:
    job = plistlib.loads(Path(args[2]).read_bytes())
    stop(job["Label"])
    pid = 0
    if job.get("KeepAlive"):
        log = open(job.get("StandardOutPath", os.devnull), "ab")
        env = {"HOME": os.environ["HOME"], **job.get("EnvironmentVariables", {})}
        pid = subprocess.Popen(
            job["ProgramArguments"], cwd=job.get("WorkingDirectory"), env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        ).pid
    (jobs / job["Label"]).write_text(str(pid))
sys.exit(0)
LAUNCHD
  cat >"$ROOT/fakebin/systemd.py" <<'SYSTEMD'
import os, shlex, signal, subprocess, sys
from pathlib import Path

home = Path(os.environ["HOME"])
units = home / ".config/systemd/user"
state = Path(os.environ["FRESH_INSTALL_SANDBOX"]) / "state/systemd"
state.mkdir(parents=True, exist_ok=True)
args = [arg for arg in sys.argv[1:] if arg != "--user"]
verb, names = (args[0] if args else ""), [a for a in args[1:] if not a.startswith("--")]
now = "--now" in args


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return value.replace("%%", "%")


def unit(name: str) -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for line in (units / name).read_text().splitlines():
        if "=" in line and not line.startswith(("[", "#")):
            key, value = line.split("=", 1)
            keys.setdefault(key, []).append(value)
    return keys


def stop(name: str) -> None:
    marker = state / f"{name}.active"
    if marker.exists():
        pid = int(marker.read_text() or 0)
        if pid:
            try:
                os.killpg(pid, signal.SIGTERM)
            except OSError:
                pass
        marker.unlink()


def start(name: str) -> None:
    stop(name)
    pid = 0
    if name.endswith(".service"):
        keys = unit(name)
        env = {"HOME": str(home)}
        for item in keys.get("Environment", []):
            key, value = unquote(item).split("=", 1)
            env[key] = value
        command = [word.replace("$$", "$").replace("%%", "%") for word in shlex.split(keys["ExecStart"][0])]
        log = open(state / f"{name}.log", "ab")
        pid = subprocess.Popen(
            command, cwd=keys["WorkingDirectory"][0].replace("%%", "%"), env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True,
        ).pid
    (state / f"{name}.active").write_text(str(pid))


for name in names:
    if verb in ("start", "restart") or (verb == "enable" and now):
        start(name)
    if verb in ("stop",) or (verb == "disable" and now):
        stop(name)
    if verb == "enable":
        (state / f"{name}.enabled").touch()
    if verb == "disable":
        (state / f"{name}.enabled").unlink(missing_ok=True)
    if verb == "is-active":
        active = (state / f"{name}.active").exists()
        print("active" if active else "inactive")
        sys.exit(0 if active else 3)
    if verb == "is-enabled":
        enabled = (state / f"{name}.enabled").exists()
        print("enabled" if enabled else "disabled")
        sys.exit(0 if enabled else 1)
sys.exit(0)
SYSTEMD
  write_fake launchctl "exec python3 -I \"$ROOT/fakebin/launchd.py\" \"\$@\""
  write_fake systemctl "exec python3 -I \"$ROOT/fakebin/systemd.py\" \"\$@\""
  write_fake docker 'echo "docker is disabled in the fresh-install sandbox" >&2; exit 1'
  write_fake claude "case \"\$1\" in --version) echo \"2.1.285 (Claude Code)\"; exit 0 ;; esac
echo \"claude is a stand-in in the fresh-install sandbox\" >&2; exit 1"
  write_fake codex "case \"\$1\" in --version) echo \"codex-cli 0.156.1\"; exit 0 ;; esac
echo \"codex is a stand-in in the fresh-install sandbox\" >&2; exit 1"
  write_fake omp "link=\"\$HOME/.omp/plugins/node_modules/agent-introspection-activity\"
case \"\$1 \$2\" in
  \"plugin link\") mkdir -p \"\${link%/*}\" && ln -sfn \"\$3\" \"\$link\" ;;
  \"plugin uninstall\") rm -f \"\$link\" ;;
  --version*) echo \"18.4.4\" ;;
esac
exit 0"
}

link_tools() {
  mkdir -p "$ROOT/tools"
  for name in uv uvx bun python3 python3.12 git curl; do
    found=$(command -v "$name" 2>/dev/null) || continue
    ln -sf "$found" "$ROOT/tools/$name"
  done
  [ -e "$ROOT/tools/uv" ] && [ -e "$ROOT/tools/bun" ] || die "uv and bun must be installed"
}

write_runner() {
  local uv_cache bun_cache caches=""
  uv_cache=$(uv cache dir 2>/dev/null || true)
  bun_cache=$(cd "$REPO/dashboard" && bun pm cache 2>/dev/null | tail -1 || true)
  if [ -n "$uv_cache" ]; then caches="UV_CACHE_DIR=\"$uv_cache\" "; fi
  if [ -n "$bun_cache" ]; then caches="${caches}BUN_INSTALL_CACHE_DIR=\"$bun_cache\" "; fi
  cat >"$ROOT/state/bashenv" <<'BASHENV'
source() {
  case "${1-}" in */shell-snapshots/*) return 0 ;; esac
  builtin source "$@"
}
BASHENV
  cat >"$ROOT/run" <<EOF
#!/bin/sh
# Runs a shell command line inside the sandbox, in the current directory: an empty
# home, the sandbox PATH, and no variable of the calling environment. It is also the
# CLAUDE_CODE_SHELL_PREFIX of \`sandbox.sh agent\`, which passes each command line as
# one argument. Usage: run 'COMMAND LINE'.
[ "\$#" -gt 0 ] || { echo "usage: run 'COMMAND LINE'" >&2; exit 2; }
exec env -i \\
  HOME="$ROOT/home" USER="\${USER-}" LOGNAME="\${LOGNAME-}" TERM="\${TERM-dumb}" \\
  LANG=en_US.UTF-8 TMPDIR="$ROOT/tmp" SHELL=/bin/bash BASH_ENV="$ROOT/state/bashenv" \\
  PATH="$ROOT/home/.local/bin:$ROOT/fakebin:$ROOT/tools:$SYSTEM_PATH" \\
  ${caches}FRESH_INSTALL_SANDBOX="$ROOT" \\
  /bin/bash -c "\$*"
EOF
  chmod 755 "$ROOT/run"
}

tunnel() {
  nohup ssh -N -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L "$2" "$DOCKER_SSH" >"$ROOT/state/$1-tunnel.log" 2>&1 &
  echo $! >"$ROOT/state/$1-tunnel.pid"
}

remote_docker() {
  local socket="/tmp/ai-fi-$STAMP.sock"
  tunnel docker "$socket:/var/run/docker.sock"
  echo "unix://$socket" >"$ROOT/state/docker_host"
  export DOCKER_HOST="unix://$socket"
  for _ in $(seq 20); do
    docker info >/dev/null 2>&1 && return 0
    sleep 1
  done
  die "Docker on $DOCKER_SSH did not answer through the forwarded socket; see $ROOT/state/docker-tunnel.log"
}

start_clickhouse() {
  local name="ai-fresh-install-${STAMP}" address port
  if [ -n "$DOCKER_SSH" ]; then remote_docker; fi
  docker info >/dev/null 2>&1 || die "Docker is not running (start Docker Desktop or OrbStack), or pass --no-clickhouse"
  docker run -d --name "$name" -p 127.0.0.1::8123 -e CLICKHOUSE_SKIP_USER_SETUP=1 \
    --label dev.agent-introspection.fresh-install="$ROOT" "$CLICKHOUSE_IMAGE" >/dev/null
  echo "$name" >"$ROOT/state/container"
  address=$(docker port "$name" 8123/tcp | head -1)
  port=${address##*:}
  if [ -n "$DOCKER_SSH" ]; then
    local remote=$port
    port=$(free_port)
    tunnel clickhouse "127.0.0.1:$port:127.0.0.1:$remote"
  fi
  CLICKHOUSE_URL="http://127.0.0.1:$port"
  for _ in $(seq 60); do
    curl -fsS "$CLICKHOUSE_URL/ping" >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS "$CLICKHOUSE_URL/ping" >/dev/null || die "ClickHouse did not start; see: docker logs $name"
  docker exec -i "$name" clickhouse-client --multiquery <"$HERE/signoz-stub.sql"
  echo "$CLICKHOUSE_URL" >"$ROOT/state/clickhouse_url"
  OTLP_PORT=$(free_port)
  local python
  python=$(uv python find 3.12 2>/dev/null || command -v python3)
  nohup "$python" -I "$HERE/otlp_stub.py" "$OTLP_PORT" "$CLICKHOUSE_URL" "$ROOT/state" \
    >"$ROOT/state/collector.log" 2>&1 &
  echo $! >"$ROOT/state/collector.pid"
  echo "http://127.0.0.1:$OTLP_PORT" >"$ROOT/state/otlp_endpoint"
}

cmd_create() {
  local clickhouse=true
  DOCKER_SSH=""
  STAMP=$(date +%Y%m%d-%H%M%S)
  ROOT="$REPO/.tmp/fresh-install/$STAMP"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --root)
        ROOT=$2
        shift 2
        ;;
      --no-clickhouse)
        clickhouse=false
        shift
        ;;
      --docker-ssh)
        DOCKER_SSH=$2
        shift 2
        ;;
      *) die "unknown option $1" ;;
    esac
  done
  [ ! -e "$ROOT" ] || die "$ROOT exists"
  local changes head
  changes="$(git -C "$REPO" status --porcelain)"
  if [ -n "$changes" ]; then
    printf 'sandbox: note: uncommitted changes are not in the sandbox (it clones HEAD)\n' >&2
  fi
  mkdir -p "$ROOT/state" "$ROOT/home" "$ROOT/tmp"
  ROOT=$(cd "$ROOT" && pwd)
  echo "$STAMP" >"$ROOT/state/sandbox"
  (cd "$ROOT" && pwd -P) >"$ROOT/state/root"
  : >"$ROOT/state/calls.log"
  git clone --quiet --no-local "$REPO" "$ROOT/repo"
  head="$(git -C "$REPO" rev-parse HEAD)"
  git -C "$ROOT/repo" checkout --quiet --detach "$head"
  mkdir -p "$ROOT/home/.claude" "$ROOT/home/.codex"
  make_fakes
  link_tools
  write_runner
  if $clickhouse; then start_clickhouse; fi
  ln -sfn "$ROOT" "$REPO/.tmp/fresh-install/latest" 2>/dev/null || true
  echo "$ROOT"
}

cmd_run() {
  ROOT=$(need_root "${1-}")
  shift
  cd "$ROOT/repo" && "$ROOT/run" "$@"
}

STEP=0
FAILED=0
step() {
  local name=$1 expect=$2 code=0
  shift 2
  STEP=$((STEP + 1))
  local log
  log="$ROOT/state/verify/$(printf '%02d' "$STEP")-$name.log"
  (cd "$ROOT/repo" && "$ROOT/run" "$@") >"$log" 2>"${log%.log}.err" || code=$?
  if [ "$expect" = any ] || [ "$code" = "$expect" ]; then
    printf '  ok    %-28s exit %s\n' "$name" "$code"
  else
    printf '  FAIL  %-28s exit %s (expected %s): %s\n' "$name" "$code" "$expect" "${log%.log}.{log,err}"
    FAILED=$((FAILED + 1))
  fi
}

check() {
  local name=$1
  shift
  if "$@" >/dev/null 2>&1; then
    printf '  ok    %s\n' "$name"
  else
    printf '  FAIL  %s\n' "$name"
    FAILED=$((FAILED + 1))
  fi
}

json_field() {
  python3 -I -c 'import json, sys; d = json.load(open(sys.argv[1])); print(eval(sys.argv[2]))' "$1" "$2"
}

fresh_hook_recorded() {
  local count
  count="$(clickhouse_count "introspection.hook_events WHERE session_id = 'fresh-1'")"
  test "$count" -ge 1
}

clickhouse_count() {
  local url
  url="$(cat "$ROOT/state/clickhouse_url")"
  curl -fsS "$url/" --data-binary "SELECT count() FROM $1"
}

dashboard_port() {
  cat "$ROOT/state/dashboard_port"
}

wait_for_dashboard() {
  local port
  port="$(dashboard_port)"
  for _ in $(seq 30); do
    curl -fsS -m 2 "http://127.0.0.1:$port/" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

dashboard_views_clean() {
  local view window since now port
  port="$(dashboard_port)"
  since="$(date -u -v-7d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -d '7 days ago' +%Y-%m-%dT%H:%M:%SZ)"
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  window="start=$since&end=$now"
  for view in pipeline cache effort tools friction guardrails provider recurrence intent interventions; do
    curl -fsS -m 30 "http://127.0.0.1:$port/api/view?view=$view&$window" >"$ROOT/state/verify/view-$view.json" || return 1
    python3 -I -c 'import json, sys; sys.exit(1 if json.load(open(sys.argv[1])).get("errors") else 0)' \
      "$ROOT/state/verify/view-$view.json" || {
      echo "view $view has errors" >&2
      return 1
    }
  done
}

cmd_verify() {
  ROOT=$(need_root "${1-}")
  [ -f "$ROOT/state/clickhouse_url" ] || die "verify needs the sandbox ClickHouse (created without --no-clickhouse)"
  local ch otlp home=$ROOT/home logs=$ROOT/state/verify
  ch=$(cat "$ROOT/state/clickhouse_url")
  otlp=$(cat "$ROOT/state/otlp_endpoint")
  rm -rf "$logs" && mkdir -p "$logs"
  local port
  port="$(free_port)"
  echo "$port" >"$ROOT/state/dashboard_port"
  local options="--clickhouse-url $ch --otlp-endpoint $otlp --backfill-days 1 --dashboard-port $port"
  echo "verify: $ROOT"
  step install-cli 0 'uv tool install --force --reinstall .'
  step cli-help 0 'agent-introspection --help'
  step signoz-detect any 'agent-introspection signoz detect'
  step setup-dry-run 0 "agent-introspection setup --dry-run $options"
  check "dry run wrote no config" test ! -e "$home/.config/agent-introspection/config.toml"
  check "dry run installed no hooks" test ! -e "$home/.claude/settings.json"
  step setup 0 "agent-introspection setup $options"
  local setup_log
  setup_log=$logs/$(printf '%02d' "$STEP")-setup.log
  check "setup: every step ran" json_field "$setup_log" 'd.get("stopped_at") is None or sys.exit(1)'
  check "config written" test -f "$home/.config/agent-introspection/config.toml"
  check "claude-code hooks registered" grep -q activity-shim.sh "$home/.claude/settings.json"
  check "codex export configured" grep -q otel "$home/.codex/config.toml"
  check "shim installed executable" test -x "$home/.local/lib/agent-introspection/activity-hooks-v1/activity-shim.sh"
  local system
  system="$(uname -s)"
  if [ "$system" = Darwin ]; then
    check "sync job bootstrapped (fake launchd)" grep -q "launchctl bootstrap .*dev.agent-introspection.sync" "$ROOT/state/calls.log"
    check "dashboard service bootstrapped (fake launchd)" grep -q "launchctl bootstrap .*dev.agent-introspection.dashboard" "$ROOT/state/calls.log"
  else
    check "sync timer enabled (fake systemd)" test -f "$ROOT/state/systemd/agent-introspection-sync.timer.enabled"
    check "dashboard service started (fake systemd)" test -f "$ROOT/state/systemd/agent-introspection-dashboard.service.active"
  fi
  check "dashboard built in the clone" test -f "$ROOT/repo/dashboard/dist/index.html"
  check "facts store created in ClickHouse" clickhouse_count introspection.hook_events
  step facts-verify 0 'agent-introspection facts verify'
  check "dashboard config names port $port" grep -q "^port = $port$" "$home/.config/agent-introspection/config.toml"
  check "dashboard service answers on $port" wait_for_dashboard
  check "every dashboard view queries cleanly" dashboard_views_clean
  step hook-event 0 "printf '%s' '{\"session_id\":\"fresh-1\",\"hook_event_name\":\"PreToolUse\",\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"git status\"},\"tool_use_id\":\"fresh-tool-1\",\"cwd\":\"/srv/work\"}' | /bin/sh \"\$HOME/.local/lib/agent-introspection/activity-hooks-v1/activity-shim.sh\" claude-code PreToolUse; sleep 3"
  step facts-sync 0 'agent-introspection facts sync'
  check "hook record reached the facts store" fresh_hook_recorded
  step doctor any 'agent-introspection doctor'
  local doctor_log
  doctor_log=$logs/$(printf '%02d' "$STEP")-doctor.log
  check "doctor: no failing check" json_field "$doctor_log" 'd["summary"].get("fail", 0) == 0 or sys.exit(1)'
  step setup-rerun 0 "agent-introspection setup $options"
  step remove-hooks 0 'agent-introspection harness remove-hooks claude-code'
  check "hooks removed, export kept" sh -c "! grep -q activity-shim.sh '$home/.claude/settings.json' && grep -q OTEL '$home/.claude/settings.json'"
  step dashboard-remove 0 'agent-introspection dashboard remove'
  step schedule-remove 0 'agent-introspection facts schedule remove'
  echo
  if [ "$FAILED" -eq 0 ]; then
    echo "verify: passed ($STEP steps); logs in $logs"
  else
    echo "verify: $FAILED failed; logs in $logs"
    if [ -f "$doctor_log" ]; then
      python3 -I - "$doctor_log" <<'SUMMARY' || true
import json, sys
for c in json.load(open(sys.argv[1]))["checks"]:
    if c["status"] in ("fail", "warn"):
        print(f"  doctor {c['status']}: {c['name']} ({c.get('harness', '')}): {c.get('detail', '')}")
SUMMARY
    fi
    return 1
  fi
}

cmd_agent() {
  ROOT=$(need_root "${1-}")
  shift
  local model=sonnet budget=5 allow_local=false
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --model)
        model=$2
        shift 2
        ;;
      --budget)
        budget=$2
        shift 2
        ;;
      --allow-local-signoz)
        allow_local=true
        shift
        ;;
      *) die "unknown option $1" ;;
    esac
  done
  command -v claude >/dev/null || die "claude is not installed"
  if ! $allow_local && { curl -fsS -m 2 http://127.0.0.1:8123/ping >/dev/null 2>&1 \
    || curl -sS -m 2 -o /dev/null http://127.0.0.1:4318/ 2>/dev/null; }; then
    die "a SigNoz answers on 127.0.0.1:8123 or :4318; stop it, or pass --allow-local-signoz"
  fi
  [ -f "$ROOT/state/clickhouse_url" ] || die "agent needs the sandbox ClickHouse"
  local prompt=$ROOT/state/agent-prompt.txt transcript=$ROOT/state/agent-transcript.jsonl
  local clickhouse_url otlp_endpoint prompt_text
  clickhouse_url="$(cat "$ROOT/state/clickhouse_url")"
  otlp_endpoint="$(cat "$ROOT/state/otlp_endpoint")"
  sed -e "s|{{CLICKHOUSE_URL}}|$clickhouse_url|g" \
    -e "s|{{OTLP_ENDPOINT}}|$otlp_endpoint|g" \
    "$HERE/agent-prompt.txt" >"$prompt"
  prompt_text="$(cat "$prompt")"
  echo "agent: a fresh Claude Code session installs from $ROOT/repo; transcript: $transcript"
  local unset=() name
  for name in $(env | grep -oE '^(OTEL_[A-Za-z0-9_]*|CLAUDECODE|CLAUDE_CODE_(SESSION_ID|CHILD_SESSION|ENTRYPOINT|ENHANCED_TELEMETRY_BETA|MESSAGING_[A-Za-z0-9_]*))=' | tr -d =); do
    unset+=(-u "$name")
  done
  (
    cd "$ROOT/repo"
    env ${unset[@]+"${unset[@]}"} CLAUDE_CODE_ENABLE_TELEMETRY=0 \
      CLAUDE_CODE_SHELL_PREFIX="$ROOT/run" claude -p "$prompt_text" \
      --model "$model" --max-budget-usd "$budget" \
      --permission-mode bypassPermissions \
      --setting-sources project,local --strict-mcp-config --no-session-persistence \
      --disallowedTools Edit Write NotebookEdit WebFetch WebSearch Agent \
      --output-format stream-json --verbose >"$transcript"
  ) || echo "agent: the session exited non-zero" >&2
  python3 -I -c '
import json, sys
result = None
for line in open(sys.argv[1]):
    try:
        event = json.loads(line)
    except ValueError:
        continue
    # The first result is the report; later ones answer background-task notices.
    if event.get("type") == "result" and result is None:
        result = event
print(result.get("result", "") if result else "no result event; see the transcript")
' "$transcript" | tee "$ROOT/state/agent-report.md"
}

signal_sandbox_process() {
  local pid="$1" pattern="$2"
  shift 2
  if [ "${pid:-0}" -gt 0 ] && ps -p "$pid" -o command= 2>/dev/null | grep -qF -- "$pattern"; then
    kill "$@" 2>/dev/null || true
  fi
}

cmd_destroy() {
  ROOT=$(need_root "${1-}")
  local recorded
  recorded="$(cat "$ROOT/state/root" 2>/dev/null)" || recorded=""
  [ "$recorded" = "$ROOT" ] || die "refusing to delete $ROOT: not created as this sandbox"
  for job in "$ROOT"/state/launchd/* "$ROOT"/state/systemd/*.active; do
    [ -f "$job" ] || continue
    pid=$(cat "$job")
    signal_sandbox_process "$pid" "$ROOT" -TERM -- "-$pid"
  done
  if [ -f "$ROOT/state/collector.pid" ]; then
    pid=$(cat "$ROOT/state/collector.pid")
    signal_sandbox_process "$pid" otlp_stub.py "$pid"
  fi
  if [ -f "$ROOT/state/docker_host" ]; then
    export DOCKER_HOST
    DOCKER_HOST=$(cat "$ROOT/state/docker_host")
  fi
  if [ -f "$ROOT/state/container" ]; then
    docker rm -f "$(cat "$ROOT/state/container")" >/dev/null 2>&1 || true
  fi
  for tunnel_pid in "$ROOT"/state/*-tunnel.pid; do
    [ -f "$tunnel_pid" ] || continue
    pid=$(cat "$tunnel_pid")
    signal_sandbox_process "$pid" "ssh -N" "$pid"
  done
  if [ -f "$ROOT/state/docker_host" ]; then
    local socket
    socket="$(sed 's|^unix://||' "$ROOT/state/docker_host")"
    rm -f "$socket"
  fi
  rm -rf "$ROOT"
  echo "destroyed $ROOT"
}

LEVER_DOCTOR_MARKER="LEVER DOCTOR FAILED"
DOCTOR_WAIT_SECONDS=20

bounded() {
  local seconds=$1 pid waited=0
  shift
  BOUNDED_STATUS=1
  set -m
  "$@" >/dev/null 2>&1 &
  pid=$!
  set +m
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$waited" -ge "$seconds" ]; then
      kill -KILL -- "-$pid" 2>/dev/null || true
      wait "$pid" 2>/dev/null || true
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  BOUNDED_STATUS=0
  wait "$pid" || BOUNDED_STATUS=$?
}

lever_doctor() {
  local tool
  for tool in uv bun git curl python3 docker; do
    command -v "$tool" >/dev/null 2>&1 || {
      echo "$tool is not installed"
      return 0
    }
  done
  if [ -z "$DOCKER_SSH" ]; then
    bounded "$DOCTOR_WAIT_SECONDS" docker info
    if [ "$BOUNDED_STATUS" -ne 0 ]; then
      echo "Docker did not answer within $DOCTOR_WAIT_SECONDS s"
      return 0
    fi
  fi
}

lever_ports() {
  local file
  [ -f "$1/state/dashboard_port" ] && cat "$1/state/dashboard_port"
  for file in "$1/state/clickhouse_url" "$1/state/otlp_endpoint"; do
    [ -f "$file" ] && sed 's|.*:||; s|/.*||' "$file"
  done
  return 0
}

lever_finish() {
  local code=$?
  trap - EXIT
  if [ -n "${ROOT-}" ] && [ -f "$ROOT/state/sandbox" ]; then
    mkdir -p "$LEVER_EVIDENCE/state"
    cp -R "$ROOT/state/." "$LEVER_EVIDENCE/state/"
    lever_ports "$ROOT" >"$LEVER_EVIDENCE/ports"
    bash "$HERE/sandbox.sh" destroy "$ROOT" >>"$LEVER_EVIDENCE/destroy.log" 2>&1 || code=1
  fi
  echo "lever: exit $code; evidence in $LEVER_EVIDENCE" >&2
  exit "$code"
}

cmd_lever() {
  local data="" reason code=0 create=()
  LEVER_EVIDENCE=""
  DOCKER_SSH=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --data-dir)
        data=$2
        shift 2
        ;;
      --evidence-dir)
        LEVER_EVIDENCE=$2
        shift 2
        ;;
      --docker-ssh)
        DOCKER_SSH=$2
        create=(--docker-ssh "$2")
        shift 2
        ;;
      *) die "unknown option $1" ;;
    esac
  done
  [ -n "$data" ] && [ -n "$LEVER_EVIDENCE" ] \
    || die "usage: sandbox.sh lever --data-dir DIR --evidence-dir DIR [--docker-ssh USER@HOST]"
  mkdir -p "$LEVER_EVIDENCE"
  LEVER_EVIDENCE=$(cd "$LEVER_EVIDENCE" && pwd)
  reason=$(lever_doctor)
  if [ -n "$reason" ]; then
    printf '%s: %s\n' "$LEVER_DOCTOR_MARKER" "$reason" | tee "$LEVER_EVIDENCE/doctor.txt" >&2
    exit 3
  fi
  trap lever_finish EXIT
  cmd_create --root "$data" ${create[@]+"${create[@]}"} >"$LEVER_EVIDENCE/create.log" 2>&1
  bash "$HERE/sandbox.sh" verify "$ROOT" >"$LEVER_EVIDENCE/verify.log" 2>&1 || code=$?
  cd "$REPO"
  return "$code"
}

command=${1-}
[ -n "$command" ] || die "usage: sandbox.sh create|run|verify|agent|destroy|lever ..."
shift
case "$command" in
  create) cmd_create "$@" ;;
  run) cmd_run "$@" ;;
  verify) cmd_verify "$@" ;;
  agent) cmd_agent "$@" ;;
  destroy) cmd_destroy "$@" ;;
  lever) cmd_lever "$@" ;;
  *) die "unknown command $command" ;;
esac
