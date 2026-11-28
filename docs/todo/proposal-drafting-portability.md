# Proposal drafting portability

Track: deprioritised: improvement-loop fix cycle

## Problem

`proposal draft` has never produced a proposal. Three attempts on `mac-cf2` failed before reaching the
model:

| Launched from             | Failure                                                                             |
| ------------------------- | ----------------------------------------------------------------------------------- |
| `/tmp`                    | `codex exec`: `Not inside a trusted directory` (exit 60)                            |
| `~/Projects`              | Same; Codex accepts only a git repository, not a `trust_level = "trusted"` entry    |
| an empty scratch git repo | `400: The 'gpt-5.5' model is not supported when using Codex with a ChatGPT account` |

`--dry-run` passes while every real run fails, a failed draft keeps its budget reservation, and the
command cannot target a chosen finding.

## Evidence

- `src/agent_introspection/review.py:16`: `PROPOSAL_MODEL = "gpt-5.5"`. `review.py:147,180` store it in
  the review session, and `_validate_model_provenance` (`review.py:244`) checks the run against it, so a
  command-line override alone is not enough.
- `src/agent_introspection/drafting.py:71-88`: `codex_command` passes `-C <cwd>` without
  `--skip-git-repo-check`.
- `src/agent_introspection/drafting.py:264-270` and `:418`: with no evidence workdir on the drafting
  machine, the workdir falls back to `Path.cwd()`.
- `src/agent_introspection/candidates.py:35`: `LIMIT 1` takes the highest-impact finding.
- `src/agent_introspection/review.py:193,358`: the budget ledger records only `reserved` and
  `consumed`; nothing releases a reservation.

## Proposed change

1. Make the drafting model configurable end to end: config, review session and provenance check.
2. Run Codex in a trusted directory: a scratch git repository fallback or `--skip-git-repo-check`.
3. Make `--dry-run` fail when the workdir is untrusted or the model is unavailable.
4. Add `proposal draft --finding-id`.
5. Release the reservation when a draft fails.
6. Decide where drafting runs. The host often cannot see the repositories behind the failures.

## Acceptance check

- A dry run on a machine without a trusted directory, or with an unsupported model, fails.
- A real draft succeeds on that machine after the fix.
- A failed draft leaves no `reserved` entry without a matching release.
