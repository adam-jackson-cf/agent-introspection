# introspection-operations

## Overview

Guarded operational workflows for the local telemetry-mining system, from health verification through approval recording, while keeping proposal application outside the skill.

## When to use it

- Checking local Agent Introspection and SigNoz readiness.
- Running deterministic interactive or scheduled scans.
- Creating and inspecting persisted proposals.
- Recording explicit approval or rejection decisions.

## Example prompts

- Check Agent Introspection health and explain any failed capability.
- Run an Agent Introspection scan and report persisted evidence.
- Draft proposals for actionable findings without applying them.
- Record my rejection of proposal PROPOSAL_ID with this reason.

## References

- [Health workflow](references/health-workflow.md)
- [Scan workflow](references/scan-workflow.md)
- [Proposal workflow](references/proposal-workflow.md)
- [Approval workflow](references/approval-workflow.md)
