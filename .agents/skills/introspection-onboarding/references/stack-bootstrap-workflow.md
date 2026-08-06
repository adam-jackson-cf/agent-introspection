# Stack bootstrap workflow

## Objective

Prove the local SigNoz stack can receive and retain OTLP before configuring session-context producers.

## Guidance

- Check loopback-only UI, collector health, OTLP listeners, and disabled LAN exposure.
- Inspect effective collector pipelines without exposing secrets.
- Run synthetic trace, metric, and log probes.
- Verify that new backend records arrived after each probe rather than relying on historical totals.
- Record the first failed boundary and stop without weakening security.
- Complete this workflow only when independent ingestion is proven and every failed capability is explicit.
