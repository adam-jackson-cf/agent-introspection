# Proposal Lifecycle

Use this guide to take one finding from candidate to evaluated intervention. The
[`introspection-improvement`](../../.agents/skills/introspection-improvement/SKILL.md)
skill runs the same loop with an agent.

## Purpose

A proposal moves `pending` → `approved` or `rejected` → `applying` → `applied`, and
every transition appends an immutable event. `proposal decide` records an approval or
rejection only. Nothing is applied by this tool; entering `applying` requires a
separate explicit user request.

## Preconditions

- Facts are healthy and `facts sync` is running (stale facts give stale findings).
- At least one actionable `facts.failure_cluster` finding without a proposal.

## Steps

1. **Select.** `candidates export` picks the actionable finding without a proposal that
   has the highest subject `impact` (ties: most recent occurrence), and reserves one
   review session for it. The candidate carries the finding, its 14-day evidence pack
   from the facts (trimmed row by row from the largest lists when the payload would
   exceed the review input limit, recorded under `evidence.trimmed`), including the
   working directories its failing calls ran in (`workdirs`, most failures first).

2. **Draft.** `proposal draft` exports that candidate and asks Codex for the proposal in
   one read-only, non-interactive run:

   ```sh
   codex exec --model gpt-5.5 -c model_reasoning_effort="high" \
     -c otel.exporter="none" -c otel.trace_exporter="none" -c otel.metrics_exporter="none" \
     --sandbox read-only -C <top workdir> --json --output-schema <schema> -
   ```

   The prompt asks Codex to root-cause the finding from the evidence, audit the
   established tools of the repository the failures ran in first (the three tiers in canonical order), choose
   exactly one intervention by the deterministic-first rules, and never apply anything.
   Codex's own telemetry export is off, so drafting runs never become facts. The thread
   ID becomes the review's `trace_id`, and the run's input plus output tokens are
   charged against the reserved budget. The answer is imported through the same
   validated transaction as `proposal create`. `--dry-run` prints the envelope,
   command, output schema, and prompt without reserving a session or running Codex.
   Codex runs in the first evidence workdir that exists on this machine (`~` is this
   home), else in the current directory.

3. **Decide.** Record the user's explicit decision:

   ```sh
   uv run agent-introspection proposal decide <proposal-id> approve --actor <name> --reason <text>
   ```

4. **Apply and record.** After you have applied an approved proposal yourself, record
   it with validation evidence:

   ```sh
   uv run agent-introspection proposal mark-applied <proposal-id> --actor <name> \
     --input-json evidence.json
   ```

   `evidence.json` must report a passed validation with at least one named check:

   ```json
   {
     "validation": {
       "status": "passed",
       "checks": ["uv run pytest", "quality gates"]
     }
   }
   ```

   Any other shape, such as a missing `validation`, a `status` other than `passed`, or
   an empty or blank `checks` list, exits with code 50 and leaves the proposal
   unchanged. The command moves an `approved` proposal through `applying` to `applied`
   in one transaction, so a failure never leaves it stuck in `applying`, and a proposal
   changed by another process meanwhile is rejected rather than overwritten.

5. **Evaluate.** `proposal evaluate` (run by `facts sync`) measures every applied
   proposal whose evaluation window has elapsed and appends one immutable `evaluated`
   event with the baseline and evaluation rates, their ratio, and a verdict:
   `validated`, `regressed`, or `inconclusive` when the baseline has fewer than 3
   matching tasks or the evaluation window fewer than 5 tasks. Windows that start
   within 90 days read the snapshot tables; older ones read the live views. A proposal
   is evaluated once.

## Success metric

A proposal's `predicted_success_metric` is structured:

```json
{
  "metric": "cluster_task_rate",
  "harnesses": ["codex_exec"],
  "baseline_days": 14,
  "evaluation_days": 14,
  "max_ratio": 0.5
}
```

`cluster_task_rate` is tasks that hit the finding's failure cluster per task, the one
metric. Harnesses must come from the finding, both windows are at least 7 days, and
`max_ratio` is in (0, 1]. Success means the rate over `evaluation_days` after the
proposal was applied is at most `max_ratio` times the rate over `baseline_days` before
it.

## What To Check

- `proposal list` shows the proposal's state; `proposal show` prints its full event history (decisions, actors, times, evidence).
- The Interventions dashboard view shows findings, proposals, and an approval history table of each decision event (time, target, event, actor, and a short recorded reason; never raw prompt text).

## Design notes

The notes below record why parts of this area work the way they do. They came from code comments, which the repository no longer allows.

- Evaluation workspaces are excluded because they hold test workloads, not work to improve. A finding's `impact` is its affected tasks, with each task that did not end cleanly counted twice. Counts cover the seven-day window, while `first_seen_ns` keeps the earliest occurrence ever seen.
- `facts findings` excludes a whole session as an evaluation workspace only when every call that recorded a working directory ran under a temporary directory. A Codex call records a working directory only when its arguments name one. One call in a temporary directory therefore never hides a session that works elsewhere.
- A failure class that names only an error type or a header, such as `ShellError` or `Traceback (most recent call last):`, cannot be root-caused. Such a finding stays `emerging` whatever its counts, so no proposal is drafted from it until sharper signatures replace it.
- Each refresh marks the still-active findings of retired detectors `dormant`, so only the current detector's problems stay active. Retired findings stay in the workflow store as history. The retired detectors are the five of the old scan pipeline and the one that read the removed project attribution.
- `facts install` adds the `subject` column in place to a workflow store created before findings carried one.
- An evidence pack holds no raw prompt, command, or output text. It carries only the normalized forms the facts already hold: signatures, classes, labels, and home-redacted working directories.
- Codex counts reasoning tokens inside `output_tokens` and cached input inside `input_tokens`. The review therefore records only those two components, with availability `partial`, and `token_count` is their sum.
- `proposal draft` commits the review import and its proposals in one transaction. A failed proposal insert therefore never leaves the review session consumed without a proposal.
- The budget charge is the larger of the reported `token_count` and the sum of every reported token component. A low `token_count` therefore cannot hide components that already exceed it, even when usage is partial.
- New proposals reject a free-text metric. Older proposals may still hold a free-text or retired metric that nothing measures; these read as having no metric.
- A proposal's established-tool audit lists the three deterministic tiers in canonical order (`CANONICAL_TIER_LABELS` in `interventions.py`) and selects at most one. The other intervention types are allowed only when no tier can enforce the behaviour.

## Related Docs

- [CLI Reference](cli-reference.md): every proposal command and its flags.
