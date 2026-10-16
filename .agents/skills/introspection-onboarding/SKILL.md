---
name: "introspection-onboarding"
description: "Use when installing or reinstalling Agent Introspection, onboarding or re-checking a harness, exploring a producer it does not know yet, adding another machine, or proving capture end to end. Connects to the SigNoz on this desktop (installing a new one only when the user asks), installs the facts store and the scheduled sync, configures each harness's export, hooks, and prompt export (Claude Code, Codex, omp), assesses unknown producers (such as opencode) against the dashboard's capabilities, and diagnoses every step with `agent-introspection doctor`."
---

# Onboarding

## Rules

- One machine is the default: SigNoz, the facts store, the scheduled sync, and the
  harnesses all run on the user's own desktop. Sending records from more machines is
  an add-on (the additional machines workflow).
- Never reconfigure an existing SigNoz. Install a new one only when the user asks for
  it and `agent-introspection signoz detect` finds none: run
  `scripts/signoz/install-signoz.sh --dry-run` first, then without `--dry-run`.
  Applying the other files in `scripts/signoz/` is the user's decision.
- ClickHouse is reached over its HTTP interface at a local address (`[signoz]
clickhouse_url`, default `http://127.0.0.1:8123`). When `signoz detect` finds it
  unpublished, publishing it (the opt-in Compose override) is the user's decision.
- Run `agent-introspection doctor` (read-only) first at every step and follow each
  check's `fix`. Never weaken or skip a failing check.
- Never assume which harnesses to include; ask.
- Keep hostnames, addresses, and user names out of skills and docs: ask the user, use
  placeholders.
- Back up every file before changing it, show the diff first, and keep unrelated hooks
  and settings (for example Orca's and Codex Computer Use's).
- Never print or write a secret; check presence only.
- Confirm with the user before turning on prompt export: prompt text is then kept in
  SigNoz for its retention. The facts store never keeps it. For Claude Code,
  `--prompt-export` also sets `OTEL_LOG_ASSISTANT_RESPONSES=0`, because without it
  Claude Code exports each assistant response's text as well.

## Workflows

- [Setup](references/setup-workflow.md): the single-machine path, from finding SigNoz
  to the first sync, `doctor`, and validation; also a reinstall.
- [Harness configuration](references/harness-configuration-workflow.md): choose the
  harnesses, then configure each registered one with `harness configure`, its activity
  hooks, and opt-in prompt export.
- [Producer onboarding](references/producer-onboarding-workflow.md): identify, explore,
  and assess a producer that is not a harness yet, preview its parity cost with
  `harness impact`, and watch known producers for drift.
- [Validation](references/validation-workflow.md): `doctor` for configuration, then a
  live session end to end.
- [Additional machines](references/additional-machines-workflow.md): send another
  machine's harness telemetry and hook records to this host's
  collector.

Closing a signal gap with a new hook event, or adding a harness, is a code change: see
the `introspection-operations` change workflow.

## Output

- Steps run, with evidence (`doctor` and preflight results, install and sync results,
  validation rows).
- Files changed, with backup paths, and any step the user must do (for example
  restarting a harness).
- Per harness: what is captured, and any gap with its reason; per candidate producer,
  the signals enabling it would hide.
