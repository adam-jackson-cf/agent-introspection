# Agent Introspection

**One local view of model usage and agent process across omp, Codex, and Claude Code,
with approval-gated proposals to fix recurring problems.**

![Version](https://img.shields.io/badge/version-0.1.0-blue.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg?style=flat-square)
![Dashboard](https://img.shields.io/badge/dashboard-bun%20%2B%20react-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-agent--introspection-orange.svg?style=flat-square)
![Platform](https://img.shields.io/badge/platform-local%20self--hosted%20SigNoz-purple.svg?style=flat-square)

> **Local only.** Agent Introspection runs on one machine, for one person, against a
> self-hosted SigNoz already installed on that same machine. It never installs or starts
> SigNoz, and it refuses any ClickHouse or collector address that is not local. See
> [SigNoz Connection](docs/references/signoz-connection.md) for the exact limits.

## Quick Install

Prerequisites:

- Python 3.12 or newer and `uv`
- A self-hosted SigNoz running on this machine, with ClickHouse 24.10 or later
- Local access to its ClickHouse: `docker exec` into its container, or its HTTP
  interface at a local address
- Bun, for the dashboard companion

Bootstrap the environment and check the SigNoz connection:

```bash
uv sync
mkdir -p ~/.config/agent-introspection
cp config.example.toml ~/.config/agent-introspection/config.toml  # then set [signoz]
uv run agent-introspection facts preflight
uv tool install --force --reinstall .   # standalone copy the launchd job and hooks run
```

`facts preflight` is read-only: it checks the ClickHouse version, the SigNoz columns the
projections read, grants, and recent producer data, and exits non-zero on any failure.

## Quick Start

Build the facts store, load the first minute of data, and open the dashboard:

```bash
agent-introspection facts install             # tables, loaders, views, snapshots, registry
agent-introspection facts backfill --days 90  # re-project everything SigNoz retains
agent-introspection facts sync                # projects + hook events, labels, findings, evaluations
agent-introspection facts status              # loader state and per-harness freshness
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard build
bun run --cwd dashboard start                 # http://127.0.0.1:4173
```

This materializes the harnesses' SigNoz telemetry into the `introspection` ClickHouse
database and serves the React companion on loopback. Its Pipeline view shows loader
freshness, harness parity, and project attribution. Harness hooks, prompt export, and the
every-minute `facts sync` schedule complete a full install; the `introspection-onboarding`
skill runs them in order.

## What Agent Introspection Does

Agent Introspection surfaces the same signals from every agent harness on this machine
(omp, Codex app-server/CLI/exec, and Claude Code) so one person can explore model usage
and agent process and find problems and improvements. It turns recurring problems into
intervention proposals with an approval history, and never applies a proposal itself:
approval records a decision only, and applying requires a separate explicit user request.

- `src/agent_introspection/`: the `agent-introspection` CLI; its `facts_sql/` holds the
  ClickHouse projections, views, and the signal support registry.
- `dashboard/`: an independent Bun + React package serving question-led views
  (outcome-first KPIs, trends, breakdowns, exemplars, session drill-down) over the facts.
- Workflow store: a SQLite file holding findings, proposals, review sessions, and their
  immutable event history.
- `.agents/skills/`: agent skills that install, operate, and improve the system (below).

### Agent skills

`.agents/skills/` holds four skills for coding agents working on or with this
repository. Each `SKILL.md` is a short index an agent loads when a request matches its
`description`; it links to step workflows under `references/`, which the agent opens
only as needed. The skills route to each other instead of overlapping.

| Skill                                                                            | Purpose                                                                                                                                                                               | Use when                                                                                                                                                                                         |
| -------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| [`introspection-onboarding`](.agents/skills/introspection-onboarding/SKILL.md)   | Connect to the local SigNoz, run the ordered install, and install and validate harness hooks and prompt export. Ships the hook runtime, shim, and per-harness adapters in `scripts/`. | Installing or reinstalling, onboarding a harness, closing a signal gap with a new hook, validating hook configuration or end-to-end capture.                                                     |
| [`introspection-operations`](.agents/skills/introspection-operations/SKILL.md)   | Keep the facts store healthy and change signals safely under the parity rule.                                                                                                         | Checking loaders, freshness, parity, the coverage grid, the hook inbox, or prompt labels; reinstalling or backfilling facts; adding, changing, closing, or excluding a signal or registry route. |
| [`introspection-improvement`](.agents/skills/introspection-improvement/SKILL.md) | Run the improvement loop: discover findings, draft a proposal, record the user's decision, mark it applied, evaluate the result.                                                      | Finding what goes wrong with agents, turning findings into proposals, reviewing or approving one, marking one applied, checking whether a fix worked.                                            |
| [`python-conventions`](.agents/skills/python-conventions/SKILL.md)               | Naming, package structure, object choice, and this repository's Python quality gates.                                                                                                 | Writing or refactoring Python in `src/`, `tests/`, `scripts/`, or `.agents` adapters.                                                                                                            |

```text
onboarding ──> operations (health gate) ──> improvement loop
  install        facts + signal changes        findings → proposal → decision → evaluation
```

## Core Concepts

- **Harness / producer**: an agent runtime whose telemetry is read: omp, Codex
  (app-server, CLI, exec), or Claude Code.
- **Facts store**: the `introspection` database inside SigNoz's ClickHouse, with
  sanitized spans and logs, normalized fact views, and 91-day snapshots. Prompt,
  command, argument, and output text never enter it.
- **Signal and registry**: a dashboard measure, defined once in
  [`signal_support.toml`](src/agent_introspection/facts_sql/signal_support.toml) with the
  route that reaches it for each harness.
- **Parity rule**: every signal must be reached by every harness it can exist for; a
  signal some harness cannot produce is excluded with a reason, never shown as zero.
- **Session-context and activity hooks**: harness hooks that write records to a local
  inbox. The first attributes sessions to Git projects; the second closes signals the
  producers' own telemetry lacks.
- **Prompt labels**: Jev classifications of each prompt's task type, correction kind,
  and sentiment. Only labels are stored.
- **Finding**: a recurring problem the facts already prove: a failure cluster or a
  repeated correction.
- **Proposal**: one evidence-backed intervention for a finding, moving
  `pending` → `approved` or `rejected` → `applying` → `applied`, then evaluated once.

## Go Deeper

- [docs/references/development.md](docs/references/development.md): quality gates,
  pre-commit, and the dashboard package commands.
- [docs/references/signoz-connection.md](docs/references/signoz-connection.md): local-only
  limits, docker and HTTP connection modes, and required ClickHouse grants.
- [docs/references/cli-reference.md](docs/references/cli-reference.md): every
  `agent-introspection` command and its output contract.
- [docs/references/facts-store.md](docs/references/facts-store.md): how producer
  telemetry becomes fact views, snapshots, and registry tables.
- [docs/references/dashboard-companion.md](docs/references/dashboard-companion.md): server
  binding, environment variables, and how views aggregate harnesses.
- [docs/references/hooks-and-attribution.md](docs/references/hooks-and-attribution.md):
  project attribution, activity hooks, prompt labels, and Codex Desktop limits.
- [docs/references/proposal-lifecycle.md](docs/references/proposal-lifecycle.md):
  candidate selection, drafting with Codex, success metrics, applying, and evaluation.
- [docs/hook-events.md](docs/hook-events.md): the hook event record contract.
- [docs/dashboard-data-gaps.md](docs/dashboard-data-gaps.md): what the dashboard cannot
  show, per harness and signal.
- [docs/dashboard-v3-plan.md](docs/dashboard-v3-plan.md): the dashboard plan and findings
  log.
