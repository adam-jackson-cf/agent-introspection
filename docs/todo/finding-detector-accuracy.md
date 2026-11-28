# Finding detector accuracy

Track: priority: CLI data access / problem and friction surfacing

## Problem

The failure-cluster detector merges unrelated failures and overstates their impact, so the findings it
surfaces point at the wrong problems:

1. `failure_class` replaces every token that contains a slash with `<path>`. Unrelated path failures
   fall into one cluster.
2. `impact` adds the unclean tasks to the affected tasks without checking that the failure caused the
   unclean outcome. Long steered sessions inflate it.
3. Automated test traffic, such as `codex exec --ephemeral` slow-seam runs, counts as agent impact.
4. `EVALUATION_PATH` needs a trailing slash, so a bare `/private/tmp` workdir probably counts as agent
   work. No test covers that case.

In the 2026-11 SP1 blind diagnoses the top finding (omp search `Path not found: <path>`, impact 30) was
harmless exploration whose unclean rate equalled the omp baseline (30% against 30%).

## Evidence

- `src/agent_introspection/facts_sql/003_views.sql:474`: the `<path>` rewrite.
- `src/agent_introspection/findings.py:80-81`: `return tasks + unclean_tasks`.
- `src/agent_introspection/findings.py:28`: `EVALUATION_PATH = r"(^|\\W)(/private)?/(tmp|var/folders)/"`.
- `tests/test_findings.py:160-175` checks only that the pattern appears in the queries, not which paths
  it matches.

## Proposed change

1. Keep distinct path failures apart, for example by keeping the error text around the path or the
   path's last segment class.
2. State a causal rule for impact, such as counting an unclean task only when its last failure is in the
   cluster, and document it in the `recur.actionable` formula.
3. Exclude known automated test traffic by a stated rule.
4. Make `EVALUATION_PATH` match a bare temporary directory and pin it with a test.

## Acceptance check

- Two failures on unrelated paths land in separate clusters in a test.
- A test pins a bare `/private/tmp` workdir as excluded.
- The registry formula states the impact rule, and `render-measures.py --check` passes.
