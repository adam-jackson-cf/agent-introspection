# Health workflow

## Objective

Establish verified readiness without bypassing failed capabilities.

## Required actions

1. Run health, schema, database, dashboard, telemetry, and scheduler checks.
2. Verify the network perimeter remains loopback-only.
3. Stop on source-schema drift, database failure, unsafe exposure, or unavailable required services.

## Done when

- Every required check has recorded evidence.
- Failures and unverified capabilities are surfaced explicitly.
