# Codex Unified Project Attribution Plan

## Decision required

Implement project attribution once in upstream Codex core, at session creation. Codex CLI and Codex Desktop use that same implementation through `SessionConfiguration` and `SessionTelemetry`.

This replaces an external CLI launcher and an app-server-only attribution path. No implementation begins until this plan is approved.

## Contract

The canonical contract is `src/agent_introspection/schemas/otel/agent-project.schema.json`.

Codex emits the complete canonical project tuple together on relevant telemetry only when it has an authoritative project context. A non-Git, missing, invalid, or ambiguous context emits no project tuple and remains unresolved.

## Invariants

- Project identity is resolved by Codex at the session or action boundary, never reconstructed downstream.
- Project identity is immutable for the telemetry context to which it is attached.
- A single trace must not contain conflicting project tuples.
- Static process-level OpenTelemetry attributes must not carry project identity.
- CWD, paths, aliases, prompts, and thread inference must not be used by Agent Introspection to create attribution.
- The existing canonical schema is the sole definition of fields and validation constraints.

## Implementation phases

### 1. Shared Codex project-context resolver

Add a shared core value for validated project metadata and a worktree-safe Git resolver.

- Resolve the selected source-owned workspace through Git's common directory.
- Normalize the canonical repository root and derive the canonical Git identity from it.
- Return either a complete validated tuple or no tuple.
- Reuse or extend Codex's existing Git utility surface; do not add parallel Git-discovery logic.
- Cover normal repositories, linked worktrees, nested paths, non-Git directories, malformed Git output, and resolution failures.

### 2. Core session telemetry propagation

Construct the project context when core creates a `SessionConfiguration` / `SessionTelemetry` pair.

- Store the tuple in session-owned telemetry state.
- Apply it consistently to relevant session and action telemetry builders.
- Preserve the tuple through cloned request, tool, hook, compaction, and resumed-session telemetry paths.
- Keep all tuple fields absent together when resolution is unavailable.
- Do not configure `otel.span_attributes` with project fields.

### 3. Codex Desktop and app-server safety

Use the existing Desktop app-server thread lifecycle as an input path, not as a second implementation.

- `thread/start` and `thread/resume` provide source-owned `cwd`, workspace roots, and thread identity to core session construction.
- Associate telemetry with a single authoritative workspace only.
- For a multi-workspace thread without one authoritative active workspace, emit no project tuple.
- For an action whose selected workspace differs from the session project, do not reuse the session tuple. Resolve an action-local tuple only if its trace cannot conflict; otherwise leave the action unresolved.
- Validate two simultaneous Desktop threads in distinct Git workspaces cannot leak or conflict.

### 4. Codex CLI adoption

Use the same core resolver and telemetry propagation for direct `codex` CLI sessions.

- Resolve the CLI session's source-owned workspace during normal session creation.
- Emit native Codex telemetry without an external launcher or per-invocation configuration injection.
- Preserve non-Git unresolved behavior.

### 5. Agent Introspection consumption and cutover

Keep Agent Introspection a validator and consumer of producer-emitted attribution.

- Verify native CLI and Desktop spans satisfy the canonical schema in SigNoz.
- Verify derived dashboard-facing events retain paired project ID and name.
- Remove the temporary CLI launcher and its command-specific source handling once the upstream Codex build is deployed and verified.
- Retain no compatibility path that writes project identity through static Codex configuration or downstream inference.

## Validation

1. Core unit tests prove canonical Git resolution and all unresolved boundaries.
2. CLI integration test starts a Git session and verifies complete native span tuples.
3. Desktop/app-server integration test starts concurrent threads in different Git workspaces and proves isolation.
4. Resume and subagent scenarios preserve the originating canonical project tuple.
5. SigNoz queries verify complete, consistent tuples on fresh source spans.
6. Agent Introspection scan verifies derived events with paired dashboard project fields.
7. Run the upstream Codex quality gates and this repository's quality gates after each repository changes.

## Acceptance criteria

- Direct Codex CLI and Codex Desktop both emit producer-owned canonical project tuples without external project-attribute configuration.
- No process-global OTel project attributes exist.
- No span trace contains conflicting tuples.
- Non-Git and ambiguous contexts remain unresolved.
- Fresh native telemetry is accepted by Agent Introspection and appears in project-filtered dashboard data.
- The temporary launcher has been removed after verified native cutover.
