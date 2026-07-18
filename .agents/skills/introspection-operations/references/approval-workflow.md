# Approval workflow

## Objective

Record an explicit approval decision without treating approval as authorization to apply.

## Required actions

1. Present the exact pending proposal and request an explicit decision.
2. Record approve or reject with actor, reason, and event history.
3. Reject any attempt to apply within this workflow.

## Done when

- The decision is durably recorded as approved or rejected.
- The proposal has not entered applying and no target has changed.
