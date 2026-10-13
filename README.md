# Agent Introspection

**One local view of model usage and agent process across omp, Codex, and Claude Code,
with approval-gated proposals to fix recurring problems.**

![Version](https://img.shields.io/badge/version-0.1.0-blue.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg?style=flat-square)
![Dashboard](https://img.shields.io/badge/dashboard-bun%20%2B%20react-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-agent--introspection-orange.svg?style=flat-square)
![Platform](https://img.shields.io/badge/platform-local%20self--hosted%20SigNoz-purple.svg?style=flat-square)

> **Local, and one machine by default.** Agent Introspection runs on a person's own
> desktop against a self-hosted SigNoz on that same machine, and refuses any ClickHouse
> or collector address in its `[signoz]` and `[dashboard]` settings that is not local.
> It never reconfigures an existing SigNoz; installing a new one is opt-in, through a
> guided script. Additional machines that run harnesses are an add-on: one set to
> `[machine] role = "additional"` sends its telemetry and hook records to the host's
> collector, and `harness configure` and `doctor` follow that role (see the onboarding
> skill's
> [additional machines workflow](.agents/skills/introspection-onboarding/references/additional-machines-workflow.md)).
> [SigNoz Connection](docs/references/signoz-connection.md) has the exact limits.

## Quick Install

Prerequisites:

- macOS or Linux (the scheduled sync is a launchd agent or a systemd user timer)
- Python 3.12 or newer and `uv`
- A self-hosted SigNoz on this machine with ClickHouse 24.10 or later, or Docker with
  Compose 2.24.4 or later to install one
- Local access to its ClickHouse: `docker exec` into its container, or its HTTP
  interface at a local address
- Bun, for the dashboard companion

1. **Find SigNoz.** `signoz detect` only reads: it lists SigNoz and ClickHouse
   containers and which SigNoz ports are open on this machine.

   ```bash
   uv sync
   uv run agent-introspection signoz detect
   ```

   With a SigNoz present, connect to it as it is. With none, and only if you want one,
   the guided install sets one up. It refuses when it finds any SigNoz, and prints the
   `[signoz]` settings to use:

   ```bash
   bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh --dry-run  # then without --dry-run
   ```

2. **Configure and check the connection.**

   ```bash
   mkdir -p ~/.config/agent-introspection
   cp config.example.toml ~/.config/agent-introspection/config.toml  # set [signoz], [harnesses]; [dashboard] in docker mode
   uv run agent-introspection facts preflight
   uv tool install --force --reinstall .   # the standalone copy the scheduled sync and hooks run
   ```

   `facts preflight` is read-only: it checks the config, the connection, the ClickHouse
   version, the SigNoz columns the projections read, grants, and recent producer data.
   It always prints a JSON report, with configuration and connection errors as failing
   checks, and exits 30 when any check fails.

3. **Build the facts store and configure each harness.**

   ```bash
   agent-introspection facts install                          # tables, loaders, views, snapshots, registry
   agent-introspection facts backfill --days 90               # re-project everything SigNoz retains
   agent-introspection harness list                           # known harnesses, and which are enabled
   agent-introspection harness configure claude-code --dry-run  # preview, then run without --dry-run
   ```

   `harness configure` (`claude-code`, `codex`, or `omp`) writes only the export
   settings its profile manages, backing up each changed file; `--prompt-export` also
   turns on prompt export, which keeps prompt text in SigNoz (for Claude Code it also
   sets `OTEL_LOG_ASSISTANT_RESPONSES=0`, so assistant responses stay redacted). Its
   `next_steps` name the
   activity hooks to install. Then set `[hooks] otlp_logs_endpoint` in the config to
   the local collector's OTLP/HTTP logs URL.

4. **Schedule and diagnose.**

   ```bash
   agent-introspection facts schedule install   # a launchd agent on macOS, a systemd user timer on Linux
   agent-introspection doctor                   # read-only; each problem comes with its fix
   ```

   On Linux, `loginctl enable-linger "$USER"` keeps the timer running after you log
   out. `doctor` checks SigNoz, the collector, each enabled harness, the spool, the
   schedule, prompt labels, and the dashboard's ClickHouse URL, and can be run at any
   step.

The `introspection-onboarding` skill runs these steps in order, with backups and
validation.

## Quick Start

Load the first data and open the dashboard:

```bash
agent-introspection facts sync                # hook events, session attribution, labels, findings, evaluations
agent-introspection facts status              # loader state and per-harness freshness
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard build
bun run --cwd dashboard start                 # http://127.0.0.1:4173
```

The facts store materializes the harnesses' SigNoz telemetry into the `introspection`
ClickHouse database, and the React companion serves it on loopback. Its Pipeline view
shows loader freshness, harness parity, and project attribution.

## What Agent Introspection Does

Agent Introspection surfaces the same signals from every agent harness on this machine
(omp, Codex app-server/CLI/exec, and Claude Code) so one person can explore model usage
and agent process and find problems and improvements. Before another producer is added,
it shows which signals that producer could reach and which enabling it would hide. It
turns recurring problems into intervention proposals with an approval history, and
never applies a proposal itself: approval records a decision only, and applying
requires a separate explicit user request.

- `src/agent_introspection/`: the `agent-introspection` CLI; its `facts_sql/` holds the
  ClickHouse projections, views, and the signal support registry, and its
  `harness_profiles/` one profile per producer.
- `dashboard/`: an independent Bun + React package serving question-led views
  (outcome-first KPIs, trends, breakdowns, exemplars, session drill-down) over the facts.
- Workflow store: a SQLite file holding findings, proposals, review sessions, and their
  immutable event history.
- `.agents/skills/`: agent skills that install, operate, and improve the system (below).

### Agent skills

`.agents/skills/` holds four skills for coding agents working on or with this
repository. Each `SKILL.md` is a short index an agent loads when a request matches its
`description`; where a skill has step workflows, they live under `references/`, which
the agent opens only as needed. The skills route to each other instead of overlapping.

| Skill                                                                            | Purpose                                                                                                                                                                                                                                                                                                                                                                                              | Use when                                                                                                                                                                                                                                                                         |
| -------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`introspection-onboarding`](.agents/skills/introspection-onboarding/SKILL.md)   | Find the local SigNoz (or install one on request), run the single-machine install, configure each harness's export, activity hooks, and prompt export, assess unknown producers against the dashboard's capabilities, add other machines, and diagnose every step with `doctor`. Ships the activity-hook shim, the Claude Code and omp activity adapters, and the opt-in SigNoz files in `scripts/`. | Installing or reinstalling, onboarding or re-checking a harness, exploring a producer it does not know yet, adding another machine, proving capture end to end.                                                                                                                  |
| [`introspection-operations`](.agents/skills/introspection-operations/SKILL.md)   | Check facts-store health and change projections, fact views, registry entries, or harnesses under the parity rule.                                                                                                                                                                                                                                                                                   | Checking doctor, preflight, loaders, freshness, parity, sanitization, the coverage grid, the inbox, prompt labels, or producer drift; adding, changing, closing (including with a new hook event), or excluding a signal, or adding a harness, then reinstalling or backfilling. |
| [`introspection-improvement`](.agents/skills/introspection-improvement/SKILL.md) | Run the improvement loop: discover findings, draft a proposal, record the user's decision, mark it applied, evaluate the result.                                                                                                                                                                                                                                                                     | Finding what goes wrong with agents, turning findings into proposals, reviewing or approving one, marking one applied, checking whether a fix worked.                                                                                                                            |
| [`python-conventions`](.agents/skills/python-conventions/SKILL.md)               | Naming, package structure, object choice, and this repository's Python quality gates.                                                                                                                                                                                                                                                                                                                | Writing or refactoring Python in `src/`, `tests/`, `scripts/`, or `.agents` adapters.                                                                                                                                                                                            |

```text
onboarding ──> operations (health gate) ──> improvement loop
  install        facts + signal changes        findings → proposal → decision → evaluation
```

## Core Concepts

- **Producer and harness**: a producer is any service that sends telemetry to SigNoz.
  A harness is a producer the registry knows and the dashboard shows: omp, Codex
  (app-server, CLI, exec), or Claude Code.
- **Facts store**: the `introspection` database inside SigNoz's ClickHouse, with
  sanitized spans and logs, normalized fact views, and 91-day snapshots. Prompt,
  command, argument, and output text never enter it.
- **Signal and registry**: a dashboard measure, defined once in
  [`signal_support.toml`](src/agent_introspection/facts_sql/signal_support.toml) with the
  route that reaches it for each harness.
- **Capability**: a kind of producer data that signals consume, such as per-call token
  usage or one row per tool call. Each harness-scoped signal lists the capabilities it
  requires; [Producer Capabilities](docs/references/producer-capabilities.md) says what
  to look for and how to check each one.
- **Harness profile**: one TOML file per producer in
  `src/agent_introspection/harness_profiles/`: the settings that switch on its export,
  its session store and activity-hook adapter, and a status for every capability.
  `harness configure` and `doctor` read it.
- **Candidate producer**: a producer with a profile that is not a harness yet, such as
  opencode. `harness impact` previews it; nothing configures it until it is added as a
  harness.
- **Assessing an unknown producer**: `producers discover` finds it, and
  `producers profile` lists its span, event, and key names with counts (never values)
  and drafts a profile from capability guesses. `harness impact` then shows which
  signals enabling it would hide. See
  [Producer Onboarding](docs/references/producer-onboarding.md).
- **Parity rule**: a signal is shown only where every enabled harness reaches it; see the
  [harness support matrix](docs/references/data-gaps.md#harness-support-matrix).
- **Session stores and activity hooks**: project attribution reads only the session ID
  and working directory from each harness's own session files or database and resolves
  the Git repository; activity hooks spool records delivered over OTLP to SigNoz to
  close signals the producers' own telemetry lacks.
- **Prompt labels**: Jev classifications of each prompt's task type, correction kind,
  and sentiment. Only labels are stored. The OpenRouter key comes from
  `OPENROUTER_API_KEY` or, for the scheduled sync, `[labels] openrouter_key_command`;
  omp's stored credential is only a fallback.
- **Finding**: a recurring problem the facts already prove: a failure cluster or a
  repeated correction.
- **Proposal**: one evidence-backed intervention for a finding, moving
  `pending` → `approved` or `rejected` → `applying` → `applied`, then evaluated once.

## Go Deeper

- [docs/references/development.md](docs/references/development.md): quality gates,
  pre-commit, and the dashboard package commands.
- [docs/references/signoz-connection.md](docs/references/signoz-connection.md): finding
  or installing SigNoz, local-only limits, docker and HTTP connection modes, and
  required ClickHouse grants.
- [docs/references/cli-reference.md](docs/references/cli-reference.md): every
  `agent-introspection` command, its output contract, and its exit codes.
- [docs/references/producer-onboarding.md](docs/references/producer-onboarding.md): the
  onboarding lifecycle, harness profiles, projection, and parity impact.
- [docs/references/producer-capabilities.md](docs/references/producer-capabilities.md):
  what each capability needs and the status each producer claims.
- [docs/references/facts-store.md](docs/references/facts-store.md): how producer
  telemetry becomes fact views, snapshots, and registry tables.
- [docs/references/dashboard-companion.md](docs/references/dashboard-companion.md): server
  binding, environment variables, and how views aggregate harnesses.
- [docs/references/project-attribution.md](docs/references/project-attribution.md):
  project attribution from the harness session stores.
- [docs/references/hook-events.md](docs/references/hook-events.md): the hook event record
  contract and delivery.
- [docs/references/data-gaps.md](docs/references/data-gaps.md): what the dashboard cannot
  show, per harness and signal.
- [docs/README.md](docs/README.md): the index of every reference, UX, and planning doc.
