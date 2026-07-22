---
name: "introspection-onboarding"
description: "Onboard local SigNoz telemetry with verified producer project attribution. USE WHEN you need to bootstrap local SigNoz telemetry or onboard requested agent harnesses."
---

# Workflow

## Step 1: Establish local telemetry health

**Purpose:** Prove the local SigNoz stack can receive and retain OTLP before configuring producers.

**When:** Before first setup or any producer onboarding.

**Actions:** Verify loopback-only UI and listener health, collector pipelines, synthetic trace/metric/log ingestion, and newly retained backend records. Stop at the first failed boundary without weakening security. Follow [stack bootstrap](references/stack-bootstrap-workflow.md).

## Step 2: Discover requested producer capabilities

**Purpose:** Classify every harness named by the user, plus explicitly requested local auto-detection, by its project-metadata emission capability.

**When:** After stack health passes and before any producer configuration change.

**Actions:** Read the request for named harnesses. If none are named, ask before discovering local harnesses. Inspect each requested configuration, extension API, launcher, or source surface without exposing secrets. Classify it as natively configurable, source-change-required, or unsupported. Reject static process-level attributes for multiplexed or multi-workspace producers. Follow [producer discovery](references/producer-discovery-workflow.md).

## Step 3: Load the canonical project schema

**Purpose:** Resolve the repository-owned project-attribution contract before evaluating or changing a producer.

**When:** Before any producer implementation or end-to-end validation.

**Actions:** Resolve the declared repository-relative schema path, verify it exists and parses as JSON, and use only its fields and constraints for the rest of the operation. Follow [canonical schema](references/canonical-schema-workflow.md).

## Step 4: Implement canonical producer attributes

**Purpose:** Configure or change the producer to emit source-owned project metadata on relevant spans.

**When:** Only when the harness is natively configurable or has an available source implementation.

**Actions:** Emit the complete schema tuple on each relevant session or action span. Require a canonical project ID and non-empty display name. Do not derive attribution from CWD, paths, aliases, prompts, or thread inference. Follow [producer implementation](references/producer-implementation-workflow.md).

## Step 5: Validate end-to-end attribution

**Purpose:** Prove each producer emits metadata accepted by SigNoz and Agent Introspection.

**When:** After every producer configuration or source change.

**Actions:** Start a fresh session in a known project. Query recent SigNoz spans and derived events. Reject absent, partial, conflicting, or invalid tuples. Follow [end-to-end validation](references/end-to-end-validation-workflow.md).

## Step 6: Escalate unsupported producers

**Purpose:** Create a bounded, evidence-backed upstream handoff when configuration cannot implement correct attribution.

**When:** When a requested harness has no safe native configuration or source surface.

**Actions:** Record the unavailable capability and observed telemetry fields, specify the required producer-side contract, and leave attribution unresolved rather than adding static or inferred attribution. Follow [upstream escalation](references/upstream-escalation-workflow.md).

## Output

### Result Format

- Selected operation and requested-producer capability classification
- Stack, configuration, and end-to-end verification evidence
- Canonical schema path and producer attributes observed
- Unsupported boundaries, upstream handoffs, and unresolved risks
