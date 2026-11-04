# Handoff: ambition review and spike SP1

Written 2026-11-04 to move this work from `mac-cf1` (`hj24mjl4p6`) to a new machine.
Branch: `chore/machine-migration-handoff`. Every other branch is pushed and clean.

## Where things stand

1. A `challenge-ambition` review (Felix Rieseberg's speed, altitude, depth and range
   lenses) found that the improvement loop — finding → drafted proposal → decision →
   evaluation — had never produced a proposal. The primary store on `mac-cf2` held 35
   actionable findings and 0 proposals, review sessions, and model runs.
2. The review proposed three spikes. SP1 was run three times and is **blocked on code
   changes**. SP2 and SP3 have not been run.
3. Suggestion S1 (a Codex sandbox fix) is applied and verified. S2 (host temp-file
   clean-up) is done.

## SP1: run the improvement loop once on live findings

**Hypothesis:** at least 2 of the top 3 actionable findings yield a proposal the owner
would approve as-is or with minor edits. 0 of 3 points at the detector. The
hypothesis is still untested, because no draft has ever been produced.

### Why every draft failed

`proposal draft` on `mac-cf2` never reaches the model:

| Attempt | Launched from | Failure |
| --- | --- | --- |
| 1 | `/tmp` | `codex exec`: `Not inside a trusted directory` (exit 60) |
| 2 | `~/Projects` | Same. Codex accepts only a git repo; a `trust_level = "trusted"` entry does not count. |
| 3 | an empty scratch git repo | `400: The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account` |

Causes in this repository:

1. `src/agent_introspection/drafting.py` `working_directory()` falls back to the
   current directory when no evidence workdir exists on the drafting machine, and
   `codex_command()` passes it to `codex exec -C` without `--skip-git-repo-check`.
   The evidence pack often has no workdirs, and the user's projects live on the
   `mac-cf1` laptop, not on `mac-cf2`.
2. `src/agent_introspection/review.py:17` hardcodes `PROPOSAL_MODEL = "gpt-5.5"`.
   `mac-cf2`'s Codex uses a ChatGPT login whose default model is `gpt-6.1-sol`.
   `_validate_model_provenance()` checks the model against the stored review session,
   so a command-line override alone is not enough.
3. `proposal draft --dry-run` checks neither condition, so it passes while every real
   run fails.
4. `proposal draft` cannot target a finding. It always takes the highest-impact
   actionable finding without a proposal (`candidates.py`, `LIMIT 1`).

### Next step (not started)

Make these changes on a branch with tests, then rerun SP1's drafting stage:

1. Make the drafting model configurable end to end (config, review session,
   provenance check).
2. Run Codex in a trusted directory: a scratch git repo fallback or
   `--skip-git-repo-check`.
3. Make `--dry-run` fail when the workdir is untrusted or the model is unavailable.
4. Optional: add `proposal draft --finding-id`.

Also decide where drafting runs. Codex on `mac-cf2` cannot see the repositories behind
most failures.

### Blind diagnoses (independent of any draft)

Three agents root-caused the top three findings from the facts store and the real
repositories, without seeing a draft:

| Finding (impact) | Diagnosis | Worth fixing | Recommended fix |
| --- | --- | --- | --- |
| omp search `Path not found: <path>` (30), `aa244d7b` | Harmless exploration: 52 failed calls (0.16% of omp calls), the next call almost always succeeds, and the unclean rate equals the omp baseline (30% vs 30%). | No | Detector change only |
| omp read `Path '…' not found` (20), `346f3294` | Mixed: about 40% are line-range syntax errors on files that exist (`file:44:70` instead of `:44-70`, from Claude models); the rest is exploration. | Low priority | Optional omp `tool_call` hook that rewrites `:N:M` to `:N-M` |
| Codex `Failed to initialize cache` (17), `aeee7e41` | Sandbox config: `~/.cache/uv` was not writable in Codex's `workspace-write` sandbox. 13 of 17 tasks are enaible slow-seam test runs. | Yes | Applied as S1 (below) |

Loop defects the judges found (inferred from code and data, not yet fixed):

- `facts_sql/003_views.sql` rewrites every slash-containing token to `<path>`, which
  merges unrelated failures into one cluster.
- `findings.py` `impact()` counts unclean tasks without checking that the failure
  caused them, so long steered sessions inflate impact.
- Ranking ignores the evidence pack's `next_step` data, so self-recovering failures
  still reach the drafter.
- Automated test traffic (enaible `codex exec --ephemeral` seams) counts as agent
  impact. `EVALUATION_PATH` probably misses a bare `/private/tmp` workdir (untested).

### Rerunning SP1

