---
name: "introspection-onboarding"
description: "Onboard deterministic Agent Introspection session context. USE WHEN you need to configure supported local producer lifecycle capture."
---

# Workflow

### Step 1: Establish local telemetry health

**Purpose:** Prove the local SigNoz stack can receive and retain OTLP before configuring producers.

**When:** Before first setup or any producer onboarding.

**Actions:** Verify loopback-only UI and listener health, collector pipelines, synthetic trace/metric/log ingestion, and newly retained backend records. Stop at the first failed boundary without weakening security. Follow [stack bootstrap](references/stack-bootstrap-workflow.md).

### Step 2: Discover producer capability

**Purpose:** Classify every requested producer by authoritative lifecycle context and end-to-end telemetry correlation availability.

**When:** After stack health passes and before installing or configuring a producer.

**Actions:** Classify Claude Code as unsupported and attribution unresolved because its hook payload does not provide an authoritative event timestamp and installed Claude trace/log telemetry does not document the same explicit producer/session correlation key. Classify Codex app-server as conditionally supported only when its OTLP spans expose the same session ID supplied by its lifecycle API. Classify direct Codex CLI and OMP as unsupported and unresolved. Follow [producer discovery](references/producer-discovery-workflow.md).

### Step 3: Apply the canonical session-context contract

**Purpose:** Use one deterministic event and project identity contract across every supported producer.

**When:** Before managed runtime installation or producer configuration.

**Actions:** Require normalized producer, session ID, lifecycle event, RFC 3339 timestamp, and absolute workspace inputs. Use the canonical event record and Git project tuple without CWD, prompt, path, alias, or static-attribute inference. Follow [canonical schema](references/canonical-schema-workflow.md).

### Step 4: Install the managed runtime

**Purpose:** Copy the skill-local distribution into a stable, versioned managed location before any hook invokes it.

**When:** After the contract is accepted and before configuring supported producers.

**Actions:** Copy the runtime and adapters to `$HOME/.local/lib/agent-introspection/session-context-runtime-v1/`; configure hooks to execute only that copied location, never the mutable skill source. Follow [managed runtime installation](references/session-hook-runtime-workflow.md).

### Step 5: Configure supported producers

**Purpose:** Configure only native lifecycle integrations that can preserve the canonical contract.

**When:** After the managed runtime is installed.

**Actions:** Configure only Codex app-server through its normalized lifecycle adapter when it meets the conditional support criteria. Do not configure Claude Code, direct Codex CLI, or OMP; leave them unresolved. Follow [producer configuration](references/producer-implementation-workflow.md).

### Step 6: Validate operation and scheduler handling

**Purpose:** Prove the managed runtime produces one canonical inbox event and that scheduler processing preserves it.

**When:** After every supported-producer configuration change.

**Actions:** Start a fresh supported producer session, validate the managed inbox record, then validate the scheduler's resulting telemetry and retained records. Do not run hooks or install the runtime when only authoring this skill. Follow [session-context validation](references/session-context-validation-workflow.md).

## Output

### Result Format

- Producer capability classification and unresolved producer boundaries
- Managed runtime version and installed location
- Canonical event-record and scheduler-operation validation evidence
- Failed boundary and corrective action when validation does not pass
