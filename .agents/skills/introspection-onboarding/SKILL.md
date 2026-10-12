---
name: "introspection-onboarding"
description: "Use when installing or reinstalling Agent Introspection, onboarding or re-checking a harness, or proving capture end to end. Installs, reinstalls, or validates Agent Introspection on the machine that hosts the self-hosted SigNoz (the SigNoz connection, the facts store, the launchd sync) and on every other machine that runs harnesses (CLI, OTLP endpoint, spool sync timer), including each harness's telemetry, hooks, and prompt export (omp, Codex, Claude Code). It never installs SigNoz."
---

# Onboarding

## Rules

- The facts store and SigNoz live on one host machine; Agent Introspection never
  installs, starts, or reconfigures SigNoz (without one, stop and say so). Every other
  machine only installs the CLI, hooks, and a spool sync timer, and sends records to
  the host's collector. Assume no container runtime; ask how ClickHouse is reached.
- Keep hostnames, addresses, and user names out of skills and docs: ask the user, use
  placeholders.
- Back up every file before changing it, show the diff first, and keep unrelated hooks
  and settings (for example Orca's and Codex Computer Use's).
- Never print or write a secret; check presence only.
- Confirm with the user before turning on prompt export: prompt text is then kept in
  SigNoz for its retention. The facts store never keeps it.

## Workflows

- [Setup](references/setup-workflow.md): connect to the local SigNoz, then install or
  reinstall in order (facts, harnesses, schedule, first sync); other machines follow
  its "Other machines" section.
- [Harness configuration](references/harness-configuration-workflow.md): choose the
  harnesses to include (never assume any), explore an unknown one, and configure each
  known one's OTLP export, session store, activity hooks, and prompt export.
- [Validation](references/validation-workflow.md): check configuration without running
  anything, then prove a live session end to end.

Closing a signal gap with a new hook event is a code change: see the
`introspection-operations` change workflow.

## Output

- Steps run, with evidence (preflight, install, sync results, validation rows).
- Files changed, with backup paths, and any step the user must do (for example
  restarting a harness).
- Per harness: what is captured, and any gap with its reason.
