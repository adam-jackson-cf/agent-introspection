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
- `agent.project.root` is a normalized absolute path and `agent.project.kind` is `git` or `non_git`.
- Never write project attributes globally in `~/.codex/config.toml`; Codex applications can host different workspaces.
- Do not infer a project in Agent Introspection from CWD, a path heuristic, aliases, prompts, or thread history.
- Retained telemetry without the tuple remains unresolved. Do not backfill it through a new inference path.

## Phase 1 — Codex CLI producer rollout

### Scope

Implement a local producer adapter in this repository for one-workspace Codex CLI processes. Codex 0.144.6 already accepts `otel.span_attributes` and applies them to every exported span through its `SpanAttributesProcessor`.

### Deliverables

1. Add an `agent-introspection codex` command that launches Codex with invocation-scoped `otel.span_attributes`.
2. Require an explicit project metadata document as command input. It must contain the complete canonical tuple; the command validates it against `agent-project.schema.json` before starting Codex.
3. Validate the requested Codex executable and preserve all remaining arguments without shell interpolation.
4. Reject absent, partial, malformed, or conflicting metadata. Do not derive IDs or display names from CWD, directory names, Git remotes, or paths.
5. Emit structured launch evidence that identifies the executable and validated project identifier without recording prompts, credentials, or arbitrary arguments.
6. Add behavioral tests for accepted full tuples and rejected absent, partial, invalid-root, and invalid-kind metadata.
7. Start a fresh Codex CLI session using a known project document. Query recent SigNoz spans and require the full tuple on all relevant source spans for that session.
8. Run Agent Introspection scan and verify resulting observations carry paired project ID and project name.

### Success criteria

A fresh standalone Codex CLI session produces only complete canonical project metadata on its relevant source spans, and its derived observations are project-attributed.

## Phase 2 — Codex app-server producer rollout

### Scope

Implement session-scoped project telemetry in the upstream `openai/codex` source. This is required for `codex-app-server`; its current `otel.span_attributes` map is process-global and cannot safely identify concurrent or sequential workspaces.

### Required upstream change

1. Add a validated `AgentProjectMetadata` type with the four canonical fields.
2. Carry it in `SessionTelemetryMetadata` from session creation through resumed sessions and subagents.
3. Attach it to all relevant session/action spans and telemetry events, including `run_sampling_request`, `session_task.turn`, `turn/start`, `turn/steer`, `turn/interrupt`, and `handle_responses`.
4. Do not use the process-level `SpanAttributesProcessor` for this metadata.
5. Reject sessions whose explicit metadata is absent, partial, invalid, or conflicts within a trace.
6. Add concurrent multi-workspace app-server tests proving no project tuple crosses a session boundary.
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
