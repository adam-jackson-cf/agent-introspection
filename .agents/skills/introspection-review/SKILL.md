---
name: "introspection-review"
description: "Run bounded manual candidate classifications; USE WHEN a user explicitly requests classification of persisted Agent Introspection candidates."
---

# Workflow

### Step 1: Verify review readiness

Confirm the health, source-contract, and current capability-proof gates before any export. Stop when a gate is invalid or expired.

Workflow: [preflight workflow](references/preflight-workflow.md)

### Step 2: Export bounded candidates

Create a classification-only candidate envelope that preserves its supplied bounds and provenance.

Workflow: [candidate export workflow](references/candidate-export-workflow.md)

### Step 3: Conduct classification

Classify only the ordered candidates in the validated envelope and record the supplied provenance.

Workflow: [classification workflow](references/classification-run-workflow.md)

### Step 4: Validate and import classifications

Accept only a complete result that matches the envelope identity, ordering, and review-run evidence.

Workflow: [provenance import workflow](references/provenance-import-workflow.md)

### Step 5: Hand off proposal decisions

Keep classifications separate from findings and proposals, then state the explicit next decision.

Workflow: [proposal handoff workflow](references/proposal-handoff-workflow.md)

### Step 6: Interpret review activity

Report the accepted manual-review facts and their dashboard meaning without inferring unavailable values.

Workflow: [review activity workflow](references/review-activity-workflow.md)

## Output

### Result Format

- Selected operation and capability status
- Review-session identifiers, counts, and validated provenance
- Imported or deferred candidates
- Dashboard review-activity implication
- Confirmation no repo mutation
