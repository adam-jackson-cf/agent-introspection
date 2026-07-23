# Codex OpenTelemetry Project Attribution Rollout

## Objective

Make Codex emit the canonical project tuple on every relevant session and action span consumed by Agent Introspection:

- `agent.project.id`
- `agent.project.name`
- `agent.project.root`
- `agent.project.kind`

The tuple is source-owned. Agent Introspection must not reconstruct it from the exported `cwd` attribute.

## Constraints

- A tuple is valid only when all four fields are present together on the same span and consistently across its trace.
- `agent.project.root` is a normalized absolute path and `agent.project.kind` is `git`.
- Never write project attributes globally in `~/.codex/config.toml`; Codex applications can host different workspaces.
- Codex resolves Git project context at session creation. A non-Git workspace remains unresolved and emits no project tuple.
- Agent Introspection does not reconstruct project identity from exported CWD, a path heuristic, aliases, prompts, or thread history.

## Phase 1 — Codex CLI producer rollout

### Scope

Implement a local producer adapter in this repository for one-workspace Codex CLI processes. Codex 0.144.6 already accepts `otel.span_attributes` and applies them to every exported span through its `SpanAttributesProcessor`.

### Deliverables

1. Add an `agent-introspection codex` command that launches Codex with invocation-scoped `otel.span_attributes`.
2. Resolve Git context in the producer from the requested workspace: use Git's common directory to establish a stable project root, derive the canonical ID as `git:` plus the SHA-256 digest of `git`, a NUL separator, and that normalized root, and use the root basename as the display name.
3. For a workspace outside Git, launch Codex without project attributes so the session remains unresolved.
4. Validate the requested Codex executable and preserve all remaining arguments without shell interpolation.
5. Emit structured launch evidence that identifies the executable and attribution state without recording prompts, credentials, arbitrary arguments, or project paths.
6. Add behavioral tests for Git discovery, worktree identity stability, non-Git unresolved operation, invalid Git output, command construction, and argument preservation.
7. Start a fresh Codex CLI session in a known Git project. Query recent SigNoz spans and require the full tuple on all relevant source spans for that session.
8. Run Agent Introspection scan and verify resulting observations carry paired project ID and project name.

### Success criteria

A fresh standalone Codex CLI session produces only complete canonical project metadata on its relevant source spans, and its derived observations are project-attributed.

## Phase 2 — Codex app-server producer rollout

### Scope

Implement session-scoped project telemetry in the upstream `openai/codex` source. This is required for `codex-app-server`; its current `otel.span_attributes` map is process-global and cannot safely identify concurrent or sequential workspaces.

### Required upstream change

1. Add a validated Git project-context resolver with the same identity rules as Phase 1.
2. Carry its `AgentProjectMetadata` result in `SessionTelemetryMetadata` from session creation through resumed sessions and subagents; non-Git sessions remain unresolved.
3. Attach a complete tuple to all relevant session/action spans and telemetry events, including `run_sampling_request`, `session_task.turn`, `turn/start`, `turn/steer`, `turn/interrupt`, and `handle_responses`.
4. Do not use the process-level `SpanAttributesProcessor` for this metadata.
5. Reject malformed or conflicting Git resolution results within a trace.
6. Add concurrent multi-workspace app-server tests proving no project tuple crosses a session boundary and non-Git sessions remain unresolved.
7. Release or build a Codex version containing the upstream change, configure the local app-server to use it, and repeat Phase 1's SigNoz and Agent Introspection validation.

### Success criteria

Concurrent app-server sessions in different projects emit their own complete tuple on relevant spans without cross-session attribution.

## Historical telemetry

Do not reimport retained CWD-only telemetry as canonical project attribution. It has no source-emitted tuple. Measure the rollout from fresh producer sessions onward and retain earlier observations as unresolved.

## Verification gates

1. `uv run ruff check .`
2. `uv run ruff format --check .`
3. `uv run mypy src`
4. `uv run pytest`
5. Fresh Codex producer smoke test and SigNoz query.
6. `uv run agent-introspection scan`
7. `uv run agent-introspection schedule status`
