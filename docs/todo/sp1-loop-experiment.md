# SP1: run the improvement loop once on live findings

Track: deprioritised: improvement-loop fix cycle

## Problem

The improvement loop (finding, drafted proposal, decision, evaluation) has never produced a proposal, so
nobody knows whether its drafts are worth approving. Hypothesis: at least 2 of the top 3 actionable
findings yield a proposal the user would approve as is or with minor edits. A result of 0 of 3 points at
the detector.

## Evidence

Blocked on [proposal drafting portability](proposal-drafting-portability.md). Three blind diagnoses of
the top findings, made without a draft in 2026-11, are the baseline to compare drafts against:

| Finding (impact)                                     | Diagnosis                                                                                                       | Worth fixing |
| ---------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ------------ |
| omp search `Path not found: <path>` (30), `aa244d7b` | Harmless exploration: 0.16% of omp calls, the next call almost always succeeds, unclean rate equals baseline    | No           |
| omp read `Path '…' not found` (20), `346f3294`       | About 40% are line-range syntax errors (`file:44:70` instead of `:44-70`) on files that exist; rest exploration | Low priority |
| Codex `Failed to initialize cache` (17), `aeee7e41`  | `~/.cache/uv` not writable in Codex's `workspace-write` sandbox; fixed by `writable_roots` (see operations)     | Yes          |

## Proposed change

After the drafting fix, draft proposals for the top 3 findings with the real CLI, one at a time. Diagnose
each finding blind in parallel, then have a judge compare each draft with its diagnosis and check the
draft's main claims against the facts store. Run it on the machine where drafting works and where the
repositories behind the failures are visible.

## Acceptance check

At least 2 of the top 3 drafts are judged approvable as is or with minor edits, and the user agrees.
