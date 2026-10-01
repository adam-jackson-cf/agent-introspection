# Hooks and Attribution

Use this reference to understand what the harness hooks record, how sessions are
attributed to projects, where prompt labels come from, and why Codex Desktop
attribution is limited. Install, validation, and the record contract are in the
[`introspection-onboarding`](../../.agents/skills/introspection-onboarding/SKILL.md)
skill and [Hook Events](../hook-events.md).

## Project attribution

The session-context hooks installed in Claude Code, Codex, and omp write one JSON
record per session event into `~/.local/share/agent-introspection/session-context-inbox`:
the native session ID and the Git project it runs in, or a rejection such as a non-Git
workspace. The `com.adamjackson.agent-introspection.projects` LaunchAgent runs
`facts sync` every minute from the standalone `uv tool` install: it inserts the records
into `introspection.session_projects`, removes each file once ClickHouse has it, and
then promotes recurring failure signatures into workflow findings. The dashboard joins
facts to `introspection.session_project` (the latest project per session) by session
ID; the Pipeline view shows the attributed share of tasks per harness, rejections, and
the inbox backlog.

Findings, proposals, and review sessions live in the SQLite workflow store at
`~/.local/share/agent-introspection/introspection.sqlite3`. The retired scan ledger is
archived at `/Volumes/UGreen-External/archive/agent-introspection-2026-09-28/`.

What the dashboard cannot show, per harness and per signal, is listed in
[Dashboard Data Gaps](../dashboard-data-gaps.md).

## Activity hooks

A second set of hooks closes signals the producers' telemetry lacks: tool argument
identity, targets, and gate-bypass flags for Claude Code and omp; Claude Code
interrupts, steers, reasoning tokens, subagents, and failure text; omp steers,
approvals, and retries. Each hook hands its envelope to a detached
`agent-introspection hook <producer> <event>`, which writes a normalized record to the
same inbox; `facts sync` stores it in `introspection.hook_events`. No prompt, argument,
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

## Codex Desktop attribution

Codex Desktop attribution uses documented global `SessionStart` and `SessionEnd`
hooks. The hook emits only the native session ID, absolute workspace, lifecycle event,
and timestamp to the local session-context runtime. The shared runtime resolves the
canonical Git workspace; the hook never inspects or forwards prompts, responses, or
transcripts. User hooks require interactive review/trust and are never automatically
approved.

Codex's active configuration root is `$CODEX_HOME` when it is set to a non-empty
absolute path; otherwise it is `~/.codex`. Global hooks live at
`<codex-root>/hooks.json`, and trust state lives at `<codex-root>/config.toml`.

**Producer boundary is not qualified.** These global hooks also run in Codex CLI, and
their documented native envelope does not distinguish CLI from Desktop. A real CLI run
emitted a `codex-app-server` end record, which the retired scanner quarantined. The two
newly added global Desktop registrations were rolled back, preserving unrelated hooks,
trust settings, runtime bytes, and evidence. Do not install the Desktop adapter into a
configuration root shared with Codex CLI until an authoritative producer boundary is
available.

Changing projects within a live Codex Desktop thread is unsupported by design.
Attribution uses the workspace supplied at the thread's lifecycle boundary. Supporting
mid-thread project changes would require complex inspection or interposition of
app-server protocol traffic. This is considered rare, so the additional complexity is
not justified.
