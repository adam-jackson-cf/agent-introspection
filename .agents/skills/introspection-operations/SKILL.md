---
name: "introspection-operations"
description: "Operate local Agent Introspection workflows safely; USE WHEN you need health checks, scans, proposal persistence, or approval recording."
---

# Workflow

### Step 1: Verify system health

- **Purpose**: Confirm the local system is safe and ready before reading or changing persisted state.
- **When**: Before scans, proposal operations, or approval recording.
- Verify SigNoz health, loopback-only network bindings, and disabled OrbStack LAN exposure.
- Check the approved source-schema fingerprint and stop on unapproved drift.
- Check SQLite integrity, dashboard validity, telemetry outbox state, and scheduler status.
- Report failed capabilities without bypassing or weakening any gate.
- Workflow: [Health workflow](references/health-workflow.md)

### Step 2: Run deterministic scans

- **Purpose**: Mine source telemetry and persist deterministic observations and trend evidence.
- Run the health workflow before scanning.
- Use scheduler leases, source watermarks, bounded ClickHouse reads, and idempotent persistence.
- Respect the seven-day actionable trend window and one successful or no-data scheduled run per UTC interval slot.
- Drain duplicate-tolerant derived telemetry and report scan, observation, trend, and delivery evidence.
- Workflow: [Scan workflow](references/scan-workflow.md)

### Step 3: Persist and inspect proposals

- **Purpose**: Create durable proposals from validated actionable findings without applying them.
- Require validated review output tied to an actionable finding.
- Evaluate established project tools first, new tools second, and bespoke scripts third.
- Persist the root cause, trend window, evidence, membership rationale, intervention, scope, target, rejected alternatives, validation criteria, rollback criteria, predicted success metric, and create-skill handoff fields when applicable.
- Inspect the proposal and its append-only event history.
- Never mutate a target repository or apply a proposal.
- Workflow: [Proposal workflow](references/proposal-workflow.md)

### Step 4: Record approval decisions

- **Purpose**: Record an explicit user decision while preserving the boundary between approval and application.
- **When**: Only after the user explicitly approves or rejects a specific persisted proposal.
- Show the proposal identifier, current state, exact intervention, validation criteria, rollback criteria, and material risks.
- Require an explicit approve or reject decision and record the actor and reason.
- Permit only pending to approved or pending to rejected transitions.
- Never enter applying, mark applied, or change a target repository; those actions require a separate explicit user request.
- Workflow: [Approval workflow](references/approval-workflow.md)

## Output

### Result Format

- Report the selected operation and final status.
- List persisted evidence, identifiers, counts, and verified provenance.
- List deferred candidates, failed capabilities, unresolved risks, and required user actions.
- Confirm whether any target repository was mutated; proposal and approval workflows must report no mutation.
