# Onboarding scripts

```text
scripts/
├── agent-introspection-spool-sync.service  # systemd user unit: `spool sync`
├── agent-introspection-spool-sync.timer    # runs it every minute (additional machines)
└── signoz/                      # opt-in SigNoz files; see signoz/README.md
    ├── install-signoz.sh        # guided install of a new SigNoz, only when asked
    ├── docker-compose.clickhouse-http.yaml  # ClickHouse HTTP, OTLP, and UI on loopback only
    └── otel-collector-opencode-filter.yaml  # drops opencode's plumbing spans
```

## Activity hooks

The activity hooks are not here: they ship in the CLI package
(`src/agent_introspection/hook_runtime/`), and `agent-introspection harness configure
<harness>` installs them, copying the runtime to
`~/.local/lib/agent-introspection/activity-hooks-v1/` so hooks never run a repository
path. [`docs/references/hook-events.md`](../../../../docs/references/hook-events.md)
holds the record contract, each harness's hooks, and how they are installed.

```text
harness hook ─> activity-shim.sh PRODUCER EVENT ─> agent-introspection hook PRODUCER EVENT (detached)
  ─> ~/.local/share/agent-introspection/hook-inbox/<event_id>.json
  ─> OTLP/HTTP logs POST ─> SigNoz ─> load_hook_events ─> introspection.hook_events
```

Records go to the collector's OTLP/HTTP logs URL (`/v1/logs` on `[signoz]
otlp_endpoint`, or on `[machine] export_endpoint` on an additional machine). The host
machine's scheduled `facts sync` drains the spool; each additional machine runs
`agent-introspection spool sync` from the systemd user timer here, as the onboarding
skill's additional machines workflow describes.

- The shim reads the whole envelope from stdin, pipes it to a detached
  `agent-introspection hook` (no temporary file), prints nothing, and exits 0, so a hook
  never blocks or decides anything for the harness. It finds the CLI at
  `$AGENT_INTROSPECTION_BIN`, on `PATH`, or at `~/.local/bin/agent-introspection`.
- **Read:** tool name and arguments, tool error text, prompt length, and (Claude
  `Stop`) the numeric usage and model of the turn's assistant messages.
- **Never stored:** prompt, argument, output, or error text. Records keep lengths, a
  16-hex argument hash, command head and allowlisted subcommand, home-redacted targets,
  and a digit-normalized failure signature.
- Failures are logged by exception type only to
  `~/.local/share/agent-introspection/hooks.log`.

Codex needs no activity hook: its signals are native. `agent-introspection doctor`
checks the installed hooks and shim.
