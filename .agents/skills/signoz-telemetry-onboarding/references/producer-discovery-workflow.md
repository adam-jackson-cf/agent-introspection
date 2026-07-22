# Producer discovery workflow

## Objective

Classify every user-requested local agent harness by its project-metadata emission capability.

## Required actions

1. Capture harnesses explicitly named by the user.
2. Ask before auto-discovering local harnesses when the request names none.
3. Inspect each requested harness configuration and supported emitter surface without exposing secrets.
4. Measure existing project-schema attributes in SigNoz where telemetry exists.
5. Classify every requested harness as natively configurable, source-change-required, or unsupported.
6. Reject static process-level project attributes for multiplexed or multi-workspace producers.

## Done when

- Every requested producer has a capability classification and evidence.
