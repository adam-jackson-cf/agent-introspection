# Project-attribution Gate 4 decision record

## Decision, scope, and evidence

**Gate 4 verdict: APPROVED — the independent oracle and its retained evidence are accepted. Current join/materialization and dashboard implementations are NOT approved; both are failed by this gate.** This is an evidence and comparison decision, not approval of source, local state, remote state, installed capabilities, or implementation changes.

The frozen source interval is `[2026-08-06T14:53:18.170773000Z, 2026-08-07T17:45:58.546340000Z)`. Gate 1 fixes the authority boundary: the shared runtime is the sole project resolver; association is namespaced exact `(canonical producer, native session ID)` to accepted immutable context; activity and dashboard project fields are derived projections, never a second resolver. [Gate 1, “Approved authority, keys, and materialization”; “Approved temporal and disposition semantics”] Gate 2 approved the reproducible population and classifications, but not installed support or dashboard behavior. [Gate 2, “Decision and frozen scope”] Gate 3 approved only installed-capability classification and expressly did not infer a join or dashboard result from callback evidence. [Gate 3, “Decision and evidence boundary”]

All values below come only from the retained Gate 4 artifacts: `project-attribution-gate-4-join.json`, `project-attribution-gate-4-projections.json`, `project-attribution-gate-4-dashboard.json`, and `project-attribution-gate-4-coverage.json`.

## Phase 4 decision steps

1. **Freeze the independent expected join set — passed.** The oracle independently classified all 12,830,131 included source records exactly once. Its canonical JSONL is `gate-4-expected-source.jsonl`, serialized as compact, sorted-key objects in input order, SHA-256 `ae5f324f75e0dc08f715054407ea39153a574bf765c019f67f321622ed05ab5a`. The included-source input is 12,830,131 records, SHA-256 `d50b6a91ae6f2a3f4a9e8e8ff6fb774fee5d14b53a00c72eeaa0c6f476778b7b`; the raw input is 12,831,230, SHA-256 `5fb339ca81cf78d63f960fdb8d342a6ecf683856f8ded1269f1940d38668850d`. [Gate 4 join, `expected_source`, `inputs`, `conservation`]
2. **Load authority context independently of the telemetry range — passed for the oracle.** It loads all 50 local-context records (30 accepted-context events; 20 intervals), SHA-256 `381f22dd6ca28f5eddaeb3385fb7459d2f1e11505dd73dee4290bb33123d4d44`, independently of the selected source interval. True lifecycle intervals are half-open: `started_at <= source_timestamp_ns < ended_at` when ended. [Gate 4 join, `authority`, `inputs.accepted_context`; Gate 1, “Current materialized path”]
3. **Prove strict producer namespace and native-session association — passed for the oracle.** The only association is exact canonical producer plus exact native session ID; cross-producer joins are zero. Missing or conflicting native keys remain failures, and project is present only for attributed rows. A unique Codex CLI completion context may map earlier telemetry only in that same exact producer/session; it never borrows app-server or another producer. [Gate 4 join, `authority`, `join_safety`; Gate 4 coverage, `frozen_population.authority_semantics`]
4. **Compare the independently recomputed outcomes with Gate 2 — outcome set passed; reason labels diverge.** All terminal outcomes and projects match Gate 2 (`outcome_mismatch_count = 0`), while 946 Codex CLI active-interval rows are labeled `exact_context_interval` by Gate 4 and `unique_codex_completion_context` by Gate 2. Thus `reason_or_project_mismatch_count = 946`, entirely reason-label differences; this does not alter the approved expected outcome set. [Gate 4 join, `gate_2_comparison`, `result`]
5. **Compare local and remote materialized projections against authority — failed implementation.** All 17 local activity versions equal their frozen remote materializations after unresolved-value normalization, but only 12 equal the expected authority projection. Five earlier same-session Codex CLI activities have temporal-selection errors; there are no identity mismatches, missing hook/context cases, or upstream-materialization errors. Reconciliation rows SHA-256 `be37688f4be2ebce4525ae04e9adbd577adea912413cdc38e08ee98efde98bc7`; divergence JSONL SHA-256 `a89a6c4ee419ab72f132f313226843ad70d3146713f530a06decbca07fd3a4b7`. [Gate 4 projections, `authority`, `counts`, `projection_equality`, `checksums`]
6. **Compare the dashboard result set with the authority set — failed implementation.** The expected exact tuple set contains 14 tuples (6 `codex-cli`, 8 `omp`), keyed by `(producer, native_session_id, project_id, project_kind, project_name, project_root)`, SHA-256 `cff0b66a2613bdd3ae3a511fe8bb55bd5e2e436b88a8be6deef847332c5e333f`. The persisted dashboard exposes zero exact tuple rows: 14 omissions, zero extras, and zero exact mismatches. It exposes only `codex-cli/codex-cli`, omitting the expected OMP surface; its `Attributed=4` activity count is not comparable to the 14 tuple set. [Gate 4 dashboard, `expected_exact_set`, `comparison`, `persisted_dashboard_sql`]
7. **Measure coverage and classify divergences — complete; implementation approval denied.** Population conservation holds (`12,831,230 = 12,830,131 + 1,099`) and terminal conservation holds (`12,830,131 = 3,279 + 12,826,851 + 1`). The accepted oracle has complete terminal classification, but the projection and dashboard divergences above make the current implementation failed. [Gate 4 coverage, `aggregate`; Gate 4 join, `conservation`; Gate 4 projections, `status`; Gate 4 dashboard, `status`]

