# Canonical session-context contract workflow

## Objective

Create deterministic session-context records from normalized native lifecycle values and a Git workspace identity.

## Guidance

- Accept only producer, session ID, event type, RFC 3339 occurred-at timestamp, and absolute workspace inputs.
- Resolve the repository-owned canonical schema before deriving, emitting, reimporting, or validating project attribution.
- Require the complete canonical event and agent-project tuple; reject absent, partial, conflicting, or invalid values.
- Resolve the normalized Git root from the explicit absolute workspace and reject non-Git, missing, ambiguous, or escaping roots.
- Derive stable event and project identifiers from the canonical inputs exactly as the repository implementation specifies.
- Write only the allowlisted canonical record; do not retain producer content, command output, prompts, environment data, or arbitrary nested fields.
- Do not add static attributes or infer identity from telemetry CWD, prompts, paths, aliases, process state, or thread identity.
- Complete this workflow only when the record matches the repository-owned canonical schema and its identity derivation.
