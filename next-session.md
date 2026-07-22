# Handoff: Roll out canonical project attribution to producers

## Starting Prompt

Continue the producer rollout for canonical Agent Introspection project attribution. Start by asking which producer harnesses to onboard if the request does not name them, then invoke `.agents/skills/introspection-onboarding/SKILL.md`. For each requested producer, establish SigNoz ingestion health, classify its real configuration or source-emission capability, load the canonical JSON schema, and implement or hand off the producer-side change. A producer must emit the complete `agent.project.id`, `agent.project.name`, `agent.project.root`, and `agent.project.kind` tuple together on every relevant session or action span. Do not derive attribution from CWD, paths, aliases, prompts, or thread inference. Validate a fresh known-project session in SigNoz and reject absent, partial, conflicting, or invalid tuples before approving attribution.

## Relevant Files

- `.agents/skills/introspection-onboarding/SKILL.md` — repository-local rollout workflow for requested harnesses.
- `.agents/skills/introspection-onboarding/references/canonical-schema-workflow.md` — resolves the repository-owned schema without copying it.
- `src/agent_introspection/schemas/otel/agent-project.schema.json` — canonical versioned OTEL contract.
- `src/agent_introspection/project_schema.py` — package loader and structural validation for the contract.
- `src/agent_introspection/source.py` — trace query and fail-closed parsing of producer project metadata.
- `src/agent_introspection/dashboard.py` — dashboard SQL consumes schema-owned ID/name keys.
- `src/agent_introspection/generations.py`, `scan.py`, and `telemetry.py` — derived-event emitters consume schema-owned keys.
- `tests/test_source.py` and `tests/test_dashboard.py` — coverage deriving expected values from the canonical schema.

## Key Context

- `245b073 feat: add canonical project telemetry schema` introduced the central JSON schema, package loader, schema-backed consumers, and onboarding skill.
- `befd46c chore: rename introspection onboarding skill` renamed the skill from `signoz-telemetry-onboarding` to `introspection-onboarding`.
- The full quality suite passed after the schema cutover: 183 tests. The built wheel includes `agent_introspection/schemas/otel/agent-project.schema.json`; `agent-introspection --help` also passed.
- The onboarding workflow intentionally distinguishes configurable, source-change-required, and unsupported producers. It does not cause external harnesses to emit metadata by itself.
- Existing attribution is deliberately fail-closed: project name alone is insufficient, and unresolved producers remain unresolved rather than using a fallback.
