# Setup

Use this guide to set up Agent Introspection by hand on one machine. The
[`introspection-onboarding`](../../.agents/skills/introspection-onboarding/SKILL.md)
skill runs the same steps for you; see the [README](../../README.md#quick-start).

## Purpose

Take a fresh machine from cloned repository to a dashboard showing live agent sessions
in four steps: install the CLI, find (or install) SigNoz, run `agent-introspection
setup`, and restart your agent sessions. At any point, `agent-introspection doctor`
checks everything and prints a `fix` for each `fail` or `warn`.

## Preconditions

| You need                                                 | How to get it                                                                          |
| -------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| macOS or Linux                                           | The sync and the dashboard run as launchd agents (macOS) or systemd user units (Linux) |
| Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)      | `curl -LsSf https://astral.sh/uv/install.sh \| sh`                                     |
| [Bun](https://bun.sh), for the dashboard                 | `curl -fsSL https://bun.sh/install \| bash`                                            |
| Docker with Compose 2.24.4+, only for step 2's install   | Docker Desktop or OrbStack (macOS), Docker Engine (Linux)                              |
| A self-hosted SigNoz on this machine (ClickHouse 24.10+) | Use the one you have, or install one in step 2                                         |
| `~/.local/bin` on your `PATH`                            | Where `uv tool install` puts the `agent-introspection` command                         |

## Steps

### 1. Install the CLI

```bash
git clone https://github.com/adam-jackson-cf/agent-introspection.git
cd agent-introspection
uv tool install --force --reinstall .
agent-introspection --help
```

You should see the command list. The scheduled sync and the activity hooks run the copy
in `~/.local/bin`, so after every update rerun `uv tool install --force --reinstall .`,
then `agent-introspection setup` (step 3), which reinstalls everything from it.

### 2. Find SigNoz, or install one

```bash
agent-introspection signoz detect
```

It only reads: it lists SigNoz and ClickHouse containers, which SigNoz ports are open,
and what to do next. It looks at SigNoz's default ports; for a SigNoz on other ports,
give it the addresses, then pass the same two options to `setup` in step 3:

```bash
agent-introspection signoz detect --clickhouse-url http://127.0.0.1:18123 --otlp-endpoint http://127.0.0.1:14318
```

If you have none, the guided install sets one up on loopback,
where every config default reaches it:

```bash
bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh --dry-run
bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh
```

It refuses to run when it finds any existing SigNoz. An existing SigNoz is kept as it
is, but the CLI and the dashboard read its ClickHouse over HTTP, which SigNoz does not
publish by default: if `signoz detect` says ClickHouse does not answer on
`127.0.0.1:8123`, add the opt-in
[ClickHouse HTTP override](../../.agents/skills/introspection-onboarding/scripts/signoz/docker-compose.clickhouse-http.yaml)
to your SigNoz Compose project (it publishes ClickHouse on `127.0.0.1:8123` only and
restarts it), or pass `--clickhouse-url` an address your container runtime resolves
(such as OrbStack's `http://<container>.orb.local:8123`).

### 3. Run setup

Preview it, then run it:

```bash
agent-introspection setup --dry-run
agent-introspection setup
```

It runs each step in order and stops at the first that fails, naming it. Each step
prints a line to stderr as it starts and ends; `facts` takes the longest, often
several minutes even on an empty SigNoz (a shorter `--backfill-days` shortens it), and
its backfill prints each table's progress in tenths. At
the end it prints a summary to stderr (each file it added or changed with its backup,
the config diff on a dry run, doctor's result, and what to do next) and the JSON report
to stdout:

1. **config:** writes the options you give into
   `~/.config/agent-introspection/config.toml` (comments and other keys kept, the file
   backed up). Anything not given keeps its default, so a SigNoz from the guided
   install needs no options. Upgrading, it also removes retired settings and keeps omp
   when its plugin is installed; the dry run's diff shows each change.
2. **signoz:** read-only checks of the connection, the ClickHouse version, the SigNoz
   columns, grants, and recent producer data.
3. **facts:** builds the facts store inside SigNoz's ClickHouse and loads the history
   SigNoz still holds (`--backfill-days`, default 90).
4. **harnesses:** for each enabled harness, writes its export settings into its own
   config files and installs its activity hooks (Claude Code's settings hooks, omp's
   plugin; Codex needs none), backing up each changed file.
5. **schedule:** the job that syncs every minute.
6. **dashboard:** builds the dashboard in this checkout and starts it as a service on
   <http://127.0.0.1:4173> (`--repo` names another checkout; `--no-dashboard` skips it).
7. **doctor:** every check, as `agent-introspection doctor` reports them.

Every step is idempotent, so a rerun is a reinstall. Its options:

| Option                                  | Sets                                                                                                                   |
| --------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `--harness KEY` (repeatable)            | `[harnesses] enabled`, the whole list; default Claude Code and Codex. omp is `oh-my-pi`.                               |
| `--prompt-export`                       | Exports prompt text for the Intent view's labels; SigNoz then keeps it for its retention (the facts store never does). |
| `--openrouter-key-command "CMD"`        | `[labels] openrouter_key_command`: a command that prints the key prompt labels need.                                   |
| `--clickhouse-url`, `--clickhouse-user` | `[signoz]`, when ClickHouse is not at `http://127.0.0.1:8123` as `default`.                                            |
| `--clickhouse-password-command "CMD"`   | `[signoz] clickhouse_password_command` (setup then skips the dashboard service: run it by hand).                       |
| `--otlp-endpoint`                       | `[signoz] otlp_endpoint`, when the collector is not at `http://127.0.0.1:4318`.                                        |

On Linux, `loginctl enable-linger "$USER"` keeps the sync and the dashboard running
after you log out.

### 4. Restart your agent sessions

Running Claude Code, Codex, or omp sessions read their settings at start, so restart
them.

## Configuration file

`setup` writes `~/.config/agent-introspection/config.toml`; `config.example.toml` in the
repository holds only the `[database]` defaults. Every other table is optional, and an
absent key keeps its default. The block below sets every documented key; the tables
after it explain each one.

```toml
[database]
path = "~/.local/share/agent-introspection/introspection.sqlite3"
busy_timeout_ms = 5000

[signoz]
clickhouse_url = "http://127.0.0.1:8123"
clickhouse_user = "default"
clickhouse_password_command = ["security", "find-generic-password", "-s", "signoz-clickhouse", "-w"]
otlp_endpoint = "http://127.0.0.1:4318"

[harnesses]
enabled = ["claude-code", "codex", "oh-my-pi"]

[hooks]
otlp_logs_endpoint = "http://127.0.0.1:4318/v1/logs"

[labels]
openrouter_key_command = ["security", "find-generic-password", "-s", "openrouter", "-w"]

[machine]
role = "additional"
export_endpoint = "http://signoz-host.local:4318"

[experimental]
intervention_outcomes = true
```

| Table            | What it sets                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `[signoz]`       | The SigNoz on this machine. Every key has a default, which suits a SigNoz installed by the onboarding skill's `install-signoz.sh`; non-local addresses are refused. `clickhouse_url` is ClickHouse's HTTP interface, which the CLI and the dashboard both read; SigNoz does not publish it by default, so publish it on loopback with the skill's `scripts/signoz/docker-compose.clickhouse-http.yaml`, or name a local container address your runtime resolves (such as OrbStack's `*.orb.local`). A password, if any, is printed by `clickhouse_password_command` (usable by the scheduled sync) and never stored in the file. `otlp_endpoint` is the OTLP/HTTP collector the harnesses export to, never port 4317. `INTROSPECTION_CLICKHOUSE_URL` overrides `clickhouse_url` for the dashboard only. |
| `[harnesses]`    | The harnesses this machine uses. Harness parity, the dashboard's harness selector and the findings detectors cover only these, and a signal some enabled harness cannot produce is hidden. Keys are the registry harnesses (`oh-my-pi`, `codex-app-server`, `codex_cli_rs`, `codex_exec`, `claude-code`); `codex` names the three Codex surfaces. Absent means Claude Code and Codex; add `oh-my-pi` to use omp. `facts install` loads the list into ClickHouse.                                                                                                                                                                                                                                                                                                                                        |
| `[hooks]`        | Where activity-hook and spool-status records go: an OTLP/HTTP logs URL, never the gRPC port 4317. Absent means `/v1/logs` on the collector this machine's harnesses export to (`[signoz] otlp_endpoint`, or `[machine] export_endpoint` on an additional machine), which is right unless records must go elsewhere.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `[labels]`       | Prompt labels are optional and need an OpenRouter key. `openrouter_key_command` is an argv list that prints it (such as a Keychain lookup), which the scheduled sync runs, so the key is never stored in the file. Without it, prompts are not labelled.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `[machine]`      | This machine's role. Absent, or `role = "host"`, means it runs SigNoz and the facts store. An additional machine runs harnesses but no SigNoz and sends everything to the host's collector: set `role = "additional"` and `export_endpoint` to that collector's OTLP/HTTP base URL (another machine's address is allowed here; no credentials). `harness configure` then writes that endpoint into each harness, records go to its `/v1/logs`, `doctor` checks the host's collector and the spool-sync timer instead of SigNoz, and commands that need ClickHouse (`facts`, `producers`) refuse to run. `[signoz]` and `[labels]` are not used there.                                                                                                                                                   |
| `[experimental]` | Dashboard features not yet proven in use, each off unless set `true`. `intervention_outcomes` shows, on the Findings and fixes view, whether an applied intervention reduced what it targeted: the post-intervention recurrence and recurrence baseline panels, the applied-interventions figure, and each proposal's verdict. `facts sync` records verdicts either way. Restart the dashboard (`dashboard install`) after a change.                                                                                                                                                                                                                                                                                                                                                                    |

## What To Check

- `setup` ends with `"passed": true`, and `agent-introspection doctor` shows the same;
  follow the `fix` on any `warn` or `fail`.
- <http://127.0.0.1:4173> opens, and its Pipeline view shows each harness's freshness.
- After a few minutes of agent use, its sessions appear in the dashboard.

## Beyond one machine and the known harnesses

- **One step at a time:** `facts install`, `facts backfill`, `harness configure`,
  `facts schedule install`, and `dashboard install` are the steps `setup` runs; the
  [CLI Reference](cli-reference.md) describes each.
- **A producer this tool does not know yet**, such as opencode:
  `agent-introspection producers discover` finds it;
  [Producer Onboarding](producer-onboarding.md) explains how to assess it before
  enabling it.
- **More than one machine**: one host runs SigNoz; other machines send to it. See the
  [additional machines workflow](../../.agents/skills/introspection-onboarding/references/additional-machines-workflow.md).

## Related Docs

- [Setup workflow](../../.agents/skills/introspection-onboarding/references/setup-workflow.md):
  the agent's version of this path, including reinstalls, the first sync, and
  validation. It is the source of truth; keep this guide in step with it.
- [CLI Reference](cli-reference.md): every command, its output, and its exit codes.
- [SigNoz Connection](signoz-connection.md): local-only limits, ClickHouse grants, and
  the two SigNoz layouts.
- [Hook Events](hook-events.md): what the activity hooks record and how prompt export
  works.
