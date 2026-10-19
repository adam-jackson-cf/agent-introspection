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
| Docker with Compose 2.24.4+                              | Docker Desktop or OrbStack (macOS), Docker Engine (Linux)                              |
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
minutes on a busy SigNoz (a shorter `--backfill-days` shortens it). The JSON report
goes to stdout at the end:

1. **config:** writes the options you give into
   `~/.config/agent-introspection/config.toml` (comments and other keys kept, the file
   backed up). Anything not given keeps its default, so a SigNoz from the guided
   install needs no options.
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
