# Provenance import workflow

## Objective

Import only classifications that are complete, bounded, and traceable to the exported envelope.

## Required actions

1. Compare the returned envelope identifier with the exported envelope identifier.
2. Verify ordered candidate identifiers and candidate count match the exported envelope.
3. Verify review-run telemetry correlation and current capability proof.
4. Reject results with missing provenance, mismatched ordering, incomplete candidates, or exceeded bounds.
5. Import accepted classifications through the authorised local import path.
6. Record imported and rejected counts with the rejection reason category.

## Done when

- Every imported classification has validated envelope and review-run provenance.
- Rejected results are not imported.
