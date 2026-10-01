# Validation workflow

Prove a harness's capture works: first check configuration without running anything,
then, only when the user wants a live check, run one short session.

## Configuration check (no harness, hook, or query runs)

- Each changed file parses (JSON, TOML) and has a backup; print no values.
- Every introspection hook points at a managed path under
  `~/.local/lib/agent-introspection/session-context-runtime-v1/`, and the managed copies
  match the repo's `scripts/`.
- The settings in the harness configuration workflow's table are present for each
  harness in use, in every Codex root, and omp's `config.yml` lists `activity.ts`.
- `agent-introspection` resolves for the shim, and an OpenRouter key is present for
  `facts sync` (presence only).

## Live check

1. In each harness, start a fresh session, submit a harmless prompt, run one command
   that fails (for example reading a missing file), and let the turn finish. For Codex
   exec, run one `codex exec` prompt.
2. After the next minute's `facts sync` (or run it), check:
   - `introspection.session_projects FINAL` has the session with its project (a
     non-Git workspace appears only in `session_project_rejections`);
   - the prompt event (`user_prompt`, `codex.user_prompt`, `omp.user_prompt`) reached
     SigNoz with text, and `introspection.prompt_labels` has a `labelled` row for it,
     while `introspection.logs` holds no `prompt` key;
   - Claude Code and omp: a `hook_events` `tool_call` whose `tool_calls` row carries
     `command_head` and `arguments_hash`; Claude Code: `prompt_submitted`,
     `tool_failure`, and `turn_stop` with `reasoning_tokens`;
   - no hook record holds prompt, argument, or output text, and `hooks.log` has no new
     failures.
3. In the dashboard, the Pipeline coverage grid shows the harness's routes healthy, and
   the session drill-down shows the task's type and correction flag.

## Done when

- Each harness passes both checks, or each gap is explained (a signal no route can
  reach goes to the `introspection-operations` change workflow).
