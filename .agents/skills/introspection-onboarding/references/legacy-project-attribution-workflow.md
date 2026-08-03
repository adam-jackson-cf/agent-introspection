# Legacy project attribution workflow

## Objective

Map eligible historical telemetry to verified projects without converting ambiguous legacy data into false attribution.

## Guidance

- Establish one bounded UTC analysis window and measure the current attributed numerator, total denominator, producer split, and active generation before changing persisted state.
- Keep every before-and-after comparison on the same event population, time bounds, generation filter policy, and project-tuple completeness rule.
- Inventory unmatched cohorts with count-only queries grouped by producer, explicit session or conversation key, trace linkage, workspace evidence state, and rejection reason.
- Read only allowlisted producer-owned session metadata and tool-workspace or tool-target fields; never retain prompts, responses, command output, environment values, or arbitrary nested content.
- Treat a direct tool record as candidate evidence only when it carries an explicit producer key, explicit session or conversation key, timestamp, and absolute workspace under a configured project collection.
- Normalize the existing workspace and Git top-level, reject symlink escapes and cross-collection roots, and use bounded argv-only Git plumbing with terminal prompting disabled.
- When a Git object or target is part of the evidence, require an exact supported object ID, verify repository membership and object type, require target containment, and require tracked-file membership where the contract calls for it.
- Use strict command-and-result pairing, remote fingerprints, or target evidence only as corroboration; never parse generic hexadecimal strings or infer a project from command prose.
- Reject absent, partial, conflicting, ambiguous, non-Git, inaccessible, multi-root, open-ended, inverted, or unknown-transition evidence.
- Attribute the direct source record that supplied valid evidence; create a wider interval only when all accepted anchors for the same producer/session key resolve to one project and bound a closed, conflict-free interval.
- Persist immutable evidence with stable IDs, exact timestamps, canonical project identity, evidence method, anchor count, and source provenance. Preserve rejected and unresolved counts without manufacturing placeholder attribution.
- Apply the same resolver and interval-containment rules in incremental scans and historical reanalysis so replayed facts match production behavior.
- Reimport only the approved bounded window, create an immutable fact set, stage a generation, drain its events, verify exact remote event IDs, and activate only after remote verification succeeds.
- Run a fresh scan, verify the hourly scheduler's installed state and latest terminal slot, then measure the active generation with the original denominator contract.
- Report accepted direct records, accepted intervals, ambiguous and rejected cohorts, unresolved remainder, generation identifiers, and the exact before-and-after percentage.