`docs/todo/sp1-improvement-loop.workflow.js` is the workflow script used for SP1. It
runs sequential real drafts, three blind diagnoses in parallel, and a judge per
finding. Run it with the Workflow tool and `scriptPath`, passing `args.findings`
(`id`, `short`, `tool_family`, `failure_class`, `harnesses`, `impact`). The cached
results stayed on `mac-cf1`, so the diagnoses will rerun.

The script expects two helper scripts on `mac-cf2`, both deleted after the run.
Recreate them first:

`/tmp/ai-primary.sh` runs the CLI with the sync job's environment:

```zsh
#!/bin/zsh
R=/Users/adamjackson/.local/share/local-docker-host/runtime/agent-introspection/f29fc1a4a27c46329062b7d282960cb5/facts-owner
T=/Users/adamjackson/.local/share/local-docker-host/runtime/introspection-dependencies/tools/agent-introspection/bin
export DOCKER_CONFIG=$R/docker-config
export PATH=$R/bin:$T:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
exec $T/agent-introspection --config $R/facts-config.toml "$@"
```

`/tmp/ch-ro.sh` runs a read-only ClickHouse query:

```zsh
#!/bin/zsh
R=/Users/adamjackson/.local/share/local-docker-host/runtime/agent-introspection/f29fc1a4a27c46329062b7d282960cb5/facts-owner
export DOCKER_CONFIG=$R/docker-config PATH=$R/bin:/opt/homebrew/bin:/usr/bin:/bin
exec docker --context orbstack exec native-f29fc1a4a27c4632-c18-signoz-clickhouse clickhouse-client --readonly=1 --format PrettyCompactMonoBlock -q "$1"
```

Update the script's draft step to launch from wherever the code changes above make
drafting work. It currently launches from `/tmp/sp1-scratch`, which was deleted.

## S1: Codex sandbox fix (applied)

Added to each Codex config:

```toml
[sandbox_workspace_write]
writable_roots = ["/Users/adamjackson/.cache/uv"]
```

Applied to `~/.codex/config.toml` on `mac-cf1` and `mac-cf2`, and to the Orca
account's `codex-accounts/c83de515…/home/config.toml` on `mac-cf1`.

Verified on `mac-cf1` in `~/Projects/complete/enaible`:
`codex sandbox -c 'sandbox_mode="workspace-write"' -- uv pip list` failed before with
`Failed to initialize cache at ~/.cache/uv` and succeeds after.

**On the new machine:** add the same section to its Codex configs if it runs Codex
with uv. Finding `aeee7e41` stays actionable until its 7-day window rolls past the
old failures; the next enaible slow-seam run is its first real test.

## Leftover state on `mac-cf2`

- The three failed drafts left 3 `exported` review sessions in
  `…/f29fc1a4a27c46329062b7d282960cb5/state/introspection.sqlite3`, each with a
  2,000,000-token reservation in `model_budget_ledger`. They do not block candidate
  selection. A failed draft never releases its reservation; fix that with the
  drafting changes.
- Prompt labelling fails during `facts sync`: the sync log shows
  `OpenRouter refused the key or credit (HTTP 403)` (964 of 1,262 lines on
  2026-10-28), and later `Jev resolved to an unexpected model`. The corrections and
  sentiment signal is starved until this is fixed.

## Spikes not yet run

- **SP2, replay check:** reproduce the Codex cache failure under `codex exec` in the
  same sandbox, fix the config, and confirm the replay passes, all in under 30
  minutes instead of a 14-day evaluation. S1 has since fixed this failure, so pick
  another environment-class finding (for example
  `zsh: can't create temp file for here document: operation not permitted`,
  `codex_exec`, 15 tasks).
- **SP3, recovery-cost ranking:** rank findings by the tokens and wall time between
  a failure and the next successful call in the same task, using `duration_ms` and
  model-call tokens. Compare it with the current `impact` ranking. Pass if at least
  2 of the top 5 change and the owner prefers the new list in a blind comparison.

## Moving to the new machine

`mac-cf1` is an `additional` machine (`~/.config/agent-introspection/config.toml`
`[machine] role = "additional"`). It sends telemetry to `mac-cf2`, which hosts
SigNoz, the facts store, and the workflow store. On the new machine:

1. Clone the repository and check out `chore/machine-migration-handoff`, or merge it
   first.
2. Run `uv tool install --force --reinstall .`, then `agent-introspection setup`
   with the additional-machine settings, or use the `introspection-onboarding`
   skill.
3. Run `agent-introspection doctor` and fix each `fail` or `warn`.
4. Add the S1 Codex section if the machine runs Codex with uv.
