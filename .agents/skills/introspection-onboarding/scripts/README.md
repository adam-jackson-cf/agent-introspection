# Activity hooks

Activity hooks close dashboard signals a harness's own telemetry lacks (tool argument
identity, targets, and gate-bypass flags; Claude Code interrupts, steers, reasoning
tokens, subagents, and failure text; omp approvals, retries, steers, and its prompt
event). The record contract and derivations are in
[`docs/hook-events.md`](../../../../docs/hook-events.md). Project attribution needs no
hook: `facts sync` reads each harness's session store (`session_stores.toml`).

```text
scripts/
├── activity-shim.sh             # detaches `agent-introspection hook PRODUCER EVENT`
└── adapters/
    ├── claude-code/             # install_activity.py: registers six Claude Code hooks
    └── omp/                     # activity.ts: omp extension
```

```text
harness hook ─> activity-shim.sh PRODUCER EVENT ─> agent-introspection hook PRODUCER EVENT (detached)
  ─> ~/.local/share/agent-introspection/hook-inbox/<event_id>.json
  ─> facts sync ─> introspection.hook_events
```

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
- Installed copies live in `~/.local/lib/agent-introspection/activity-hooks-v1/`; hooks
  never run a repository path.

Codex needs no activity hook: its signals are native. Install commands are in each
adapter's README and the onboarding skill's harness configuration workflow.
