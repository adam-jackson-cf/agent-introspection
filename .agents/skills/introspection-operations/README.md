# introspection-operations

## Overview

Guarded operational workflows for the local agent telemetry facts store, from health verification through approval recording, while keeping proposal application outside the skill.

## When to use it

- Checking the facts loaders, freshness, coverage, and project sync.
- Applying facts projection, view, or registry changes.
- Creating and inspecting persisted proposals.
- Recording explicit approval or rejection decisions.

## Example prompts

- Check Agent Introspection facts health and explain any failed check.
- I changed the span projection; reinstall and backfill the facts.
- Draft proposals for actionable findings without applying them.
- Record my rejection of proposal PROPOSAL_ID with this reason.

## References

- [Health workflow](references/health-workflow.md)
- [Facts workflow](references/facts-workflow.md)
- [Proposal workflow](references/proposal-workflow.md)
- [Approval workflow](references/approval-workflow.md)
