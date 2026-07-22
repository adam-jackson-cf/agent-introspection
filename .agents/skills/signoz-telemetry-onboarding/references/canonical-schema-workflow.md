# Canonical schema workflow

## Objective

Resolve and validate the repository-owned agent-project OpenTelemetry contract without copying it into workflow prose.

## Required actions

1. Resolve [the canonical JSON schema](../../../../src/agent_introspection/schemas/otel/agent-project.schema.json) relative to this reference file.
2. Confirm the target exists, parses as JSON, and identifies the agent-project OTEL contract.
3. Use the resolved schema as the only source for producer fields, scope, value constraints, tuple requirements, dashboard pairing, and prohibited attribution sources.
4. Reject a missing, malformed, or ambiguous schema reference before changing producer configuration.

## Done when

- The canonical repository schema has been resolved and validated.
- No workflow document duplicates the schema contract.
