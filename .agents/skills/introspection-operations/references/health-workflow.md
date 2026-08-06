# Health workflow

## Objective

Establish verified readiness without bypassing failed capabilities.

## Required actions

1. Run health, source-schema, database, dashboard, canonical outbox, and scheduler checks.
2. Verify the network perimeter remains loopback-only.
3. Verify the managed runtime and configured producer adapters are present before a scan or fresh-session proof.
4. Record context inbox counts, canonical activity/version counts, pending and failed outbox rows, and the scheduler's latest terminal state.
5. Stop on source-schema drift, database failure, unsafe exposure, missing runtime configuration, unresolved required producer support, or unavailable services.

## Done when

- Every required check has recorded evidence.
- Failures and unverified producer, ledger, outbox, scheduler, or dashboard capabilities are surfaced explicitly.
