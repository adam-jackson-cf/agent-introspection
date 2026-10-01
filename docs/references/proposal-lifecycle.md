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
- At least one actionable `facts.failure_cluster` or `facts.repeated_correction`
  finding without a proposal.

## Steps

1. **Select.** `candidates export` picks the actionable finding without a proposal that
   has the highest subject `impact` (ties: most recent occurrence), and reserves one
   review session for it. The candidate carries the finding, its 14-day evidence pack
   from the facts (trimmed row by row from the largest lists when the payload would
   exceed the review input limit, recorded under `evidence.trimmed`), and the root of
   the project where it happened most.

2. **Draft.** `proposal draft` exports that candidate and asks Codex for the proposal in
   one read-only, non-interactive run:

   ```sh
   codex exec --model gpt-5.5 -c model_reasoning_effort="high" \
     -c otel.exporter="none" -c otel.trace_exporter="none" -c otel.metrics_exporter="none" \
     --sandbox read-only -C <project root> --json --output-schema <schema> -
   ```

   The prompt asks Codex to root-cause the finding from the evidence, audit the
   project's established tools first (the three tiers in canonical order), choose
   exactly one intervention by the deterministic-first rules, and never apply anything.
   Codex's own telemetry export is off, so drafting runs never become facts. The thread
   ID becomes the review's `trace_id`, and the run's input plus output tokens are
   charged against the reserved budget. The answer is imported through the same
   validated transaction as `proposal create`. `--dry-run` prints the envelope,
   command, output schema, and prompt without reserving a session or running Codex.
   When the project root is unknown or missing locally, Codex runs in the current
   directory.

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

`cluster_task_rate` (failure clusters) is tasks that hit the cluster per task;
`correction_task_rate` (repeated corrections) is labelled tasks in the finding's
project whose next prompt was a correction of the finding's kind, per labelled task
there. Harnesses must come from the finding, both windows are at least 7 days, and
`max_ratio` is in (0, 1]. Success means the rate over `evaluation_days` after the
proposal was applied is at most `max_ratio` times the rate over `baseline_days` before
it.

## What To Check

- `proposal list` shows the proposal's state and history.
- The Interventions dashboard view shows findings, proposals, and approval history.

## Related Docs

- [CLI Reference](cli-reference.md): every proposal command and its flags.
