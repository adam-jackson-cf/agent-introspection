# CLI Reference

Use this catalog to find the `agent-introspection` command for a task. Every command
except `hook` writes structured JSON to stdout, writes diagnostics to stderr, and fails
with a stable non-zero exit code.

Run commands as `uv run agent-introspection …` from the repo, or as
`agent-introspection …` from the standalone copy installed by
`uv tool install --force --reinstall .` (the copy the launchd job and hooks run). The
global `--config PATH` option, or `AGENT_INTROSPECTION_CONFIG`, selects a config file
other than `~/.config/agent-introspection/config.toml`.

## Facts

| Command                                  | What it does                                                                                     | When to use it                                                |
| ---------------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- |
| `facts preflight`                        | Read-only checks of version, SigNoz columns, grants, and recent producer data.                   | Before `facts install`.                                       |
| `facts install`                          | Creates tables, loaders, views, snapshots, and loads the signal registry.                        | First install; after any change under `facts_sql/`.           |
| `facts backfill [--days 90]`             | Re-projects everything SigNoz retains.                                                           | After a span or log projection change.                        |
| `facts status`                           | Loader state and per-harness freshness.                                                          | Health checks.                                                |
| `facts sessions [--rescan]`              | Attributes new sessions from the harness session stores; `--rescan` re-reads every session file. | After adding a session-store root, or to rebuild attribution. |
| `facts findings`                         | Promotes failure clusters and repeated corrections into workflow findings.                       | Refreshing findings on demand.                                |
| `facts sync`                             | Hook events, session attribution, prompt labels, findings, and due proposal evaluations.         | What the schedule runs every minute.                          |
| `facts classify [--days 2] [--limit 60]` | Labels exported prompts now.                                                                     | On-demand prompt labelling.                                   |
| `facts schedule install\|remove\|status` | Manages the launchd job that runs `facts sync` every minute.                                     | Install, uninstall, or check the schedule.                    |

## Proposals

| Command                                                                        | What it does                                                                                  | When to use it                    |
| ------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------- | --------------------------------- |
| `candidates export --reserved-model-budget <tokens> [--batch-id ID]`           | Picks the highest-impact actionable finding without a proposal and reserves a review session. | Feeding an external drafter.      |
| `proposal draft --reserved-model-budget <tokens> [--dry-run] [--batch-id ID]`  | Exports a candidate and drafts its proposal with `codex exec`.                                | Drafting; run `--dry-run` first.  |
| `proposal create --input-json FILE`                                            | Imports a proposal through the validated transaction.                                         | Importing a drafted proposal.     |
| `proposal list`                                                                | Lists proposals.                                                                              | Reviewing state.                  |
| `proposal show <proposal-id>`                                                  | Shows one proposal.                                                                           | Reviewing one proposal.           |
| `proposal decide <proposal-id> approve\|reject --actor <name> --reason <text>` | Records a decision only.                                                                      | After the user decides.           |
| `proposal mark-applied <proposal-id> --actor <name> --input-json FILE`         | Records a user-applied proposal with validation evidence.                                     | After applying a change yourself. |
| `proposal evaluate [--now <ISO 8601 instant>]`                                 | Evaluates applied proposals whose window has elapsed.                                         | Normally run by `facts sync`.     |

## Hooks

| Command                   | What it does                                                                                                                                                                        | When to use it                                    |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| `hook <producer> <event>` | Reads one activity-hook envelope from stdin and writes normalized records to the hook inbox. Always exits 0 and prints nothing; failures go to the hook log by exception type only. | Run by the installed activity hooks, not by hand. |

## Notes

- Nothing is applied by this tool. See [Proposal Lifecycle](proposal-lifecycle.md) for
  selection, the success metric, and `mark-applied` evidence.
- Source: `src/agent_introspection/cli.py`.
