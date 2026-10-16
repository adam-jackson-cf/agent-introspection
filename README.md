# Agent Introspection

**See how your coding agents use models and work, across Claude Code, Codex, and omp,
and turn recurring problems into fixes you approve.**

![Version](https://img.shields.io/badge/version-0.1.0-blue.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg?style=flat-square)
![Dashboard](https://img.shields.io/badge/dashboard-bun%20%2B%20react-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-agent--introspection-orange.svg?style=flat-square)
![Platform](https://img.shields.io/badge/platform-local%20self--hosted%20SigNoz-purple.svg?style=flat-square)

Everything runs on your own desktop: your agents send telemetry to a SigNoz on the same
machine, and Agent Introspection turns it into a local dashboard and a list of
evidence-backed improvement proposals. It never applies a proposal without your
explicit request.

## Quick Install

Prerequisites: macOS or Linux, Python 3.12+ with [`uv`](https://docs.astral.sh/uv/),
[Bun](https://bun.sh), Docker with Compose 2.24.4+, and `~/.local/bin` on your `PATH`.
An existing self-hosted SigNoz is optional; setup can install one.

```bash
git clone https://github.com/adam-jackson-cf/agent-introspection.git
cd agent-introspection
uv sync
uv tool install --force --reinstall .   # rerun after every update
agent-introspection --help
```

## Quick Start

Open the repository in Claude Code or Codex and ask:

> Use the introspection-onboarding skill to set up Agent Introspection on this machine.

The [`introspection-onboarding`](.agents/skills/introspection-onboarding/SKILL.md) skill
finds or installs SigNoz, writes your config, builds the facts store, connects each
harness, and schedules the sync. It shows a dry run before changing any file, backs up
what it changes, and asks before each decision that is yours, such as installing SigNoz
or exporting prompts.

When it finishes, `agent-introspection doctor` shows `"passed": true` and the dashboard
at <http://127.0.0.1:4173> shows your agent sessions within a few minutes of use.

To do it by hand, follow [Setup](docs/references/setup.md). If anything looks wrong,
run `agent-introspection doctor` and follow the `fix` on each `fail` or `warn`.

## What Agent Introspection Does

It collects the same signals from every agent harness on the machine, so one person can
compare model usage and agent process across them, find what goes wrong, and act on it.

```text
harnesses ──OTLP──> SigNoz ──sync──> facts store ──┬──> dashboard
(Claude Code,       (local)          (sanitized,   │
 Codex, omp)                          ClickHouse)  └──> findings ──> proposals ──> your decision
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
  - [`introspection-operations`](.agents/skills/introspection-operations/SKILL.md):
    check health; change signals, projections, or harnesses.
  - [`introspection-improvement`](.agents/skills/introspection-improvement/SKILL.md):
    findings → proposal → decision → evaluation.
  - [`python-conventions`](.agents/skills/python-conventions/SKILL.md): this
    repository's Python rules and quality gates.

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
- **Finding → proposal**: a finding is a recurring problem the facts prove; a proposal
  is one intervention for it, which you approve or reject and which is evaluated after
  it is applied.

## Go Deeper

| To…                                    | Read                                                                                                                                                          |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Set up by hand                         | [Setup](docs/references/setup.md), [SigNoz Connection](docs/references/signoz-connection.md)                                                                  |
| Run or debug a command                 | [CLI Reference](docs/references/cli-reference.md)                                                                                                             |
| Read the dashboard and its blind spots | [Dashboard Companion](docs/references/dashboard-companion.md), [Data Gaps](docs/references/data-gaps.md), [Signal Catalog](docs/references/signal-catalog.md) |
| Add a producer or harness              | [Producer Onboarding](docs/references/producer-onboarding.md), [Producer Capabilities](docs/references/producer-capabilities.md)                              |
| Understand the data pipeline           | [Facts Store](docs/references/facts-store.md), [Hook Events](docs/references/hook-events.md)                                                                  |
| Run the improvement loop               | [Proposal Lifecycle](docs/references/proposal-lifecycle.md)                                                                                                   |
| Contribute code                        | [Development](docs/references/development.md)                                                                                                                 |

[docs/README.md](docs/README.md) indexes every doc.