## Independent expected outcome set and divergence classes

| Terminal outcome/reason | Records |
|---|---:|
| Attributed / `exact_context_interval` | 965 |
| Attributed / `unique_codex_completion_context` | 2,314 |
| Blocked / `missing_gate_3_identity_proof` | 1 |
| Failed / `accepted_context_missing` | 107,097 |
| Failed / `native_session_key_conflicting` | 5,576 |
| Failed / `native_session_key_missing` | 12,714,178 |

The independent result is 3,279 attributed, 12,826,851 failed, and 1 blocked. It retains failures rather than excluding them. [Gate 4 join, `outcomes_by_reason`, `conservation`]

Divergence classes are: (a) Gate 2 reason-label difference, 946 records; (b) local/remote temporal selection error, 5 of 17 activities; and (c) dashboard `missing_exact_tuple`, 14, `missing_producer_surface`, 1, and `context_range_coupling`, 1 (16 dashboard divergence records). The dashboard's accepted-context CTE applies the display-range `COMMON_FILTER`: six context records lie outside the displayed range, including one authority-relevant OMP record, so it fails Gate 1's unrestricted exact-key context lookup rule. [Gate 4 join, `gate_2_comparison`; Gate 4 projections, `counts`; Gate 4 dashboard, `divergences`, `context_range_test`]

## Coverage oracles

The five coverage oracles are classification coverage, session attribution coverage, source-event attribution coverage, project-tuple accuracy, and population conservation. Percentages use `round(100 * numerator / denominator, 12)`; a zero denominator is `Blocked` with no percentage. Detector yield is deliberately not a coverage denominator.

| Oracle | Formula | Aggregate |
|---|---|---|
| Classification coverage | `classified_terminal_outcome_events / included_source_events` | 12,830,131 / 12,830,131 = 100% |
| Session attribution coverage | `attributed_exact_native_sessions / included_valid_native_sessions` | 14 / 84 = 16.666666666667% (6 missing/conflicting groups retained) |
| Source-event attribution coverage | `attributed_source_events / included_source_events` | 3,279 / 12,830,131 = 0.025557026659% |
| Project-tuple accuracy | attributed source events equal to the single exact producer/session accepted tuple / attributed source events with a tuple | 3,279 / 3,279 = 100% (0 unverifiable/mismatched) |
| Population conservation | `raw_source_events = included_source_events + excluded_source_events` | 12,831,230 = 12,830,131 + 1,099; conserved |
| Detector yield | not a coverage denominator | N/A |

| Producer | Classification | Session attribution | Source-event attribution | Project-tuple accuracy | Population conservation | Detector yield |
|---|---|---|---|---|---|---|
| `claude-code` | 1/1 = 100%; 0 attributed, 1 blocked | 0/1 = 0% | 0/1 = 0% | 0/0, Blocked | 17 = 1 + 16 | N/A |
| `codex-app` | 0/0, Blocked | 0/0, Blocked | 0/0, Blocked | 0/0, Blocked | 0 = 0 + 0, Blocked | N/A |
| `codex-app-server` | 1,593,377/1,593,377 = 100%; all failed | 0/9 = 0% | 0/1,593,377 = 0% | 0/0, Blocked | 1,593,377 = 1,593,377 + 0 | N/A |
| `codex-cli` | 11,229,882/11,229,882 = 100%; 3,260 attributed, 11,226,622 failed | 6/51 = 11.764705882353% | 3,260/11,229,882 = 0.029029690606% | 3,260/3,260 = 100% | 11,229,882 = 11,229,882 + 0 | N/A |
| `omp` | 6,871/6,871 = 100%; 19 attributed, 6,852 failed | 8/23 = 34.782608695652% | 19/6,871 = 0.276524523359% | 19/19 = 100% | 7,843 = 6,871 + 972 | N/A |

Per-producer session denominators retain missing/conflicting groups: Claude 0, app 0, app-server 2, CLI 2, and OMP 2. Project-tuple accuracy is blocked where no event is attributed; it is not treated as 100%. [Gate 4 coverage, `aggregate`, `surfaces`, `method`]

## Gate 5 requirements

Gate 5 downstream work MUST recompute/use the exact namespaced `(canonical producer, native session ID)` join against accepted context, retain missing/conflicting key or context cases as terminal failures/blocks, and permit the narrowly defined unique same-session Codex CLI completion-context association only. It MUST NOT use activity, remote, or dashboard project fields as an independent resolver; those are projections only. Context lookup MUST be range-independent: select telemetry in the requested display/source range, but load accepted context without that range filter before exact-key association. It MUST preserve half-open true-lifecycle interval semantics and must eliminate the five temporal-selection and dashboard tuple/range-coupling failures before any implementation approval. [Gate 1, “Approved authority, keys, and materialization”, “Current materialized path”; Gate 4 join, `authority`, `join_safety`; Gate 4 projections, `counts`; Gate 4 dashboard, `authority`, `context_range_test`, `comparison`]
