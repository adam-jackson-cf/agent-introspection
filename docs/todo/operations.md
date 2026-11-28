# Machine operations

Track: priority: CLI data access / problem and friction surfacing

These items are machine state, not repository code. Each needs a person or agent on the named machine.

## Problem

1. **`mac-cf3` Codex sandbox.** `~/.codex/config.toml` has no `writable_roots` entry, so a Codex
   `workspace-write` sandbox cannot write `~/.cache/uv`. That failure (`Failed to initialize cache`,
   finding `aeee7e41`) was fixed this way on `mac-cf1` and `mac-cf2`.
2. **`mac-cf2` prompt labelling.** The `facts sync` log showed
   `OpenRouter refused the key or credit (HTTP 403)` (964 of 1,262 lines on 2026-10-28), and later
   `Jev resolved to an unexpected model`. The corrections and sentiment signals are starved while this
   lasts.
3. **`mac-cf2` leftover review sessions.** Three failed drafts left three `exported` review sessions in
   the workflow store, each with a 2,000,000-token `reserved` entry in `model_budget_ledger`. They do
   not block candidate selection.
4. **`mac-cf3` doctor.** The move from `mac-cf1` ended with "run `agent-introspection doctor` and fix
   each `fail` or `warn`". No record shows that run on `mac-cf3`.

## Evidence

- On `mac-cf3`, `grep -c writable_roots ~/.codex/config.toml` returned 0 on 2026-11-28.
- Items 2 and 3 were observed on `mac-cf2` in 2026-10 and 2026-11 and are not verifiable from `mac-cf3`.

## Proposed change

1. On `mac-cf3`, if Codex runs `uv` there, add:

   ```toml
   [sandbox_workspace_write]
   writable_roots = ["/Users/adamjackson/.cache/uv"]
   ```

2. On `mac-cf2`, check the OpenRouter key command and credit, rerun `facts sync --log`, and confirm new
   prompt labels land.
3. On `mac-cf2`, close the three review sessions and release their reservations once
   [proposal drafting portability](proposal-drafting-portability.md) adds a release path, or leave them.
4. On `mac-cf3`, run `agent-introspection doctor` and fix each `fail` or `warn`.

## Acceptance check

1. `codex sandbox -c 'sandbox_mode="workspace-write"' -- uv pip list` succeeds on `mac-cf3`.
2. `agent-introspection doctor` on `mac-cf2` shows the `labels` check passing, and the sync log has no
   new 403 lines.
3. The workflow store on `mac-cf2` has no `reserved` ledger entry without a matching outcome.
4. `agent-introspection doctor` on `mac-cf3` reports no `fail`.
