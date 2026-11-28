# Facts history backup

Track: priority: CLI data access / problem and friction surfacing

## Problem

The `introspection` tables hold history older than SigNoz's 90-day retention, and that history is the
only copy. The docs tell the user to back it up before any SigNoz upgrade, but the repository has no
backup or restore tooling.

## Evidence

- `docs/references/facts-store.md` "Retention and backups": "back it up before any SigNoz upgrade or
  stack recreation once the store holds more than 90 days".
- `git ls-files | grep -i backup` finds nothing.

## Proposed change

Add `facts backup` and `facts restore` commands (or a script) that export the `introspection` tables
older than 90 days, `hook_events` included, to a local directory, and restore them. Make `doctor` warn
when the store holds more than 90 days and no recent backup exists.

## Acceptance check

A backup and restore round trip on a test store returns the same row counts per table.
