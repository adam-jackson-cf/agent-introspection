# Model review workflow

## Objective

Perform bounded model review with validated provenance and deterministic imports.

## Required actions

1. Export eligible candidates and create a persisted review session.
2. Dispatch only the specified model and effort through a model-specific background subagent.
3. Enforce all review limits and validate the response envelope.
4. Verify actual SigNoz model and token telemetry before continuing.
5. Import only validated results and defer all unprocessed candidates unchanged.

## Done when

- Imported outputs match the requested session, model, effort, schema, and candidates.
- Every call has verified provenance or the workflow has halted safely.
