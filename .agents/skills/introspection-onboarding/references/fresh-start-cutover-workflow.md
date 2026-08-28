# Fresh-start cutover workflow

## Objective

Retire approved historical telemetry and establish an empty canonical baseline without weakening future producer attribution.

## Guidance

- Obtain explicit approval for the exact historical local and remote row sets before deletion.
- Preserve an immutable inventory outside automatic import paths with counts, event IDs, timestamps, project tuples, and checksums.
- Keep the scheduler stopped and drain the context inbox and canonical outbox before the cutover.
- Prove every retained producer with the end-to-end validation workflow before deleting historical telemetry.
- Require fresh, resume, concurrent-project, workspace-change, end, and non-Git scenarios wherever the native producer exposes the corresponding session-context or lifecycle behavior.
- Verify each scenario through the managed adapter or proxy, canonical runtime, context ledger, accepted project evidence, source activity, canonical activity version, canonical outbox event, remote event ID, and dashboard source-time query.
- Delete only the approved bounded historical population through a separately proven operation, wait for completion, and verify zero matching rows.
- Reset local projection state only through a rehearsed database migration that preserves canonical runtime configuration, source schema approval, context contracts, and immutable operational evidence required for the fresh baseline.
- Start one new session per supported producer after the reset and require the first canonical activity version to carry the same project ID and project name as its accepted context interval.
- Reinstall the scheduler only after every supported producer passes and the dashboard returns only post-cutover canonical activity IDs.
- Stop and restore before producer configuration changes when any count, checksum, stable ID, project tuple, integrity check, foreign key check, remote event, or dashboard result differs.
- Report the approved deletion bounds, backup checksum, zero-row proof, producer scenario results, canonical activity and outbox IDs, dashboard stable-ID comparison, and scheduler state.
