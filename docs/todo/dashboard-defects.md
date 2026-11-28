# Dashboard defects

Track: priority: CLI data access / problem and friction surfacing.

## Problem

These are objective defects in today's dashboard: wrong or ambiguous figures, data cut without notice, and
contradictions between pages. They were confirmed in the code on 2026-11-28. Layout and emphasis opinions from
the earlier UX reviews were dropped; a fresh review of the current dashboard will judge those again.

## Evidence

| #   | Defect                                                                                          | Evidence                                                                                                             | What the user sees                                                                                |
| --- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 1   | Opening a session writes that row's harness into the filter, and it stays when you navigate on  | `dashboard/src/App.tsx` `sessionHref`; `ViewNav` reuses `filters`                                                    | Every later page is quietly limited to one harness                                                |
| 2   | The route info note shows 0 when there is no count                                              | `dashboard/src/components.tsx` `fmtCount(rows ?? 0)`                                                                 | "0 rows on this route" reads as a true zero                                                       |
| 3   | Refresh never reloads the registry after the first load                                         | `dashboard/src/app-data.ts` `reload` returns once `loaded` is set                                                    | A newly enabled harness or a reinstall stays invisible until a full page reload                   |
| 4   | Seconds round wrongly at a unit boundary                                                        | `dashboard/src/format.ts` `fmtSeconds`                                                                               | 59.96 s shows "60 s", not "1.0 min"; 9.96 s shows "10.0 s"                                        |
| 5   | Cache "By model" is cut at 40 rows with no notice                                               | `dashboard/server/views.ts` cache by-model query `LIMIT 40`, no `total_rows`                                         | Models past 40 vanish with no "Showing 40 of N"                                                   |
| 6   | Friction "latest tasks" is cut at 25 rows with no notice                                        | `dashboard/server/views.ts` friction latest-tasks query `LIMIT 25`, no `total_rows`                                  | Older friction tasks vanish silently                                                              |
| 7   | Session tool-call times have no date                                                            | `dashboard/src/views/Session.tsx` `slice(11, 19)` on the call time                                                   | Times are ambiguous in a session that spans days                                                  |
| 8   | Session tasks older than about 90 days lose their labels                                        | Session queries read live views, but `task_labels` reads `task_outcomes_snapshot` (`facts_sql/004_labels.sql`)       | Old tasks show "—" for task type and corrected-next                                               |
| 9   | Pipeline "All = Σ harnesses" ignores the harness filter, and its subtitle does not say so       | `dashboard/server/pipeline.ts` `recombinationQueries` has no harness predicate                                       | With one harness selected, the panel still covers every harness                                   |
| 10  | Source parity "Match" also compares cached and output tokens, which the table does not show     | `dashboard/server/pipeline.ts` parity comparison vs the columns in `Pipeline.tsx`                                    | A row can say "possible break" while every visible number matches                                 |
| 11  | Daily charts mix UTC days and Europe/London days                                                | Most `views.ts` daily queries use `toDate(ts)`; Recurrence and the Interventions baseline use Europe/London          | Requests and corrections and the Interventions baseline give different daily rates for one metric |
| 12  | Interventions matched counts can count one task twice (fix cycle, behind the experimental flag) | `views.ts` `cluster_daily` counts distinct tasks per day; `server/interventions.ts` sums them across days            | A task failing on two days counts as 2 against a denominator that counts it once                  |
| 13  | The titles manifest is missing 17 rendered titles                                               | `dashboard/src/feature-audit.titles.json` against the rendered titles (for example "Hook spool by host")             | Those titles can be removed without the test failing                                              |
| 14  | Generic manifest titles guard nothing                                                           | `dashboard/src/feature-audit.test.ts` matches against all source text, not the page ("At a glance" appears 10 times) | Removing such a section from one page passes the test                                             |
| 15  | The UX probe's scoring step cannot run                                                          | `scripts/ux-probe/score.ts` needs `~/.agents/by-request/setup-ux-standards/scripts/score_rubric.py`, which is absent | `score.ts` fails on this machine                                                                  |

Unverified from code alone (needs data):

- Friction "Observes" uses `any(friction_observed)` (`views.ts`); it is only arbitrary if the list varies between
  tasks of one harness.
- Guardrails counts a blank decision as rejected (`views.ts`, matching the registry formula); the impact depends
  on whether rows with a blank `detail` exist.
- `maybe("abc")` returns 0 instead of null (`format.ts`); no input the client receives was found to reach it.

## Proposed change

Fix each defect with a test that fails before the fix. For 5 and 6, return `total_rows` so the table shows
"Showing N of M". For 11, pick one day definition for every daily chart and state it in each chart's subtitle.
For 13 and 14, extend the manifest and match each title against its own page. For 15, vendor the scorer or remove
the scoring step. Check the three unverified items against ClickHouse data before deciding on them.

## Acceptance check

Each numbered defect has a regression test that fails on today's code and passes after the fix, and the gate is
green.
