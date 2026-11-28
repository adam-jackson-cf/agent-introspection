# Proposals

The `agent-introspection candidates` and `proposal` commands behind the fix cycle: a finding becomes a proposed intervention, the user decides on it, and an applied fix is evaluated against the finding's rate. The tool never applies a proposal.

## Sub-features

- `candidates export --reserved-model-budget N`: the highest-impact actionable finding with no proposal yet, with its evidence pack trimmed to the drafting input limit, as a review session.
- `proposal draft --reserved-model-budget N`: drafts a proposal for that finding with Codex and imports it as `pending`. `--dry-run` shows the finding, its evidence and the drafting directory without reserving anything.
- `proposal create --input-json FILE`: imports a proposal drafted elsewhere; the document needs `provenance`.
- `proposal list` and `proposal show <id>`: every proposal's state, and one proposal with its full event history.
- `proposal decide <id> approve|reject --actor NAME --reason TEXT`: records the user's decision.
- `proposal mark-applied <id> --actor NAME --input-json FILE`: records an applied fix; the evidence must show passed validation checks.
- `proposal evaluate`: evaluates every applied proposal whose window has elapsed (`--now` takes an ISO 8601 instant with an offset).

## How to get to it (user POV)

Run the commands on the machine that holds the workflow store (`INTROSPECTION_WORKFLOW_DB`). Findings come from `facts findings` and from each `facts sync`. The `introspection-improvement` skill walks the whole cycle, and the [Interventions view](interventions.md) shows the results when `[experimental]` turns it on.

## Driving it with sandbox.sh lever

The lever does not run these commands; the [install journey](../../tests/journeys/test_install_journey.py) covers install, not the fix cycle.

Outcome tests: [tests/test_proposals.py](../../tests/test_proposals.py), [tests/test_drafting.py](../../tests/test_drafting.py), [tests/test_candidates.py](../../tests/test_candidates.py) and [tests/test_evaluation.py](../../tests/test_evaluation.py).

## Gotchas

- `facts sync` still evaluates due proposals when the `[experimental]` flag hides the Interventions view.
- A failed `draft` keeps its model-budget reservation.
- `mark-applied` that rejects its evidence leaves the proposal `approved`; the change already made in the target repository stays.
- A wrong proposal is rejected and redrafted; no command edits one.
