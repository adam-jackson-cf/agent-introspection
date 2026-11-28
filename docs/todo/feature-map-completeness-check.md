# Feature map completeness check

Track: priority: CLI data access / problem and friction surfacing

## Problem

`docs/features/` is the feature map the verification-surface check enforces, but the check validates
only each document's shape. It never compares the map with the dashboard views or the CLI commands, so
"one document per feature" is a manual duty. Every CLI feature group (dashboard JSON commands, setup,
doctor, facts, hooks and spool, harness, producers) has no feature document.

## Evidence

- `scripts/check-verification-surface.py:78-113`: checks the four H2 sections, relative links and a test
  link per document, and that at least one document exists.
- `scripts/check-verification-surface.py` never reads `dashboard/src/routes.tsx` `NAV_GROUPS` or the CLI
  parsers.

## Proposed change

Extend the check to require a feature document per `NAV_GROUPS` view and per top-level CLI command, with
an explicit allowlist for commands that do not need one. Then write the missing CLI feature documents.

## Acceptance check

The check fails when a new view or top-level command has no document, and passes on the current tree
once the CLI documents exist.
