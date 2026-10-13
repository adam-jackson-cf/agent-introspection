# Agent Introspection

**One local view of model usage and agent process across Claude Code, Codex, and omp,
with approval-gated proposals to fix recurring problems.**

![Version](https://img.shields.io/badge/version-0.1.0-blue.svg?style=flat-square)
![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg?style=flat-square)
![Dashboard](https://img.shields.io/badge/dashboard-bun%20%2B%20react-lightgrey.svg?style=flat-square)
![Primary CLI](https://img.shields.io/badge/cli-agent--introspection-orange.svg?style=flat-square)
![Platform](https://img.shields.io/badge/platform-local%20self--hosted%20SigNoz-purple.svg?style=flat-square)

Agent Introspection runs on your own desktop. It reads the telemetry your coding agents
send to a SigNoz on the same machine, and shows it in a local dashboard.

## Set it up

### The easy way: let your coding agent do it

Clone the repository, open it in Claude Code or Codex, and ask:

> Use the introspection-onboarding skill to set up Agent Introspection on this machine.

The [`introspection-onboarding`](.agents/skills/introspection-onboarding/SKILL.md) skill
runs the steps below in order. It shows a dry run before changing any file, backs up
what it changes, and asks before anything you have to decide, such as installing
SigNoz or turning on prompt export.

### Before you start

| You need                                                 | How to get it                                                                 |
| -------------------------------------------------------- | ----------------------------------------------------------------------------- |
| macOS or Linux                                           | The scheduled sync is a launchd agent (macOS) or a systemd user timer (Linux) |
| Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)      | `curl -LsSf https://astral.sh/uv/install.sh \| sh`                            |
| [Bun](https://bun.sh), for the dashboard                 | `curl -fsSL https://bun.sh/install \| bash`                                   |
| Docker with Compose 2.24.4+                              | Docker Desktop or OrbStack (macOS), Docker Engine (Linux)                     |
| A self-hosted SigNoz on this machine (ClickHouse 24.10+) | Use the one you have, or install one in step 2                                |
| `~/.local/bin` on your `PATH`                            | Where `uv tool install` puts the `agent-introspection` command                |

### The manual way

Each step says what you should see. `agent-introspection doctor` checks everything at
any point and gives the fix for each problem, so run it whenever something looks wrong.

**1. Get the code and install the CLI.**

```bash
git clone https://github.com/adam-jackson-cf/agent-introspection.git
cd agent-introspection
uv sync
uv tool install --force --reinstall .
agent-introspection --help
```

You should see the command list. `uv tool install` puts a standalone copy in
`~/.local/bin`; the scheduled sync and the activity hooks run that copy, so reinstall it
after every update.

**2. Find SigNoz, or install one.**

```bash
agent-introspection signoz detect
```

It only reads, and lists any SigNoz and ClickHouse containers and which SigNoz ports are
open. If you already have a SigNoz, keep it as it is and go to step 3. If you have none
and want one, the guided install sets one up on loopback and prints the settings for
step 3:

```bash
bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh --dry-run
bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh
```

It refuses to run when it finds any existing SigNoz, so it can never change yours.

**3. Write the config and check the connection.**

```bash
mkdir -p ~/.config/agent-introspection
cp config.example.toml ~/.config/agent-introspection/config.toml
```

Edit the copy. It needs two things: how to reach ClickHouse, and which harnesses you
use.

| Your SigNoz                                                 | `[signoz]`                                                                                                              | `[dashboard]`                                                           |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Installed by the guided script                              | Paste what it printed (`clickhouse_url`, `clickhouse_user`, `otlp_endpoint`)                                            | Not needed                                                              |
| Existing, ClickHouse HTTP published on loopback (port 8123) | `clickhouse_url = "http://127.0.0.1:8123"`, `clickhouse_user = "default"`                                               | Not needed                                                              |
| Existing, ClickHouse not published                          | `clickhouse_container` = the name `signoz detect` shows (`signoz-clickhouse` or `signoz-telemetrystore-clickhouse-0-0`) | `clickhouse_url` of an HTTP address the dashboard can reach (see below) |

The dashboard always reads ClickHouse over HTTP. If yours is not published, add the
opt-in [ClickHouse HTTP override](.agents/skills/introspection-onboarding/scripts/signoz/docker-compose.clickhouse-http.yaml)
to your SigNoz Compose project, which publishes it on `127.0.0.1:8123` only.

Then list your harnesses, for example `enabled = ["claude-code", "codex"]` under
`[harnesses]`, and check:

```bash
agent-introspection facts preflight
```

You should see `"passed": true`. It only reads, always prints a JSON report, and names
each failing check.

**4. Build the facts store.**

```bash
agent-introspection facts install
agent-introspection facts backfill --days 90
```

`facts install` creates the `introspection` database inside SigNoz's ClickHouse;
`backfill` loads the history SigNoz still holds. Both print a JSON summary.

**5. Connect your harnesses.**

Uncomment `[hooks]` in the config so activity records reach the collector:

```toml
[hooks]
otlp_logs_endpoint = "http://127.0.0.1:4318/v1/logs"
```

Then, for each harness you use, preview and write its export settings:

```bash
agent-introspection harness configure claude-code --dry-run   # then without --dry-run
agent-introspection harness configure codex --dry-run         # then without --dry-run
```

Each run changes only the settings its profile manages, backs up every file it changes,
and lists any `next_steps`. Claude Code also needs its activity hooks:

```bash
python3 .agents/skills/introspection-onboarding/scripts/adapters/claude-code/install_activity.py --dry-run  # then without
```

Codex needs no hooks. For omp, follow the
[harness configuration workflow](.agents/skills/introspection-onboarding/references/harness-configuration-workflow.md).
Restart running agent sessions so they pick up the settings. Prompt export (needed for
the Intent view's labels) is opt-in: add `--prompt-export` to `harness configure` once
you have decided SigNoz may keep prompt text.

**6. Schedule, check, and open the dashboard.**

```bash
agent-introspection facts schedule install
agent-introspection doctor
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard build
bun run --cwd dashboard start
```

The schedule syncs every minute (on Linux, `loginctl enable-linger "$USER"` keeps it
running after you log out). `doctor` should show `"passed": true`; any `warn` or `fail`
comes with its fix. Open <http://127.0.0.1:4173>: the Pipeline view shows each
harness's freshness. Use an agent for a few minutes and its sessions appear.

### If something fails

- Run `agent-introspection doctor` and follow the `fix` on each `fail` or `warn`.
- [CLI reference](docs/references/cli-reference.md): every command, its output, and its
  exit codes.
- [SigNoz connection](docs/references/signoz-connection.md): connection modes, ClickHouse
  grants, and the two SigNoz layouts.

### Other harnesses and machines

- **A producer this tool does not know yet**, such as opencode:
  `agent-introspection producers discover` finds it, and
  [Producer Onboarding](docs/references/producer-onboarding.md) explains how to assess
  it before enabling it.
- **More than one machine**: one host runs SigNoz; other machines that run agents send
  to it. See the
  [additional machines workflow](.agents/skills/introspection-onboarding/references/additional-machines-workflow.md).
- **Local only**: the host refuses any ClickHouse or collector address that is not on
  this machine, and never reconfigures an existing SigNoz.

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
