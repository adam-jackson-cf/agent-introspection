# Agent Introspection

**Read how your coding agents use models and work, across Claude Code, Codex, and omp,
from one CLI, and find where they fail and where you lose time.**

![Version](https://img.shields.io/badge/version-0.1.0-blue.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg?style=flat-square)
![Dashboard](https://img.shields.io/badge/dashboard-bun%20%2B%20react-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-agent--introspection-orange.svg?style=flat-square)
![Platform](https://img.shields.io/badge/platform-local%20self--hosted%20SigNoz-purple.svg?style=flat-square)

Everything runs on your own desktop: your agents send telemetry to a SigNoz on the same
machine, and Agent Introspection turns it into sanitized facts. The
`agent-introspection` CLI returns every dashboard view as JSON, so you or an agent can
find recurring failures and user friction without a browser. A local dashboard shows
the same views. A fix cycle that drafts and evaluates improvement proposals also
exists, but it is not the focus, and it never applies a proposal for you.

## Quick Install

Prerequisites: macOS or Linux, Python 3.12+ with [`uv`](https://docs.astral.sh/uv/),
[Bun](https://bun.sh), and `~/.local/bin` on your `PATH` (`uv tool update-shell` adds
it). An existing self-hosted SigNoz
is optional; the onboarding skill can install one, which needs Docker with Compose
2.24.4+.

```bash
git clone https://github.com/adam-jackson-cf/agent-introspection.git
cd agent-introspection
uv tool install --force --reinstall .   # rerun after every update, then setup
agent-introspection signoz detect       # find the SigNoz on this machine
agent-introspection setup --dry-run     # preview every step
agent-introspection setup               # config, facts, harnesses, sync, dashboard, doctor
```

SigNoz on other ports than its defaults (ClickHouse HTTP 8123, OTLP/HTTP 4318)? Add
`--clickhouse-url <url> --otlp-endpoint <url>` to both `signoz detect` and `setup`.
`setup` and `doctor` print their JSON report to stdout and a short summary to stderr:
the files changed (with backups), each check that is not a pass with its fix, and the
next steps.

`setup` writes the config, builds the facts store, configures Claude Code and Codex
(export settings and activity hooks; for omp too, pass
`--harness claude-code --harness codex --harness oh-my-pi`), schedules the
minute sync, starts the dashboard at <http://127.0.0.1:4173> (`--dashboard-port` picks another), and ends with
`agent-introspection doctor`. It stops at the first failing step and names it; every
step is idempotent, so rerun it to reinstall. Restart running agent sessions so they
pick up their settings.

## Quick Start

Open the repository in Claude Code or Codex and ask:

> Use the introspection-onboarding skill to set up Agent Introspection on this machine.

The [`introspection-onboarding`](.agents/skills/introspection-onboarding/SKILL.md) skill
finds or installs SigNoz, asks the decisions that are yours (which harnesses, prompt
export, the prompt-label key), shows `setup --dry-run`, and runs `setup` once you
confirm. Every file it changes is backed up first.

When it finishes, `agent-introspection doctor` shows `"passed": true` and the dashboard
shows your agent sessions within a few minutes of use. To do it by hand, run the
Quick Install commands; `agent-introspection setup --help` lists every option. If
anything looks wrong, run `agent-introspection doctor` and follow the `fix` on each
`fail` or `warn`.

## What Agent Introspection Does

It collects the same signals from every agent harness on the machine, so one person can
compare model usage and agent process across them and find what goes wrong.

```text
harnesses ──OTLP──> SigNoz ──sync──> facts store ──┬──> CLI (JSON) and dashboard
(Claude Code,       (local)          (sanitized,   │
 Codex, omp)                          ClickHouse)  └──> findings (recurring failures)
```

- `src/agent_introspection/`: the `agent-introspection` CLI, the ClickHouse fact views
  and signal registry (`facts_sql/`), and one profile per producer
  (`harness_profiles/`).
- `dashboard/`: a Bun + React app of question-led views over the facts, from KPIs down
  to single sessions.
- Workflow store: a SQLite file of findings, proposals, and their immutable history.
- `.agents/skills/`: skills that let a coding agent run the system:
  - [`introspection-onboarding`](.agents/skills/introspection-onboarding/SKILL.md):
    install, connect harnesses, assess new producers, add machines.
  - [`introspection-health`](.agents/skills/introspection-health/SKILL.md): check
    health before changes and before trusting findings or figures.
  - [`introspection-signals`](.agents/skills/introspection-signals/SKILL.md): change
    signals, projections, or harnesses.
  - [`introspection-analysis`](.agents/skills/introspection-analysis/SKILL.md):
    read what the dashboard shows from the terminal and route its themes.
  - [`introspection-improvement`](.agents/skills/introspection-improvement/SKILL.md):
    the fix cycle: findings → proposal → decision → evaluation.
  - [`python-conventions`](.agents/skills/python-conventions/SKILL.md): this
    repository's Python rules and quality gates.
  - [`testing-approach`](.agents/skills/testing-approach/SKILL.md): how to write,
    change, move, or delete tests here.

## Core Concepts

- **Producer / harness**: a producer is anything that sends telemetry to SigNoz; a
  harness is a producer the dashboard supports (Claude Code, Codex, omp).
- **Facts store**: the `introspection` database inside SigNoz's ClickHouse. It holds
  sanitized data only: no prompt, command, argument, or output text.
- **Signal**: one dashboard measure, defined once in
  [`signal_support.toml`](src/agent_introspection/facts_sql/signal_support.toml) with
  how each harness reaches it.
- **Capability**: a kind of producer data a signal needs, such as per-call token usage.
- **Harness profile**: a producer's TOML file: how to switch on its export and which
  capabilities it has.
- **Parity rule**: a signal is shown only if every enabled harness reaches it.
- **Activity hooks**: small hooks that record what a harness's own telemetry misses.
- **Prompt labels**: per-prompt task type, correction kind, and sentiment, classified
  by Jev; only the labels are stored.
- **Finding**: a recurring problem the facts prove, such as one failure class across
  several tasks.
- **Proposal**: in the fix cycle, one intervention for a finding. You approve or reject
  it, and it is evaluated after you apply it.

## Go Deeper

- [docs/features/](docs/features/pipeline.md): one page per dashboard view and CLI
  feature: what it does, how to reach it, and its gotchas.
- `agent-introspection --help`, and `--help` on each command: every command and
  option.
- [docs/README.md](docs/README.md) indexes every doc.

## Contributing

1. Activate [mise](https://mise.jdx.dev) in your shell, then run `mise trust` and
   `mise install` in the checkout, so `bun` resolves to the pinned 1.3.0. With another
   Bun, the T1 time budget finds no approved machine entry and only reports timing.
   `uv run python scripts/check-test-budget.py --print-machine-id` prints the id it
   looks up.
2. Docker is needed only for the gate's `install` mode.
3. Run `bash scripts/run-ci-quality-gates.sh hook` before you commit. The commit hook
   runs the same command, and every CI job runs the same entrypoint.
4. Fix what a tool can fix with `bash scripts/fix-quality-gates.sh`. The gate itself
   never changes a file.
