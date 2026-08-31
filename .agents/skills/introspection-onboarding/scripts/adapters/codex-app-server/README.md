# Codex app-server adapter

Codex Desktop emits documented global Codex hooks. The installer registers command hooks at `<codex-root>/hooks.json`, where `<codex-root>` is `$CODEX_HOME` when it is set to a non-empty absolute path and otherwise `~/.codex`. Trust state is at `<codex-root>/config.toml`. The command hooks are:

- `SessionStart` with source `startup`, `resume`, `clear`, or `compact`
- `SessionEnd` with reason `other`

Codex requires interactive trust before user hooks run. Restart or begin a future Desktop session after completing that trust flow.

`adapter.py` accepts one hook envelope on standard input and forwards only the native `session_id`, lifecycle event, synchronous UTC hook time, and existing absolute `cwd` to the shared session-context runtime. The native session ID is the canonical `codex-app-server` identity.

Desktop project changes made mid-thread are unsupported. The adapter does not read `transcript_path`, prompts, responses, or other arbitrary hook payload fields, and it does not resolve Git state itself; the shared runtime performs workspace attribution.
