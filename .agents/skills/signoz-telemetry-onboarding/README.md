# signoz-telemetry-onboarding

## Overview

A repository-local workflow for bootstrapping SigNoz, onboarding requested agent harnesses, loading the canonical project-attribution schema, and proving attribution from producer emission through dashboard-facing telemetry.

## When to use it

- Bootstrapping local SigNoz telemetry from scratch
- Onboarding Codex, Claude Code, OMP, or another user-requested agent harness
- Replacing CWD-based attribution with canonical source-owned project metadata

## Example prompts

- Set up local SigNoz telemetry and onboard Codex project attribution.
- Check whether OMP can emit the canonical project span attributes.
- Validate Claude Code project attribution from a fresh session through the dashboard.

## References

- [Stack bootstrap](references/stack-bootstrap-workflow.md)
- [Producer discovery](references/producer-discovery-workflow.md)
- [Canonical schema](references/canonical-schema-workflow.md)
- [Producer implementation](references/producer-implementation-workflow.md)
- [End-to-end validation](references/end-to-end-validation-workflow.md)
- [Upstream escalation](references/upstream-escalation-workflow.md)
