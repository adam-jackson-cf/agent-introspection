# Setup

Use this guide to set up Agent Introspection by hand on one machine. The
[`introspection-onboarding`](../../.agents/skills/introspection-onboarding/SKILL.md)
skill runs the same steps for you; see the [README](../../README.md#quick-start).

## Purpose

Take a fresh machine from cloned repository to a dashboard showing live agent sessions:
connect to (or install) SigNoz, build the facts store, connect each harness, and
schedule the sync. Every step says what you should see. At any point,
`agent-introspection doctor` checks everything and prints a `fix` for each `fail` or
`warn`.

## Preconditions

| You need                                                 | How to get it                                                                 |
| -------------------------------------------------------- | ----------------------------------------------------------------------------- |
| macOS or Linux                                           | The scheduled sync is a launchd agent (macOS) or a systemd user timer (Linux) |
| Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)      | `curl -LsSf https://astral.sh/uv/install.sh \| sh`                            |
| [Bun](https://bun.sh), for the dashboard                 | `curl -fsSL https://bun.sh/install \| bash`                                   |
| Docker with Compose 2.24.4+                              | Docker Desktop or OrbStack (macOS), Docker Engine (Linux)                     |
| A self-hosted SigNoz on this machine (ClickHouse 24.10+) | Use the one you have, or install one in step 2                                |
| `~/.local/bin` on your `PATH`                            | Where `uv tool install` puts the `agent-introspection` command                |

## Steps

### 1. Install the CLI

```bash
git clone https://github.com/adam-jackson-cf/agent-introspection.git
cd agent-introspection
uv sync
uv tool install --force --reinstall .
agent-introspection --help
```

You should see the command list. The scheduled sync and the activity hooks run the copy
in `~/.local/bin`, so rerun `uv tool install --force --reinstall .` after every update.

### 2. Find SigNoz, or install one

```bash
agent-introspection signoz detect
```

It only reads: it lists SigNoz and ClickHouse containers and which SigNoz ports are
open. If you have a SigNoz, keep it as it is and go to step 3. If you have none, the
guided install sets one up on loopback and prints the settings for step 3:

```bash
bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh --dry-run
bash .agents/skills/introspection-onboarding/scripts/signoz/install-signoz.sh
```

It refuses to run when it finds any existing SigNoz.

### 3. Write the config and check the connection

```bash
mkdir -p ~/.config/agent-introspection
cp config.example.toml ~/.config/agent-introspection/config.toml
```

Edit the copy. It needs two things: how to reach ClickHouse, and which harnesses you
use.

| Your SigNoz                                   | `[signoz]`                                                                   | `[dashboard]`                                             |
| --------------------------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------- |
| Installed by the guided script                | Paste what it printed (`clickhouse_url`, `clickhouse_user`, `otlp_endpoint`) | Not needed                                                |
| Existing, ClickHouse HTTP on `127.0.0.1:8123` | `clickhouse_url = "http://127.0.0.1:8123"`, `clickhouse_user = "default"`    | Not needed                                                |
| Existing, ClickHouse not published            | `clickhouse_container` = the name `signoz detect` shows                      | `clickhouse_url` of an HTTP address the dashboard reaches |

The dashboard always reads ClickHouse over HTTP. If yours is not published, add the
opt-in [ClickHouse HTTP override](../../.agents/skills/introspection-onboarding/scripts/signoz/docker-compose.clickhouse-http.yaml)
to your SigNoz Compose project; it publishes ClickHouse on `127.0.0.1:8123` only.

Under `[harnesses]`, list the harnesses you use, for example
`enabled = ["claude-code", "codex"]` (omp is `oh-my-pi`). Then check:

```bash
agent-introspection facts preflight
```

You should see `"passed": true`. It only reads and names each failing check.

### 4. Build the facts store

```bash
agent-introspection facts install
agent-introspection facts backfill --days 90
```

`facts install` creates the `introspection` database inside SigNoz's ClickHouse;
`backfill` loads the history SigNoz still holds. Both print a JSON summary.

### 5. Connect your harnesses

Uncomment `[hooks]` in the config so activity records reach the collector:

```toml
[hooks]
otlp_logs_endpoint = "http://127.0.0.1:4318/v1/logs"
```

For each harness, preview and then write its export settings:

```bash
agent-introspection harness configure claude-code --dry-run   # then without --dry-run
agent-introspection harness configure codex --dry-run         # then without --dry-run
```

Each run changes only the settings its profile manages, backs up every file it
changes, and lists any `next_steps`. Then, per harness:

- **Claude Code** also needs its activity hooks:
  ```bash
  python3 .agents/skills/introspection-onboarding/scripts/adapters/claude-code/install_activity.py --dry-run  # then without
  ```
- **Codex** needs no hooks.
- **omp**: follow the
  [harness configuration workflow](../../.agents/skills/introspection-onboarding/references/harness-configuration-workflow.md).

Restart running agent sessions so they pick up the settings.

Prompt export is opt-in. The Intent view's labels need it, and SigNoz then keeps prompt
text for its retention (the facts store never does). Once you accept that, rerun
`harness configure` with `--prompt-export`.

### 6. Schedule the sync and open the dashboard

```bash
agent-introspection facts schedule install
agent-introspection doctor
bun install --cwd dashboard --frozen-lockfile
bun run --cwd dashboard build
bun run --cwd dashboard start
```

The schedule syncs every minute. On Linux, `loginctl enable-linger "$USER"` keeps it
running after you log out.

## What To Check

- `agent-introspection doctor` shows `"passed": true`; follow the `fix` on any `warn`
  or `fail`.
- <http://127.0.0.1:4173> opens, and its Pipeline view shows each harness's freshness.
- After a few minutes of agent use, its sessions appear in the dashboard.

## Beyond one machine and the known harnesses

- **A producer this tool does not know yet**, such as opencode:
  `agent-introspection producers discover` finds it;
  [Producer Onboarding](producer-onboarding.md) explains how to assess it before
  enabling it.
- **More than one machine**: one host runs SigNoz; other machines send to it. See the
  [additional machines workflow](../../.agents/skills/introspection-onboarding/references/additional-machines-workflow.md).

## Related Docs

- [Setup workflow](../../.agents/skills/introspection-onboarding/references/setup-workflow.md):
  the agent's full version of this path, including reinstalls, quality gates, the first
  sync, and validation. It is the source of truth; keep this guide in step with it.
- [CLI Reference](cli-reference.md): every command, its output, and its exit codes.
- [SigNoz Connection](signoz-connection.md): connection modes, local-only limits,
  ClickHouse grants, and the two SigNoz layouts.
- [Hook Events](hook-events.md): what the activity hooks record and how prompt export
  works.
