# Hooks and Attribution

Use this reference to understand how sessions are attributed to projects, what the
activity hooks record, and where prompt labels come from. Install and validation are in
the [`introspection-onboarding`](../../.agents/skills/introspection-onboarding/SKILL.md)
skill; the hook record contract is [Hook Events](../hook-events.md).

## Project attribution

Every harness keeps its sessions on disk with the session ID its telemetry reports and
the working directory the session ran in. `facts sync` (`sessions.py`) reads only that
record from each new session file, as
[`session_stores.toml`](../../src/agent_introspection/session_stores.toml) defines for
each store (Claude Code `~/.claude/projects/`, Codex `<codex-root>/sessions/`, omp
`~/.omp/agent/sessions/`), resolves the working directory to its Git repository on this
machine (a linked worktree to its main repository), and stores one row per session in
`introspection.session_projects`: `attributed` with the project, or the reason it has
none (`non_git_workspace`, `missing_workspace`). Nothing is installed in any harness,
and message content is never read. Processed files are remembered in
`~/.local/share/agent-introspection/session-scan.json`;
`agent-introspection facts sessions --rescan` re-reads them all. Extra roots (for
example Orca's per-account Codex homes) go under `[sessions.roots]` in the config.

The dashboard joins facts to `introspection.session_project` (attributed sessions) by
session ID. The Pipeline view shows, per harness, the share of tasks with a project,
tasks whose session no store holds (runs that keep no session file), and unattributed
sessions by reason. A new harness is supported by adding its store to
`session_stores.toml`.

Findings, proposals, and review sessions live in the SQLite workflow store at
`~/.local/share/agent-introspection/introspection.sqlite3`. What the dashboard cannot
show, per harness and per signal, is listed in
[Dashboard Data Gaps](../dashboard-data-gaps.md).

## Activity hooks

A second set of hooks closes signals the producers' telemetry lacks: tool argument
identity, targets, and gate-bypass flags for Claude Code and omp; Claude Code
interrupts, steers, reasoning tokens, subagents, and failure text; omp steers,
approvals, and retries. Each hook hands its envelope to a detached
`agent-introspection hook <producer> <event>`, which writes a normalized record to
`~/.local/share/agent-introspection/hook-inbox`; `facts sync` stores it in
`introspection.hook_events`. No prompt, argument,
or output text is stored.

## Prompt labels

Prompt labels come from Jev (TypeSafe System One through OpenRouter's decisions API):
task type, whether the prompt corrects the agent's previous work and how, and
sentiment. The producers export prompt text to SigNoz (Claude Code
`OTEL_LOG_USER_PROMPTS=1`, Codex `[otel] log_user_prompt = true`, and an
`omp.user_prompt` log from the omp activity extension), and `facts sync` labels new
prompts in the app, retrying failures; `facts classify` runs it on demand. Prompt text
stays in SigNoz's tables for their retention; the facts store keeps only the labels
(`introspection.prompt_labels`). Labels feed the Intent and corrections view and the
repeated-correction findings.
