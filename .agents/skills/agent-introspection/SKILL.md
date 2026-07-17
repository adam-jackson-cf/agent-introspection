---
name: "agent-introspection"
description: "Operate local Agent Introspection workflows safely; USE WHEN you need health checks, scans, bounded model review, proposal persistence, or approval recording for Agent Introspection."
---

# Workflow

### Step 1: Verify system health

- **Purpose**: Confirm the local system is safe and ready before reading or changing persisted state.
- **When**: Before scans, model review, proposal operations, or approval recording.
- Verify SigNoz health, loopback-only network bindings, and disabled OrbStack LAN exposure.
- Check the approved source-schema fingerprint and stop on unapproved drift.
- Check SQLite integrity, dashboard validity, telemetry outbox state, and scheduler status.
- Report failed capabilities without bypassing or weakening any gate.
- Workflow: [Health workflow](references/step-1-health-workflow.md)

### Step 2: Run deterministic scans

- **Purpose**: Mine source telemetry and persist deterministic observations and trend evidence.
- Run the health workflow before scanning.
- Use scheduler leases, source watermarks, bounded ClickHouse reads, and idempotent persistence.
- Respect the seven-day actionable trend window and one successful or no-data scheduled run per UTC interval slot.
- Drain duplicate-tolerant derived telemetry and report scan, observation, trend, and delivery evidence.
- Workflow: [Scan workflow](references/step-2-scan-workflow.md)

### Step 3: Conduct bounded model review

- **Purpose**: Classify ambiguous episodes and analyze actionable trends through verified model-specific background subagents.
- Export persisted review candidates without modifying target repositories.
- Use GPT-5.6 Luna medium for semantic classification and GPT-5.5 high for actionable trend analysis and proposal drafting.
- Enforce the per-review candidate, call, character, output, and combined-call limits.
- Validate the nonce, payload hash, candidate IDs, requested model, effort, schema, and returned provenance before import.
- Query SigNoz for actual model and token telemetry before another call.
- Stop further calls and defer remaining candidates unchanged when provenance is missing or a ceiling is exceeded.
- Workflow: [Model review workflow](references/step-3-model-review-workflow.md)

### Step 4: Persist and inspect proposals

- **Purpose**: Create durable proposals from validated actionable findings without applying them.
- Require validated GPT-5.5 proposal output tied to an actionable finding.
- Evaluate established project tools first, new tools second, and bespoke scripts third.
- Persist the root cause, trend window, evidence, membership rationale, intervention, scope, target, rejected alternatives, validation criteria, rollback criteria, predicted success metric, and create-skill handoff fields when applicable.
- Inspect the proposal and its append-only event history.
- Never mutate a target repository or apply a proposal.
- Workflow: [Proposal workflow](references/step-4-proposal-workflow.md)

### Step 5: Record approval decisions

- **Purpose**: Record an explicit user decision while preserving the boundary between approval and application.
- **When**: Only after the user explicitly approves or rejects a specific persisted proposal.
- Show the proposal identifier, current state, exact intervention, validation criteria, rollback criteria, and material risks.
- Require an explicit approve or reject decision and record the actor and reason.
- Permit only pending to approved or pending to rejected transitions.
- Never enter applying, mark applied, or change a target repository; those actions require a separate explicit user request.
- Workflow: [Approval workflow](references/step-5-approval-workflow.md)

## Output

### Result Format

- Report the selected operation and final status.
- List persisted evidence, identifiers, counts, and verified provenance.
- List deferred candidates, failed capabilities, unresolved risks, and required user actions.
- Confirm whether any target repository was mutated; proposal and approval workflows must report no mutation.
