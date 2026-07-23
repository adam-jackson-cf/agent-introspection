---
name: "introspection-onboarding"
description: "Onboard bounded Agent Introspection session-context capture from producer-owned artifacts."
---

# Workflow

### Step 1: Establish local telemetry health

**Purpose:** Prove the local SigNoz stack can receive and retain OTLP before configuring capture.

**When:** Before first setup or any producer onboarding.

**Actions:** Verify loopback-only UI and listener health, collector pipelines, synthetic trace/metric/log ingestion, and newly retained backend records. Stop at the first failed boundary without weakening security. Follow [stack bootstrap](references/stack-bootstrap-workflow.md).

### Step 2: Discover a native command-hook surface

**Purpose:** Separate producers that can safely request bounded artifact backfill from scheduled capture.

**When:** Before changing a producer configuration.

**Actions:** A hook may invoke only `agent-introspection session-context backfill` with no hook payload, prompt content, static project value, or telemetry CWD. The command reads only producer-owned session metadata and tool-target records under its configured roots and is idempotent. Configure a native local-command hook only when the installed producer configuration surface documents it; do not patch a binary or invent a setting. Follow [producer configuration](references/producer-implementation-workflow.md).

### Step 3: Configure the supported hook or retain scheduled capture

**Purpose:** Trigger bounded backfill after a safe lifecycle event when a documented local-command hook exists.

**When:** After the producer capability boundary is recorded.

**Actions:** Configure Claude Code `SessionStart` to invoke the backfill command. Configure Codex app-server only when its installed documented configuration surface, rather than an API request surface, binds a local command. Do not configure direct Codex CLI. Use an OMP extension lifecycle surface only when it natively and safely launches a local command without static project values; otherwise leave it to scheduled harvesting. Record every intentionally unsupported surface and its exact boundary.

### Step 4: Validate configuration without triggering producers

**Purpose:** Ensure changed configuration is syntactically valid while preserving lifecycle-only execution.

**When:** After configuration authoring.

**Actions:** Parse the producer configuration format without printing secrets. Do not run producers, hooks, backfill, scans, or telemetry queries while only authoring configuration. Scheduled harvesting remains the capture path for unsupported surfaces.

## Output

### Result Format

- Enabled native producer hooks and their lifecycle events
- Intentionally unsupported producer surfaces and exact boundaries
- Configuration syntax-validation evidence
- Scheduled-capture producers
