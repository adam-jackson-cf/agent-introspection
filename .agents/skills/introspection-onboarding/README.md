# introspection-onboarding

## Overview

A portable workflow for bounded Agent Introspection session-context capture. Native producer hooks request idempotent artifact backfill; unsupported surfaces remain on scheduled harvesting.

## When to use it

- Configuring a documented local-command lifecycle hook for bounded artifact backfill
- Recording why a producer must remain on scheduled harvesting
- Validating configuration syntax without triggering a producer

## Example prompts

- Configure Claude Code `SessionStart` bounded artifact backfill.
- Determine whether an installed Codex app-server configuration surface can bind a local command.
- Classify whether an OMP extension lifecycle surface can safely invoke bounded artifact backfill.

## References

- [Stack bootstrap](references/stack-bootstrap-workflow.md)
- [Producer discovery](references/producer-discovery-workflow.md)
- [Producer configuration](references/producer-implementation-workflow.md)
- [Session-context validation](references/session-context-validation-workflow.md)
