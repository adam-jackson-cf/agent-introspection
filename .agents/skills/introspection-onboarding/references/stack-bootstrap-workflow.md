# Stack bootstrap workflow

## Objective

Prove the local SigNoz stack can receive and retain OTLP before configuring session-context producers.

## Required actions

1. Check loopback-only UI, collector health, and OTLP listeners.
2. Inspect effective collector pipelines without exposing secrets.
3. Run synthetic trace, metric, and log probes.
4. Verify that new backend records arrived after each probe.
5. Record the first failed boundary and stop without weakening security.

## Done when

- The stack is healthy and independent ingestion is proven.
- Any failed boundary is explicitly reported.
