# Preflight workflow

## Objective

Establish that a bounded classification can proceed safely against the approved local data perimeter.

## Required actions

1. Confirm the health check passes for the approved source and database.
2. Confirm the source contract is approved and current.
3. Confirm a current correlated capability proof exists for the classification operation.
4. Stop and report the failed or expired gate without exporting candidates.
5. Record the validated health, source-contract, and capability-proof identifiers.

## Done when

- All required gates are current and valid.
- A failed or expired gate stops the workflow before candidate export.
