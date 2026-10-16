# Additional machines workflow

An add-on to the single-machine setup. An additional machine runs harnesses but has no
SigNoz and no facts store. It sends its harness telemetry, and its hook and spool-status
records, over OTLP to the collector on the host (the machine that runs SigNoz and the
facts store). Finish the [setup workflow](setup-workflow.md) on the host first.

```text
additional machine                                         host
harness telemetry ──────────── OTLP ─────────────────────▶ collector ─▶ SigNoz ─▶ facts store
hook records ──────────────▶ spool ─▶ spool sync timer ─▶ collector (OTLP/HTTP logs)
```

## On the host

1. **Collector reachable.** The host's collector must accept connections from the
   additional machines on its OTLP ports (4318 for records; 4317 too if a harness
   exports over gRPC). On its own, SigNoz publishes 4317 and 4318 on all interfaces,
   but `docker-compose.clickhouse-http.yaml` binds them to `127.0.0.1`, and
   `install-signoz.sh` starts SigNoz with it. If the host's stack runs with that
   override, ask the user to delete its collector entry and recreate the stack (the
   override's header gives the command); its ClickHouse HTTP port stays on loopback.
   For another install, check the ports its Compose file publishes. The host's
   firewall must also allow them. Additional machines never reach ClickHouse.
2. **Harnesses enabled.** Parity is judged on the host: its `[harnesses] enabled` must
   include every harness any machine runs. After changing it, run
   `agent-introspection facts install` on the host so the list is loaded.

## On each additional machine

1. **CLI.** Install the revision the host runs, so record schemas and `spool sync`
   match: `<commit>` is the output of `git rev-parse HEAD` in the host's checkout (the
   one its CLI was installed from; the repository has no release tags). Install with
   `uv tool install --force git+<repository-url>@<commit>`, and again with the new
   commit whenever the host upgrades. `agent-introspection` must resolve at
   `~/.local/bin/agent-introspection`.
2. **Role and endpoints.** In `~/.config/agent-introspection/config.toml`, mark this
   machine as additional and point it at the host's collector:

   ```toml
   [machine]
   role = "additional"
   export_endpoint = "http://<collector-host>:4318"   # where its harnesses export

   [hooks]
   otlp_logs_endpoint = "http://<collector-host>:4318/v1/logs"   # where records go
   ```

   Both name the host collector's OTLP/HTTP port (usually 4318), never the gRPC port
   (4317), and they are the only settings that may name another machine; a URL never
   carries credentials. With `role = "additional"`, every command that needs ClickHouse
   (`facts` but `facts schedule status` and `remove`, `producers`, `candidates export`,
   `proposal draft`, and `proposal evaluate`) refuses to run with a message saying so
   (exit 10; `facts preflight` reports it as a failing `connection` check): run it on
   the host. `[signoz]`, `[dashboard]`, and `[labels]` are not used here. Records sent
   before the host's `facts install` created the loaders wait for the 3-day sweep or a
   `facts backfill` on the host.
   Set `[harnesses] enabled` to the harnesses this machine runs, so `doctor` checks
   those.

3. **Harnesses.** Run the
   [harness configuration workflow](harness-configuration-workflow.md) (export, hooks,
   opt-in prompt export) as on the host. On this machine,
   `harness configure` writes `[machine] export_endpoint` into each harness (Codex:
   `/v1/logs`, `/v1/traces`, and `/v1/metrics` under it), with the same backups and
   `--dry-run` preview. An exporter that already sends to the host's collector over
   the other transport (gRPC on the host's port 4317) is `satisfied` and left alone; one
   that still names this machine is changed.
4. **Timer.** Fetch the repository at the same commit (the CLI install in step 1
   leaves no clone): `git clone <repository-url> "$UNITS_SRC"`, then
   `git -C "$UNITS_SRC" checkout <commit>`, where `UNITS_SRC` is a fresh temporary
   directory such as `$(mktemp -d)/repo`. Install the
   systemd user units from its `.agents/skills/introspection-onboarding/scripts/` and
   enable them. The service runs `agent-introspection spool sync` (status record and
   OTLP drain), and the timer starts it every minute. Back up any
   existing unit with the same name first:

   ```sh
   mkdir -p ~/.config/systemd/user
   cd "$UNITS_SRC"
   cp .agents/skills/introspection-onboarding/scripts/agent-introspection-spool-sync.service \
      .agents/skills/introspection-onboarding/scripts/agent-introspection-spool-sync.timer ~/.config/systemd/user/
   systemctl --user daemon-reload
   systemctl --user enable --now agent-introspection-spool-sync.timer
   loginctl enable-linger "$USER"   # keeps the timer running with no login session
   ```

   The service sets `PATH` to include `~/.local/bin`, where `uv tool` installs the
   CLI. These units need systemd (Linux), and their names differ from the host's
   `agent-introspection-sync` units. No other scheduler is bundled: on another
   platform, agree with the user how to run `agent-introspection spool sync` every
   minute.

5. **Validate.**
   - `systemctl --user list-timers` shows the timer (on Linux),
     `agent-introspection spool sync` exits 0, and `agent-introspection spool status` shows the backlog draining to 0
     with no rejected files.
   - `agent-introspection doctor --live` passes. On an additional machine it leaves
     `signoz`, `schedule`, `prompt_labels`, `dashboard`, `drift`, and each harness's
     `telemetry` to the host (`info`, with the reason). It checks instead that the
     host's collector accepts connections (`host_collector`), that the hooks endpoint
     names it (`hooks_endpoint`), and that the timer runs (`spool_sync`, with
     `schedule_linger`); `collector_live` sends one empty OTLP/HTTP logs request to
     it. `export_settings` judges each harness against `[machine] export_endpoint`.
     On a platform without the bundled units, `spool_sync` is `info`: confirm the job
     agreed with the user runs.
   - On the host, `agent-introspection doctor` shows this machine's harnesses in
     `telemetry` once they have been used.
   - The [validation workflow](validation-workflow.md)'s live check: its probe record
     shows up in `hook_events` with this machine's host name.

## Done when

- Every additional machine's spool status is fresh in the host's Pipeline view, with a
  backlog that drains and no rejected files, and its sessions are attributed.
