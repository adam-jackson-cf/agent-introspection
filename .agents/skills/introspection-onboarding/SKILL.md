---
name: "introspection-onboarding"
description: "Install, reinstall, or validate Agent Introspection on this machine against the self-hosted SigNoz already running here: the SigNoz connection, the facts store, the launchd sync, and each harness's telemetry, hooks, and prompt export (omp, Codex, Claude Code). USE WHEN installing or reinstalling Agent Introspection, onboarding or re-checking a harness, or proving capture end to end. Local only: it never installs SigNoz."
---

# Onboarding

## Rules

- Local only: Agent Introspection runs on this machine against a SigNoz already
  installed here. It never installs, starts, or reconfigures SigNoz; without one, stop
  and say so. Assume no container runtime; ask how ClickHouse is reached.
- Back up every file before changing it, show the diff first, and keep unrelated hooks
  and settings (for example Orca's and Codex Computer Use's).
- Never print or write a secret; check presence only.
- Confirm with the user before turning on prompt export: prompt text is then kept in
  SigNoz for its retention. The facts store never keeps it.
- Never install the Codex Desktop (`codex-app-server`) session-context hooks into a
  Codex root that Codex CLI also uses: they cannot tell the two apart.

## Workflows

- [Setup](references/setup-workflow.md): connect to the local SigNoz, then install or
  reinstall in order (facts, harnesses, schedule, first sync).
- [Harness configuration](references/harness-configuration-workflow.md): choose the
  harnesses to include (never assume any), explore an unknown one, and configure each
  known one's OTLP export, session-context hook, activity hooks, and prompt export.
- [Validation](references/validation-workflow.md): check configuration without running
  anything, then prove a live session end to end.

Closing a signal gap with a new hook event is a code change: see the
`introspection-operations` change workflow.

## Output

- Steps run, with evidence (preflight, install, sync results, validation rows).
- Files changed, with backup paths, and any step the user must do (for example
  trusting a Codex hook).
- Per harness: what is captured, and any gap with its reason.
