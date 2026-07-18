# Candidate export workflow

## Objective

Produce a bounded classification envelope without altering persisted findings, proposals, or repository files.

## Required actions

1. Select the classification operation only; do not run proposal or repository actions.
2. Export candidates using the existing candidate and token limits.
3. Preserve the envelope identifier, ordering, candidate identifiers, bounds, and source provenance.
4. Stop when no candidates satisfy the approved bounds.
5. Record whether the result is a valid envelope or no candidates.

## Done when

- The envelope is valid and bounded, or no candidates are available.
- Candidate identity, ordering, and provenance are preserved.
