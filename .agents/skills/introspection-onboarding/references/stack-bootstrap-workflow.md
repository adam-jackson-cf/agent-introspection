# Stack bootstrap workflow

## Objective

Provision and verify local SigNoz ingestion safely before onboarding producers.

## Required actions

1. Check UI health, collector health, OTLP gRPC and HTTP listeners, and loopback-only exposure.
2. Inspect effective collector pipelines without exposing secrets.
3. Run synthetic trace, metric, and log probes.
4. Verify that new backend records arrived after each probe.
5. Record the first failed boundary and stop without weakening security.

## Done when

- The stack is healthy and independent ingestion is proven.
- Any failed boundary is explicitly reported.
