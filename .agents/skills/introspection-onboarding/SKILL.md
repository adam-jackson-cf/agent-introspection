---
name: "introspection-onboarding"
description: "Configure and validate canonical Agent Introspection project attribution; USE WHEN onboarding producers, validating managed session-context capture, or verifying project attribution."
---

# Task

## Procedure

- Select only the workflows required by the request; do not execute this index from top to bottom.
- Load a linked workflow only when its stated operation applies.
- Preserve explicit capability boundaries and leave unsupported attribution unresolved.

## Workflow index

### Telemetry foundation

- [Stack bootstrap](references/stack-bootstrap-workflow.md): prove local SigNoz ingestion before producer onboarding.
- [Canonical session-context contract](references/canonical-schema-workflow.md): resolve the authoritative event and project identity contract before creating or validating attribution.

### Producer capture

- [Producer discovery](references/producer-discovery-workflow.md): classify requested producers by their installed native lifecycle or approved proxy capabilities.
- [Producer configuration](references/producer-implementation-workflow.md): configure only documented native local-command hooks or the managed Codex app-server process proxy.
- [Managed runtime installation](references/session-hook-runtime-workflow.md): install the stable versioned managed runtime used by supported producers.
- [Session-context configuration validation](references/session-context-validation-workflow.md): validate producer configuration without triggering capture.

### Fresh-start cutover

- [Fresh-start cutover](references/fresh-start-cutover-workflow.md): retire approved historical telemetry only after every retained producer passes canonical end-to-end verification.

### Verification and escalation

- [End-to-end validation](references/end-to-end-validation-workflow.md): verify producer, ledger, source telemetry, and dashboard-facing identity correlation.
- [Unresolved producer escalation](references/upstream-escalation-workflow.md): record missing native capabilities without adding inferred attribution.

## Output

- Selected workflows and producer capability classifications
- Baseline window, denominator, unmatched cohorts, and evidence provenance
- Accepted and rejected attribution counts with rejection reasons
- Managed runtime, canonical scan, activity-version outbox, scheduler, and dashboard verification evidence
- Before-and-after attribution percentages using the same denominator
- Unsupported boundaries and unresolved risks
