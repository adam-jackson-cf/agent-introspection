# Fresh-install sandbox

Tests the install of the committed `HEAD` as a new machine would see it, without
touching this one. Two ways to run it:

- **`verify`** is the scripted path. It installs the CLI, runs `setup --dry-run` and
  `setup`, sends one Claude Code hook event through the installed shim, runs
  `facts sync`, checks that the record reached the facts store and that every dashboard
  view queries cleanly, runs `doctor` and a second `setup`, then removes the hooks, the
  dashboard service, and the schedule. Each step and check prints `ok` or `FAIL`.
- **`agent`** starts a fresh Claude Code session that knows nothing about how the
  branch was built. It installs from the clone by following the README and the
  onboarding skill as a new user would, and reports its steps, the friction it hit,
  and suggestions.

```bash
root=$(bash scripts/fresh-install/sandbox.sh create)   # needs Docker (Docker Desktop or OrbStack)
bash scripts/fresh-install/sandbox.sh verify "$root"
bash scripts/fresh-install/sandbox.sh agent "$root" --model sonnet --budget 5
bash scripts/fresh-install/sandbox.sh run "$root" 'agent-introspection doctor'
bash scripts/fresh-install/sandbox.sh destroy "$root"
```

Run `verify` and `agent` on separate sandboxes: each expects a machine where nothing
has been installed yet. Commit first, because the sandbox clones `HEAD`. It runs on
macOS and Linux; CI runs `verify` on Linux for every push and pull request (the
`fresh-install` job), since GitHub's macOS runners have no Docker.

## What the sandbox is

| Part       | What it is                                                                                                                                                                                                                                                                                                                                                  |
| ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `repo/`    | A clone of `HEAD`.                                                                                                                                                                                                                                                                                                                                          |
| `home/`    | An empty home with only `~/.claude` and `~/.codex`, as if each harness had run once.                                                                                                                                                                                                                                                                        |
| `run`      | Runs a command line with `HOME=home/`, a sandbox `PATH`, and no variable of the caller. It is also the agent session's `CLAUDE_CODE_SHELL_PREFIX`, so every command that session runs goes through it.                                                                                                                                                      |
| `fakebin/` | Stand-ins that log each call to `state/calls.log`. `launchctl` (macOS) starts a kept-alive job (the dashboard) from its plist and only records a timed job (the sync). `systemctl` (Linux) does the same from the unit files, unquoting them as systemd does. `omp` links its plugin. `docker` refuses. `claude` and `codex` print only a verified version. |
| `tools/`   | Links to this machine's `uv`, `bun`, Python, `git`, and `curl`.                                                                                                                                                                                                                                                                                             |
| ClickHouse | A disposable `clickhouse/clickhouse-server` container on a random loopback port, holding only the two SigNoz tables the facts store reads ([`signoz-stub.sql`](signoz-stub.sql)).                                                                                                                                                                           |
| Collector  | [`otlp_stub.py`](otlp_stub.py) on a random loopback port. It accepts OTLP/HTTP and writes log records into that ClickHouse, as SigNoz's collector would.                                                                                                                                                                                                    |

Nothing reaches this machine's launchd, systemd, Docker, or harness configs. The
network is not contained: the CLI's defaults name a SigNoz on this machine's loopback
ports, so `verify` always passes the sandbox's addresses, and `agent` refuses to start
while a SigNoz answers on `127.0.0.1:8123` or `:4318` (`--allow-local-signoz` overrides
it). Two more exceptions:

- Caches are shared to save time: `uv` and `bun` read and write their usual caches.
- The sandbox dashboard listens on the fixed port 4173. `verify` skips its dashboard
  checks when another dashboard already answers there.

The agent session:

- loads no user settings, so none of this machine's hooks or plugins run, and no MCP
  server;
- inherits no session identifiers or `OTEL_*` settings of the calling session, and
  runs with Claude Code's telemetry off;
- has no file-editing, web, or subagent tools;
- skips Claude Code's snapshot of your own shell, so your `PATH` never comes back.

Its transcript is `state/agent-transcript.jsonl` and its report `state/agent-report.md`.

## What it cannot show

- A real SigNoz. The stub has only the columns the facts store reads, and real producer
  telemetry never arrives, so `doctor`'s `telemetry` checks warn.
- A real launchd or systemd. The schedule never fires, and a stand-in reads the job
  files: their quoting is checked only as far as the stand-in follows the real one.
- A harness actually running. Hook delivery is tested with one synthetic event.
