---
name: "introspection-onboarding"
description: "Use when installing or reinstalling Agent Introspection, onboarding or re-checking a harness, exploring a producer it does not know yet, adding another machine, or proving capture end to end. Connects to the SigNoz on this desktop (installing a new one only when the user asks), runs `agent-introspection setup` (config, facts store, each harness's export and activity hooks, the scheduled sync, the dashboard service) for Claude Code and Codex by default and omp on request, assesses unknown producers (such as opencode) against the dashboard's capabilities, and diagnoses every step with `agent-introspection doctor`."
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
- Install with `agent-introspection setup`: ask the user's decisions, run it with
  `--dry-run` and show the result, then run it after they confirm. Rerun it to
  reinstall; the single commands it runs are for one step only.
- Run `agent-introspection doctor` (read-only) first at every step and follow each
  check's `fix`. Never weaken or skip a failing check.
- Ask which harnesses to include; Claude Code and Codex are the default, omp is
  opt-in (`setup --harness oh-my-pi`, its registry key).
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

- [Setup](references/setup-workflow.md): the single-machine path: install the CLI,
  find SigNoz, `setup --dry-run`, then `setup`, the first sync, and validation; also a
  reinstall.
- [Harness configuration](references/harness-configuration-workflow.md): choose the
  harnesses, and configure one on its own with `harness configure` (export settings,
  activity hooks, opt-in prompt export), such as adding omp later.
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

- Steps run, with evidence (the `setup` report, `doctor` results, sync results,
  validation rows).
- Files changed, with backup paths, and any step the user must do (for example
  restarting a harness).
- Per harness: what is captured, and any gap with its reason; per candidate producer,
  the signals enabling it would hide.
