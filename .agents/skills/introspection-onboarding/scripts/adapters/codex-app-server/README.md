# Codex app-server adapter

This surface needs three files because Codex Desktop launches an app-server executable rather than a command hook:

- `install.py` atomically installs the managed proxy, this adapter, the shared runtime, the immutable bundled-Codex path, and the user LaunchAgent `CODEX_CLI_PATH` override.
- `proxy.py` is the executable selected by `CODEX_CLI_PATH`. It forwards JSONL bytes and child behavior unchanged while structurally inspecting only approved protocol metadata.
- `adapter.sh` converts the proxy's four validated arguments to the shared five-field runtime contract.

## Protocol normalization

The proxy maps the first successful `thread/start` or `thread/started` to `session_start`; a known successful `thread/resume` or `thread/settings/updated` with changed `cwd` to `workspace_changed`; and a successful `thread/delete` or `thread/deleted` to `session_end`. It retains bounded `(thread.id, cwd)` state so same-workspace resumes are idempotent and unknown resumes or deletes fail closed.

It must preserve protocol bytes, backpressure, signals, stderr, argv, and child exit status. It may inspect only request ID, method, `thread.id`, and absolute `cwd`; prompts, responses, history, and titles are structurally skipped.

## Installation and attribution boundary

Run `install.py`, then restart Codex Desktop for the LaunchAgent environment to affect future launches. This producer stays unresolved until a fresh end-to-end proof establishes that protocol `thread.id` equals the SigNoz source correlation. It is a `codex-app-server` producer, never a Codex CLI or separate Desktop producer.
