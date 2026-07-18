# Classification workflow

## Objective

Produce candidate-level classifications from a valid envelope without accessing source content or secrets.

## Required actions

1. Confirm that the supplied capability proof is current for the requested classification operation.
2. Process candidates in the envelope order and within the supplied limits.
3. Use only the candidate content and provenance provided by the envelope.
4. Record one classification result for each processed candidate.
5. Return the envelope identifier, ordered candidate identifiers, classifications, and review-run telemetry correlation.

## Done when

- Every returned classification matches a candidate in the envelope.
- The result contains the required provenance and telemetry correlation.
