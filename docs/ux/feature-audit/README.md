# Feature audit: Agent Introspection dashboard, 2026-10-01

The no-regression baseline for the UX/UI redesign. It lists every feature on every page, every signal the pages
show and how each signal is formed, from the producer to the number on screen. Each row has a stable ID and an
empty **New home** column.

**The rule.** A feature, signal or calculation can move to another page, another level (first screen, in place,
its own page) or into the data source. It can't disappear. A row may be removed only when the change is listed
under [Decided changes](#decided-changes) with the user's agreement.

## Files

| File                                 | Covers                                                                                        | Features | Signals | UI calculations |
| ------------------------------------ | --------------------------------------------------------------------------------------------- | -------: | ------: | --------------: |
| [shell.md](shell.md)                 | Navigation, filters, header, panels, charts, tables, API, security, formatters                |       56 |      13 |              31 |
| [pipeline.md](pipeline.md)           | V1 Pipeline                                                                                   |       30 |      21 |              14 |
| [cache.md](cache.md)                 | V2 Cache efficiency                                                                           |       26 |      17 |              13 |
| [effort.md](effort.md)               | V3 Reasoning effort                                                                           |       31 |      26 |              17 |
| [tools.md](tools.md)                 | V4 Tool failures                                                                              |       20 |      14 |              12 |
| [friction.md](friction.md)           | V5 Friction                                                                                   |       20 |      12 |              12 |
| [guardrails.md](guardrails.md)       | V6 Guardrails                                                                                 |       21 |      10 |              15 |
| [provider.md](provider.md)           | V7 Provider                                                                                   |       22 |      11 |              11 |
| [recurrence.md](recurrence.md)       | V8 Recurrence                                                                                 |       23 |       9 |              12 |
| [intent.md](intent.md)               | V9 Intent and corrections                                                                     |       29 |      11 |              14 |
| [interventions.md](interventions.md) | V10 Interventions                                                                             |       25 |      24 |              18 |
| [session.md](session.md)             | Session drill-down                                                                            |       14 |      14 |               9 |
| **Total**                            |                                                                                               |  **317** | **182** |         **178** |
| [lineage.md](lineage.md)             | 9 fact views, producer routes per harness, all 64 registry signals, snapshot and window rules |          |         |                 |

Each page file has the same sections:

- **Features:** every figure, chart and series, table and its columns, legend, link, control, empty, not-emitted
  and error state, and every subtitle or caveat that carries meaning, with where it sits on the page.
- **Signals and how they are formed:** for each measure shown, its registry signal ID, unit and denominator, and
  the chain from browser step to API dataset (`dashboard/server/views.ts` or `pipeline.ts`), the SQL (aggregation,
  filters, grouping, limits, time zone) and the fact view (`src/agent_introspection/facts_sql/003_views.sql`),
  with each harness's support (aligned, differs or not emitted) from `signal_support.toml`.
- **UI calculations:** every aggregation, ratio, pivot, ranking, threshold, window or state decided in browser
  code. Moving one into the data source is a relocation, not a loss.
- **Behaviours to preserve:** filter rules, sort and top-N rules, links and the parameters they carry, time
  zones, and anything visible only on hover, focus or expand.
- **Changes since the 2026-09-30 inventory:** every row of the earlier baseline mapped to its current row.
- **Audit notes:** inconsistencies, unverified possible defects and the features most at risk in a redesign.

## How to check a change for regressions

1. Before changing a page, read its audit file and the shell file.
2. For every row the change touches, fill **New home** with where it now lives, for example
   `Effort / Comparison band / outcome chart` or `server: views.ts effort.outcomes`.
3. Write `removed: <decision ID>` only for a removal listed under [Decided changes](#decided-changes).
4. A touched row with a blank New home, or a removal without a decision ID, is a regression. Fix it before
   merging.
5. Content that today lives only on hover or in a popover (definitions, formulas, per-harness routes, chip
   tooltips, coverage-cell counts) must still be reachable after the change, even if it moves.
6. Re-run the page against [the rubric](../rubrics/ux-page.yaml) after the change.

## Method

Static audit of the working tree at commit `24a0d29` (clean), by one auditor per page pair plus one for the shell
and one for the data lineage, each reading the view, the shared components, the server queries, the fact views and
the signal registry. Nothing was run against ClickHouse. Checks after assembly:

- Every `Panel`, `Kpi` and `Section` title in `dashboard/src/views/*.tsx` appears in its page's audit file.
- Every one of the 100 registry entries in `signal_support.toml` (64 signals, routes and strays) appears in a page
  file or in [lineage.md](lineage.md).
- No feature, signal or calculation ID repeats within a file.
- `titles.json` and its test guard every title from here on (see [Automated guard](#automated-guard)).

The earlier baseline, `docs/ux/reviews/2026-09-30-feature-inventory.md`, is replaced by this audit and remains in
git history. Every one of its rows is mapped in the **Changes since** tables.

## Result against the earlier baseline

**No feature has been lost since 2026-09-30.** Every earlier row still exists: as it was, renamed, moved to the
shell, split into finer rows, or changed in presentation. The changes already made in code are:

- The panel info note is a hover and focus popover instead of an expanding footer.
- The content column is left-aligned beside the sidebar and capped at 2560px.
- Contributor chips have a new state set (rows, route missing, no activity, no events, not applicable), computed by
  the server from route counts.
- Signals a machine doesn't emit are hidden machine-wide and listed on Pipeline ("Signals hidden on this
  machine"), instead of a per-panel not-emitted notice.
- The harness selector lists only the harnesses enabled on this machine.
- Pipeline coverage has 9 states (was 7), and its unexplained count covers 3 of them (was 2).
- Friction's interrupt and steer series use the non-harness palette.

## Decided changes

Changes the user has agreed to (2026-09-30 brief and design approval). None of them removes a feature.

| ID   | Decision                                                                                                                                         | Effect on features                   |
| ---- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------ |
| DC1  | Content left-aligned beside the sidebar, capped at 2560px                                                                                        | Layout only                          |
| DC2  | Type scale: body 14px, nothing below 12px, sentence-case labels and headers                                                                      | Presentation only; wording unchanged |
| DC3  | Daily charts 280–360px high                                                                                                                      | Presentation only                    |
| DC4  | Non-harness series use `cat-6`…`cat-8`; unknown and unset groups use `other`                                                                     | Colour only                          |
| DC5  | Panel info note as an anchored popover; native `title` tooltips become the same popover                                                          | Content must be kept in full         |
| DC6  | Flat panels on `surface` with a `rule` border                                                                                                    | Presentation only                    |
| DC7  | Labels, state words and terms stay as they are                                                                                                   | Constraint on every change           |
| DC8  | No verdict line, change-against-baseline figures or action hub                                                                                   | No new features of these kinds       |
| DC9  | Tools: Repeated attempts and Tool loops become one Repeated calls table (grouped by identical call; attempts, longest consecutive run, failures) | Both tables' fields kept, merged     |
| DC10 | Effort: the daily clean-completion and daily median-duration charts become whole-window interval charts by harness and effort                    | Same measures, over the whole window |

**No removals are approved.** Moving long tables to their own detail pages (Pipeline coverage, Recurrence lists,
Interventions findings, the Session tool-call log) is a proposed relocation that needs the user's approval
before it is built.

## At risk in a redesign

These are easy to drop silently because they aren't obvious on the first screen. Each page file lists its own.

- **Hover-only content:** the info popover (registry question, formula, per-harness route, alignment and row
  counts), contributor-chip tooltips, Pipeline coverage-cell counts (route, own, foreign, unexplained), bar-list
  tooltips and chart readouts.
- **Conditional panels:** panels and figures that swap or vanish depending on which signals a machine emits
  (Guardrails sandbox versus approval decisions, Provider disconnects versus retries, Provider throughput and
  conformance), with no visible state when they vanish.
- **Rules in subtitles:** thresholds and definitions written only in subtitle text (Recurrence actionable rule,
  80% localisation, Tools ≥2 and ≥3 repeat and loop rules, polling-tool exclusions, recovery definition, effort
  correlation caveat, Intent sample notes).
- **Silent limits:** top-N cut-offs with no "N of M" notice (Session 500 rows, Pipeline unrouted 40, Recurrence
  actionable 30, sandbox and latency 30, errors, churn and bypass 25, Effort tasks 25).
- **"—" is not zero:** missing values and unobserved components render "—" or "(inferred)", never 0.
- **Mixed windows:** some panels use the selected UTC window, others seven Europe/London days or a fixed parity
  window, and some Pipeline and Interventions datasets ignore the harness or time filter. Today only subtitles
  say so.
- **Registry-driven content:** info notes, hidden signals, Pipeline coverage and exclusions come from six registry
  tables (`signals`, `support`, `routes`, `strays`, `exclusions`, `harnesses`), not UI strings. A redesign must
  keep loading all six.
- **Non-ClickHouse data:** the hook inbox backlog (local files) and Interventions findings, proposals and approval
  history (SQLite workflow store).
- **Accessibility:** "View as table" twins for charts, the skip link, focus on the page heading after navigation,
  and keyboard focus on chart marks.

## Defects found and fixed (2026-10-01)

Every possible defect the audit found was fixed on 2026-10-01 without removing a feature, except one that turned
out not to be a defect. Each page file's **Fixed 2026-10-01** list has the details and the rows that changed. All
view and session queries were then run against the live ClickHouse.

| Page                   | Defect                                                                                 | Fix                                                                                                                       |
| ---------------------- | -------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Pipeline               | Coverage figure might count cells the grid hides                                       | Not a defect: the grid and the figure use the same harness-filtered cells (`dashboard/server/pipeline.ts` `coverageGrid`) |
| Pipeline               | Fact rows per day dropped hook rows with no resolved harness                           | They form their own "unresolved (Codex hook)" series in the neutral colour                                                |
| Pipeline               | Sanitization figure read "0 / 0" parity when no harness had usage rows                 | The detail says there is no harness with usage rows to compare; both checks stay on the figure                            |
| Reasoning effort       | Reasoning share over three populations                                                 | Figure and spend table share one calculation; the outcomes-table share keeps its task population and says so beside it    |
| Reasoning effort       | Model × effort reasoning per step used a different step population                     | Divides by the steps of tasks that report reasoning                                                                       |
| Reasoning effort       | Model × effort sorted effort alphabetically                                            | Sorted low, medium, high, xhigh, mixed, unset                                                                             |
| Tool failures          | Two KPIs over different task populations, unstated                                     | Each detail names its denominator                                                                                         |
| Tool failures          | Example session picked by `any()`                                                      | The most recent occurrence, with a stable tiebreak                                                                        |
| Friction               | Recovery windowed on call time                                                         | Windowed on the start of the task the calls belong to, like the other measures                                            |
| Guardrails             | Churn, bypass and sandbox tables cut with no notice; sandbox figure summed capped rows | "Showing N of M" on each; the sandbox figure sums the uncapped daily data                                                 |
| Provider               | Calls denominator included cancelled calls in the figure but not the daily chart       | Both use calls not cancelled, as the registry formula says; latency and errors tables show "Showing N of M"               |
| Recurrence             | Targets figure counted a file twice across harnesses                                   | Counts distinct files across the selected harnesses                                                                       |
| Recurrence             | Actionable figure capped at 30 by the query                                            | Uncapped count from its own query; the tables show "Showing N of M"                                                       |
| Recurrence             | Window-end label in UTC, query cut in London time                                      | The label uses the Europe/London date                                                                                     |
| Recurrence             | Evaluation-workspace exclusion on some datasets only                                   | Applied to every Recurrence dataset                                                                                       |
| Intent and corrections | Codex exec and evaluation-workspace exclusions on some datasets only                   | Applied consistently to the next-prompt datasets and `recent`                                                             |
| Intent and corrections | Labelled figure and task-type shares over different denominators                       | Shares divide by all labelled tasks; untyped labelled tasks have their own row                                            |
| Interventions          | Figures, findings and proposals ignored the time range with no note                    | Each says it shows current workflow state, not the selected range                                                         |
| Interventions          | Applied figure and comparison table counted different proposals                        | The detail shows the in-range count and a "not evaluated" count, so the parts add up                                      |
| Session                | 500-row caps also capped the header counts                                             | Header counts come from uncapped counts; capped tables show "Showing N of M"                                              |
| Session                | Empty text named a range that doesn't apply                                            | Each table's empty text refers to the session                                                                             |
| Shell                  | A preset became "Custom range" after five minutes and Refresh didn't move it           | Presets are live: `range=<days>` in the URL, recomputed on load and Refresh                                               |
| Registry               | `task.delegations` not shown anywhere                                                  | "Delegations / task" column in the Effort task outcomes table, with its info note                                         |

**Checked against live data.** Session timestamps are UTC as labelled (the fact columns are
`DateTime64(9, 'UTC')`). A finding's task types reach the page as an array and render as a comma list
(`dashboard/server.test.ts` covers the parse, `dashboard/src/charts.test.tsx` the rendering); the current store has
no finding with task types to show.

## Automated guard

[`titles.json`](titles.json) records every panel, figure and section title in the pages on 2026-10-01 (141), each
with its feature ID. `dashboard/src/feature-audit.test.ts` fails when one of them is no longer rendered anywhere in
`dashboard/src`. A title may move to another page or component. To remove one on purpose, set its `removed` to a
decision ID from [Decided changes](#decided-changes); the test also fails when that ID isn't in the table. The test
runs with `bun run --cwd dashboard test` and the repository quality gates.
