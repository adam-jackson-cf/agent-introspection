---
name: "introspection-operations"
description: "Operate local Agent Introspection workflows safely; USE WHEN you need facts health checks, facts maintenance, proposal persistence, or approval recording."
---

# Workflow

### Step 1: Verify facts health

- **Purpose**: Confirm the ClickHouse facts store and project sync are current before reading or changing persisted state.
- **When**: Before facts maintenance, proposal operations, or approval recording.
- Run `agent-introspection facts status` and `agent-introspection facts schedule status`.
- Open the dashboard Pipeline view and read loaders, freshness, parity, sanitization, the coverage grid, and project attribution.
- Report stale loaders, possible breaks, unexplained strays, parity mismatches, sanitization violations, or an inbox backlog without bypassing or weakening any check.
- Workflow: [Health workflow](references/health-workflow.md)

### Step 2: Maintain the facts store

- **Purpose**: Apply projection, view, or registry changes and keep project attribution flowing.
- Run `facts install` after any change under `facts_sql/`, then `facts backfill --days 90` when the span or log projection changed.
- Keep `session_projects` intact: it is the only copy of hook history once inbox files are synced.
- Workflow: [Facts workflow](references/facts-workflow.md)

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
- List failed checks, unresolved risks, and required user actions.
- Confirm whether any target repository was mutated; proposal and approval workflows must report no mutation.
