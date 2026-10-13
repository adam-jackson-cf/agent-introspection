# Project Attribution

Use this reference to understand how sessions are attributed to projects. Activity hooks
and prompt labels are summarized at the end and specified in
[Hook Events](hook-events.md). Install and validation are in the
[`introspection-onboarding`](../../.agents/skills/introspection-onboarding/SKILL.md)
skill.

## Project attribution

Every harness keeps its sessions on disk with the session ID its telemetry reports and
the working directory the session ran in. The attribution scan (`sessions.py`, run by
`facts sync` and `spool sync`) reads only that record from each new session file, as
[`session_stores.toml`](../../src/agent_introspection/session_stores.toml) defines for
each store (Claude Code `~/.claude/projects/`, Codex `<codex-root>/sessions/`, omp
`~/.omp/agent/sessions/`), and resolves the working directory to its Git repository on
this machine (a linked worktree to its main repository). It spools one
`agent-introspection.session-project/1` record per session: `attributed` with the
project, or the reason it has none (`non_git_workspace`, `missing_workspace`). Nothing
is installed in any harness, and message content is never read.

`spool.py` ships the spooled records over OTLP to SigNoz, and the loader
`load_session_projects` copies them into `introspection.session_projects`, keeping the
newest `resolved_at` per session (route and delivery:
[Hook Events](hook-events.md#route), [Facts Store](facts-store.md)). Processed files
are remembered in `~/.local/share/agent-introspection/session-scan.json`;
`agent-introspection facts sessions --rescan` re-reads them all. Extra roots (for
example Orca's per-account Codex homes) go under `[sessions.roots]` in the config.

The dashboard joins facts to `introspection.session_project` (attributed sessions) by
session ID. The Pipeline view shows, per harness, the share of tasks with a project,
tasks whose session no store holds (runs that keep no session file), and unattributed
sessions by reason. A new harness is supported by adding its store to
`session_stores.toml`.

## Activity hooks

Hooks close signals the producers' telemetry lacks (tool argument identity, targets,
gate-bypass flags, interrupts, steers, subagents). Each normalized, text-free record is
spooled locally, shipped over OTLP to SigNoz, and loaded by `load_hook_events` into
`introspection.hook_events`. See [Hook Events](hook-events.md#route).

## Prompt labels

Jev labels each prompt's task type, correction kind, and sentiment from the prompt text
the producers export to SigNoz; only the labels are kept in
`introspection.prompt_labels`. See [Hook Events](hook-events.md#prompt-export).

## Related Docs

- [Hook Events](hook-events.md): the record contract, route, and delivery.
- [Data Gaps](data-gaps.md): what cannot be shown, per harness and signal.
- [Facts Store](facts-store.md): how loaded records become fact views.
