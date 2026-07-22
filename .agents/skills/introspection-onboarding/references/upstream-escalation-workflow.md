# Upstream escalation workflow

## Objective

Create a bounded, evidence-backed producer-side handoff when correct attribution cannot be configured locally.

## Required actions

1. Document the unavailable producer capability and observed telemetry fields.
2. Link the canonical schema and identify the exact producer-side span contract required.
3. State why static configuration or inferred attribution is unsafe for the requested harness.
4. Keep the producer unresolved rather than adding a fallback attribution path.

## Done when

- A concrete producer-side implementation handoff exists.
- The unresolved attribution boundary is explicit.
