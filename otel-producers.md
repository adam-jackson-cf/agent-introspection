# Sustainable Codex Project Attribution Decision Plan

## Purpose

Determine whether Codex can provide canonical project attribution without a locally maintained binary patch, process-global project configuration, a proxy, or downstream inference.

No implementation begins until this plan is approved and its upstream release gate is met.

## Learned constraints

- Codex CLI and Codex Desktop/app-server both own the workspace context required for safe attribution.
- Codex's existing `otel.span_attributes` configuration is process-global. It cannot safely attribute a multi-workspace Desktop app-server process.
- An external CLI launcher can scope values to one CLI invocation, but cannot attribute Codex Desktop app-server sessions.
- A local Codex source patch can cover both clients, but every Codex upgrade would require patch maintenance, rebuild, and revalidation.
- The installed Codex release has no supported per-session project-attribution configuration or extension point.

## Non-negotiable rules

- The canonical contract remains `src/agent_introspection/schemas/otel/agent-project.schema.json`.
- Project identity must be producer-owned and must not be inferred by Agent Introspection from CWD, paths, aliases, prompts, thread state, or historical telemetry.
- Static process-level OpenTelemetry project attributes must not be used.
- No local fork, binary replacement, upgrade-time patch replay, launcher, proxy, or compatibility attribution path is an acceptable production solution.
- Unsupported producer contexts remain unresolved rather than receiving guessed attribution.

## Decision gates

### Gate 1: Upstream maintainership

Create an evidence-backed upstream Codex proposal for a supported per-session project-attribution capability.

The proposal must establish:

1. The authoritative source of workspace identity for CLI and Desktop app-server sessions.
2. The supported public API or configuration contract for carrying canonical project metadata.
3. Session and multi-workspace isolation semantics.
4. A maintainer-approved delivery path and target released Codex version.
5. A migration and deprecation path that does not require users to maintain a local fork.

Stop if maintainers reject the capability, decline a release commitment, or require an unsupported local patch.

### Gate 2: Released native capability

After an official Codex release exposes the approved capability:

1. Configure only the supported producer interface.
2. Start fresh Git and non-Git sessions through direct CLI and Codex Desktop.
3. Verify source spans carry the complete canonical tuple only in authoritative Git contexts.
4. Verify concurrent Desktop workspaces cannot leak or conflict.
5. Verify resumed sessions and action spans preserve valid attribution semantics.

Stop if the released capability is process-global, CLI-only, or cannot isolate Desktop sessions.

### Gate 3: Consumer verification

After producer verification:

1. Verify Agent Introspection accepts the complete native tuple without a Codex-specific inference path.
2. Verify derived dashboard-facing events retain paired project ID and name.
3. Measure attribution from fresh, producer-emitted sessions separately from historical unresolved data.
4. Remove no producer feature until the released native capability has passed end-to-end validation.

## Current operating state

Codex project attribution is unsupported in the installed release for the required CLI-and-Desktop scope. Codex telemetry remains ingested and unresolved. Agent Introspection must report that limitation accurately.

## Acceptance criteria

- An upstream-maintained, released Codex capability supports session-scoped project attribution for both CLI and Codex Desktop.
- The capability satisfies the canonical schema without static process attributes or downstream inference.
- No locally patched Codex binary, external launcher, proxy, or upgrade-time patch maintenance is required.
- Fresh producer telemetry is validated end to end before any dashboard coverage claim.
