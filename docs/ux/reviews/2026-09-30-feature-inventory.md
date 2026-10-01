# Feature inventory: Agent Introspection dashboard baseline, 2026-09-30

Every feature, signal and calculation the app has today, page by page, with what each is for (from
[`discovery.md`](../discovery.md)). This is the **no-regression baseline** for later changes and for
[the UX review](2026-09-30-ux-review.md). Features can move between pages, levels (first screen, in place, own
page) or into the data source, but each row must still exist somewhere afterwards, or be removed on purpose
with a reason.

### How to use it

1. Fill in **New home** while changing the app: the page and component where the row now lives, for example
   `Cache efficiency / At a glance` or `server: views.ts cache.kpi`.
2. Write `removed: <reason>` only for deliberate, agreed removals. A blank New home after a change that touched
   the row is a regression.
3. Rows marked **UI (should move to the source of truth)** are calculations done in page code. Their New home
   is a query in `dashboard/server/`, and the page just shows the result.
4. Line references are for the working tree on 2026-09-30. IDs are stable; line numbers drift. The tree was being
   edited while this inventory was taken; the Intent and corrections view (V9) is included.

**Scope.** Every page plus the shared shell. Kinds are control, figure, chart, table, list, text, action, link,
download, form and state. Private values are left out; code constants and public thresholds are kept.

## Totals

| Page                   | Prefix | Features | Signals | Calculations | Done in UI, should move |
| ---------------------- | ------ | -------: | ------: | -----------: | ----------------------: |
| Shared shell           | SHELL  |       32 |       5 |           10 |                       9 |
| Pipeline               | PIPE   |       21 |       6 |           10 |                      10 |
| Cache efficiency       | CACHE  |       15 |       1 |            8 |                       8 |
| Reasoning effort       | EFF    |       18 |       3 |           14 |                      14 |
| Tool failures          | TOOL   |       13 |       6 |            9 |                       9 |
| Friction               | FRIC   |       13 |       7 |            9 |                       9 |
| Guardrails             | GUARD  |       11 |       4 |            9 |                       9 |
| Provider               | PROV   |       15 |       6 |            8 |                       7 |
| Recurrence             | RECUR  |       15 |       4 |            7 |                       7 |
| Intent and corrections | INTENT |       19 |       7 |            9 |                       9 |
| Interventions          | INTV   |       14 |       4 |           13 |                      12 |
| Session                | SESS   |        8 |       3 |            6 |                       4 |
| **Total**              |        |      194 |      56 |          112 |                     107 |

## Behaviour to fix, not to preserve

Carry the feature over, not the defect.

- Non-harness series (Cache token parts, Friction interrupt/steer, Interventions baseline, Intent bars) use
  harness colours, so one colour means two things on the same screen (CACHE, FRIC, INTV, INTENT chart rows;
  `dashboard/src/format.ts` `SERIES`).
- Tables clip rows at a fixed 340px height with no "N of M" notice, and `overflow-wrap: anywhere` breaks short
  words such as state values mid-word (SESS, INTV, RECUR table rows; `dashboard/src/styles.css:560-589`).
- Several charts show the harness contributor legend and a second series legend in the same colours (PIPE,
  CACHE chart rows).
- Pipeline: the Coverage figure reports unexplained cells that the grid's visible rows don't show, so the
  reader can't find them from the figure (PIPE figure and coverage grid rows; cause unverified).
- Reasoning effort: reasoning per step and reasoning per task use different denominators in two tables, which
  read as contradictory for the same harness (EFF calculation rows; cause unverified).
- Interventions: the recurrence subtitle names a fixed seven-day window while the range filter changes the
  window (INTV rows; `dashboard/src/views/Interventions.tsx`).

## Shared shell (`dashboard/src/App.tsx`, `components.tsx`, `charts.tsx`)

### Features: Shared shell

| ID        | Feature             | Kind    | What it shows or does (options, defaults, states)                                                                                                                 | Purpose                                       | Source                                     | New home |
| --------- | ------------------- | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------- | ------------------------------------------ | -------- |
| SHELL-F01 | Skip link           | control | 'Skip to content' link to #content; visible on focus                                                                                                              | Keyboard bypass                               | `App.tsx:363-365`                          |          |
| SHELL-F02 | Sidebar brand       | text    | Mark, 'Agent introspection'                                                                                                                                       | Identity                                      | `App.tsx:367-370`                          |          |
| SHELL-F03 | Sidebar navigation  | control | 10 views V1–V10, number + title, active state with aria-current, real hrefs carrying filters, modified clicks fall through                                        | Move between pages                            | `App.tsx:340-359,371`                      |          |
| SHELL-F04 | Sidebar note        | text    | 'One definition per signal for every harness. Harnesses are never ranked…'                                                                                        | States the cross-harness principle            | `App.tsx:372-375`                          |          |
| SHELL-F05 | Mobile navigation   | control | Same links repeated in a second nav for small screens                                                                                                             | Small-screen nav                              | `App.tsx:386-388`                          |          |
| SHELL-F06 | Topbar crumb        | text    | 'Views / `<title>`' (not a link)                                                                                                                                  | Location                                      | `App.tsx:379-381`                          |          |
| SHELL-F07 | Refresh button      | action  | Re-runs the current query                                                                                                                                         | Manual refresh                                | `App.tsx:382-384,264-297`                  |          |
| SHELL-F08 | Page header         | text    | Eyebrow view number (or 'Drill-down'), h1 focused on change, question line                                                                                        | Question-led pages                            | `App.tsx:390-405`                          |          |
| SHELL-F09 | Live query status   | state   | 'Loading…', 'Query failed', or 'Queried HH:MM:SS UTC · N ms'; aria-live polite                                                                                    | Freshness and cost                            | `App.tsx:406-416`                          |          |
| SHELL-F10 | Time-range select   | control | Presets Last 24 hours / 7 days (default) / 30 days / 90 days / Custom range; label 'Time range · UTC'                                                             | Set window                                    | `App.tsx:121-126,420-434`                  |          |
| SHELL-F11 | Custom range inputs | control | Two datetime-local inputs (UTC), shown when Custom range is chosen; each edit navigates                                                                           | Arbitrary window                              | `App.tsx:435-460`                          |          |
| SHELL-F12 | Harness selector    | control | All harnesses (default) plus each harness; label 'Harness'                                                                                                        | Scope to one producer                         | `components.tsx:314-338`                   |          |
| SHELL-F13 | Scope note          | text    | 'All = the union…' or 'One harness: signals not applicable to it say so, never zero'                                                                              | Explains the filter                           | `App.tsx:466-470`                          |          |
| SHELL-F14 | Query-failed notice | state   | Alert 'Query failed — outcome unknown' with the error message                                                                                                     | Failure state                                 | `App.tsx:473-478`                          |          |
| SHELL-F15 | Unknown-view notice | state   | Alert for unknown paths                                                                                                                                           | Failure state                                 | `App.tsx:479-484`                          |          |
| SHELL-F16 | Loading skeleton    | state   | Three shimmer blocks while the first load runs                                                                                                                    | Loading state                                 | `App.tsx:504-510`                          |          |
| SHELL-F17 | Section             | layout  | h2 plus 12-column grid                                                                                                                                            | Group panels under a question                 | `components.tsx:299-312`                   |          |
| SHELL-F18 | Panel               | layout  | Title, subtitle, info button, Contributors, body in error Boundary, expanding info footer; spans 3/4/5/6/7/8/12, default 6; gradient background                   | Shared frame                                  | `components.tsx:226-275`                   |          |
| SHELL-F19 | Kpi                 | figure  | Panel span 3 with a large value and detail line                                                                                                                   | Headline number                               | `components.tsx:278-297`                   |          |
| SHELL-F20 | InfoNote            | text    | Registry title, question, formula; per-harness table with alignment word, route description, note, row count in window                                            | Definitions and provenance, from the registry | `components.tsx:126-187`                   |          |
| SHELL-F21 | Contributors        | list    | Chips for each selected harness with dot in harness colour when rows exist, or the state word 'no rows' / 'not applicable'; native `title` tooltip with row count | Which harnesses fed the panel                 | `components.tsx:83-123`                    |          |
| SHELL-F22 | NotApplicableNotice | state   | Replaces panel body with 'Not applicable' and registry notes                                                                                                      | Never show zero for not applicable            | `components.tsx:208-218`                   |          |
| SHELL-F23 | Boundary            | state   | Error boundary per panel: '`<title>` could not be displayed.'                                                                                                     | Isolate render errors                         | `components.tsx:189-206`                   |          |
| SHELL-F24 | HarnessName         | figure  | Coloured dot plus harness label                                                                                                                                   | Harness identity in tables                    | `components.tsx:340-348`                   |          |
| SHELL-F25 | SessionLink         | link    | Link to /session with current filters; text is the first 8 characters of the id plus ellipsis by default                                                          | Drill down to a session                       | `components.tsx:350-367`                   |          |
| SHELL-F26 | DailyChart          | chart   | SVG stacked columns or lines, 170px high, one y-axis, continuous days so gaps show, nice axis max, hover/focus readout of every series                            | Daily trends                                  | `charts.tsx:97-304`                        |          |
| SHELL-F27 | Legend              | chart   | List of colour swatches with labels; hidden for a single series                                                                                                   | Series key                                    | `charts.tsx:39-51`                         |          |
| SHELL-F28 | BarList             | chart   | Horizontal bars with value at tip and native `title` readout, optional detail                                                                                     | Ranked or comparable values                   | `charts.tsx:315-350`                       |          |
| SHELL-F29 | DataTable           | table   | Sticky header, 9px uppercase headers, 340px max-height scroll, numeric right-aligned, null → '—', empty message                                                   | Row data                                      | `charts.tsx:370-412`; `styles.css:560-594` |          |
| SHELL-F30 | TableView           | control | 'View as table' disclosure around a DataTable of the chart data                                                                                                   | Exact values, accessibility                   | `charts.tsx:415-428`                       |          |
| SHELL-F31 | Content column      | layout  | Max width 1540px, centred                                                                                                                                         | Page width                                    | `styles.css:156-160`                       |          |
| SHELL-F32 | Focus               | state   | Heading receives focus on each view change; #content tabIndex -1                                                                                                  | Keyboard/screen-reader orientation            | `App.tsx:305-308,389`                      |          |

### Signals: Shared shell

| ID        | Signal            | Rule (thresholds → state, colour, word)                                                                            | Computed in                          | Source                     | New home |
| --------- | ----------------- | ------------------------------------------------------------------------------------------------------------------ | ------------------------------------ | -------------------------- | -------- |
| SHELL-S01 | Contributor state | rows / no rows / not applicable from registry support and contribution counts; dot in harness colour only for rows | components.tsx                       | `components.tsx:62-80`     |          |
| SHELL-S02 | Alignment word    | Registry alignment string with class alignment-`<word>` per harness route                                          | Registry, rendered in components.tsx | `components.tsx:158-165`   |          |
| SHELL-S03 | Not applicable    | When the measured signal is unsupported for every selected harness, body is replaced by the registry reason        | components.tsx                       | `components.tsx:45-58,265` |          |
| SHELL-S04 | Live status       | loading → Loading…; failure → Query failed; else Queried time and elapsed ms                                       | App.tsx                              | `App.tsx:406-416`          |          |
| SHELL-S05 | Range label       | Custom range unless end−start matches a preset within 0.001 day                                                    | App.tsx                              | `App.tsx:136-144`          |          |

### Calculations: Shared shell

| ID        | Output                    | Method (function and inputs, or the in-page formula written out)                        | Where                                                    | Owned by the data source?               | New home |
| --------- | ------------------------- | --------------------------------------------------------------------------------------- | -------------------------------------------------------- | --------------------------------------- | -------- |
| SHELL-C01 | Preset range              | start = now − N days, end = now, ISO to ms zero                                         | App.tsx lastDays, `App.tsx:128-135`                      | UI (should move to the source of truth) |          |
| SHELL-C02 | Preset matching           | days = (end − start) / 86 400 000 compared to preset days                               | App.tsx presetFor, `App.tsx:136-144`                     | UI (should move to the source of truth) |          |
| SHELL-C03 | Contribution counts       | rows per harness per signal from contributions                                          | components.tsx contributionState, `components.tsx:62-80` | UI (should move to the source of truth) |          |
| SHELL-C04 | Chart axis max and totals | stack sum or line max, rounded up to 1/2/5×10ⁿ                                          | charts.tsx niceMax, `charts.tsx:31-37,120-125`           | UI (should move to the source of truth) |          |
| SHELL-C05 | Gap filling               | Every UTC day between first and last row inserted as an empty row                       | charts.tsx continuousDays, `charts.tsx:53-68`            | UI (should move to the source of truth) |          |
| SHELL-C06 | Bar width                 | 100 × value / max, min 0.5%                                                             | charts.tsx BarList, `charts.tsx:338`                     | UI (should move to the source of truth) |          |
| SHELL-C07 | sumRows / groupSum        | Sum numeric fields across per-harness rows, grouped by a key                            | format.ts, `format.ts:86-110`                            | UI (should move to the source of truth) |          |
| SHELL-C08 | ratio                     | 100 × numerator / denominator, null without denominator                                 | format.ts, `format.ts:82-84`                             | UI (should move to the source of truth) |          |
| SHELL-C09 | pivotDaily                | Long daily rows pivoted to a column per series with a value function over summed fields | format.ts, `format.ts:115-141`                           | UI (should move to the source of truth) |          |
| SHELL-C10 | Number and time formats   | fmtCount, fmtPct, fmtSeconds, shortTime, Intl en formatting with 2 decimals             | format.ts, charts.tsx cell, `charts.tsx:359-368`         | Yes (formatting only)                   |          |

### Dependencies and side effects: Shared shell

- Network: `GET /api/registry` once; `GET /api/view?view&start&end&harness` or `GET /api/session?harness&session` per navigation, refresh and filter change; stale responses are discarded by a sequence counter and aborted (`App.tsx:250-297`).
- Storage: none; filters, view and session live in the URL (history pushState / popstate, `App.tsx:228-248`).
- Side effects: document.title and heading focus per view (`App.tsx:305-308`); ResizeObserver on each chart (`charts.tsx:16-29`).

## Pipeline (`dashboard/src/views/Pipeline.tsx`)

### Features: Pipeline

| ID       | Feature                              | Kind    | What it shows or does (options, defaults, states)                                                                         | Purpose                          | Source                      | New home |
| -------- | ------------------------------------ | ------- | ------------------------------------------------------------------------------------------------------------------------- | -------------------------------- | --------------------------- | -------- |
| PIPE-F01 | Page header and question             | text    | Title, question, V1 label, Queried timestamp and latency chip                                                             | Frame the page                   | `Shell/PageHeader (shared)` |          |
| PIPE-F02 | Time range filter                    | control | Select, default Last 7 days                                                                                               | Scope data window                | `components.tsx useView`    |          |
| PIPE-F03 | Harness filter                       | control | Select, default All harnesses; filters freshness, parity, coverage, daily, project, stray rows via inScope                | Scope by harness                 | `Pipeline.tsx:92-93`        |          |
| PIPE-F04 | Loaders and snapshots KPI            | figure  | `ok / total` loaders; detail 'on schedule, no errors' or N stale or failing                                               | Is the loader healthy            | `Pipeline.tsx:138-147`      |          |
| PIPE-F05 | Max fact lag KPI                     | figure  | Largest lag seconds, or 'no facts' when any source lacks fact rows                                                        | Is the fact loader keeping up    | `Pipeline.tsx:148-157`      |          |
| PIPE-F06 | Coverage KPI                         | figure  | N unexplained over signal × harness cells                                                                                 | Are signals routed               | `Pipeline.tsx:158-163`      |          |
| PIPE-F07 | Sanitization KPI                     | figure  | Violation count; detail parity harnesses matching raw                                                                     | No raw data stored, totals match | `Pipeline.tsx:164-169`      |          |
| PIPE-F08 | KPI info buttons and harness legends | control | Info expands footer with signal definitions; harness legend under each KPI                                                | Definitions and colour key       | `components.tsx Kpi`        |          |
| PIPE-F09 | Loader and snapshot refresh table    | table   | View, Status, Last success UTC, Age, Rows written, Error ('—' if none)                                                    | Per-loader freshness             | `Pipeline.tsx:172-197`      |          |
| PIPE-F10 | Freshness by harness table           | table   | Harness, Source, SigNoz latest, Fact lag ('no facts' when missing), Last activity ago; empty message                      | Per-source lag                   | `Pipeline.tsx:198-236`      |          |
| PIPE-F11 | Fact rows per day chart              | chart   | Stacked daily bars per harness, View as table toggle, UTC days, 170px                                                     | Volume and gaps over time        | `Pipeline.tsx:237-260`      |          |
| PIPE-F12 | Coverage state legend                | list    | Seven state chips with marks                                                                                              | Key for grid                     | `Pipeline.tsx:269-275`      |          |
| PIPE-F13 | Coverage grid                        | table   | Signal rows grouped by view × harness columns of StateChip; title tooltip with route, own rows, foreign rows, unexplained | Completeness of routing          | `Pipeline.tsx:35-86`        |          |
| PIPE-F14 | Source parity table                  | table   | Per harness raw vs fact operations and input, Match chip; window in subtitle                                              | Fact equals source               | `Pipeline.tsx:278-316`      |          |
| PIPE-F15 | All = Σ harnesses table              | table   | Fact, Measure, All, Σ harnesses, Match chip                                                                               | Recombination check              | `Pipeline.tsx:317-338`      |          |
| PIPE-F16 | Sanitization table                   | table   | Table, Rows, Dropped keys, Status > 160, Home paths                                                                       | Privacy check                    | `Pipeline.tsx:339-354`      |          |
| PIPE-F17 | Project attribution tables           | table   | Three tables: harness share with project, last sync/age/backlog/events, hook rejections                                   | Project attribution health       | `Pipeline.tsx:355-406`      |          |
| PIPE-F18 | Stray and unrouted rows tables       | table   | Registered strays with explanation; unrouted rows by harness/source/name/rows                                             | Explain unexplained coverage     | `Pipeline.tsx:407-447`      |          |
| PIPE-F19 | Excluded signals table               | table   | Signal, View (title), Missing for, Reason                                                                                 | Signals left off dashboard       | `Pipeline.tsx:448-468`      |          |
| PIPE-F20 | Panel info footers                   | control | Expanding footer per panel                                                                                                | Source and definitions           | `components.tsx Panel`      |          |
| PIPE-F21 | Loading/error state                  | state   | Page data from shared useView                                                                                             | Fetch states                     | `App`                       |          |

### Signals: Pipeline

| ID       | Signal                  | Rule (thresholds → state, colour, word)                                                                                                                   | Computed in                              | Source                    | New home |
| -------- | ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- | ------------------------- | -------- |
| PIPE-S01 | Loader state            | Stale flag (num(row.stale)===1) → counted failing; KPI word 'N stale or failing' vs 'on schedule, no errors'; no colour                                   | Source flag, count in UI                 | `Pipeline.tsx:95,140-145` |          |
| PIPE-S02 | Fact lag missing        | facts_missing===1 → 'no facts'                                                                                                                            | Source flag, UI selection                | `Pipeline.tsx:97,150,221` |          |
| PIPE-S03 | Coverage state          | healthy ✓ good; no events/idle ○ neutral; not applicable · neutral; stray (explained) ≈ warning; possible break ! serious; stray (unexplained) ✕ critical | State from source, mark/tone table in UI | `Pipeline.tsx:14-22`      |          |
| PIPE-S04 | Unexplained set         | states 'possible break' and 'stray (unexplained)' count as unexplained                                                                                    | UI                                       | `Pipeline.tsx:23,113`     |          |
| PIPE-S05 | Match state             | ok===1 → healthy else possible break                                                                                                                      | UI mapping of source flag                | `Pipeline.tsx:309,332`    |          |
| PIPE-S06 | Sanitization violations | sum of forbidden_keys+long_status+home_paths; >0 is a violation                                                                                           | UI                                       | `Pipeline.tsx:104-111`    |          |

### Calculations: Pipeline

| ID       | Output                      | Method (function and inputs, or the in-page formula written out) | Where                | Owned by the data source?               | New home |
| -------- | --------------------------- | ---------------------------------------------------------------- | -------------------- | --------------------------------------- | -------- |
| PIPE-C01 | Loaders ok count            | loaders.length − failing.length                                  | Pipeline.tsx:95,140  | UI (should move to the source of truth) |          |
| PIPE-C02 | Max fact lag                | max(lag_seconds) over in-scope freshness rows                    | Pipeline.tsx:98-100  | UI (should move to the source of truth) |          |
| PIPE-C03 | Sources missing facts       | count facts_missing===1                                          | Pipeline.tsx:97      | UI (should move to the source of truth) |          |
| PIPE-C04 | Violations total            | sum forbidden_keys + long_status + home_paths over sanitization  | Pipeline.tsx:104-111 | UI (should move to the source of truth) |          |
| PIPE-C05 | Parity ok count             | count of parity rows where ok===1                                | Pipeline.tsx:102     | UI (should move to the source of truth) |          |
| PIPE-C06 | Unexplained coverage cells  | count coverage rows with state in UNEXPLAINED                    | Pipeline.tsx:113-115 | UI (should move to the source of truth) |          |
| PIPE-C07 | Daily fact rows per harness | pivot daily_rows by day and harness, summing n                   | Pipeline.tsx:117-124 | UI (should move to the source of truth) |          |
| PIPE-C08 | Project share               | ratio(attributed, tasks) as percent                              | Pipeline.tsx:374     | UI (should move to the source of truth) |          |
| PIPE-C09 | Parity window label         | first parity row window_start/window_end sliced                  | Pipeline.tsx:132-134 | UI (should move to the source of truth) |          |
| PIPE-C10 | Coverage grid shape         | unique signals, views and harness columns from rows              | Pipeline.tsx:39-42   | UI (should move to the source of truth) |          |

### Dependencies and side effects: Pipeline

- Fetches `/api/view/pipeline` (datasets: loaders, freshness, parity, sanitization, coverage, recombination, daily_rows, project_coverage, project_sync, project_rejections, strays, unrouted, exclusions) through useView.
- Reads shared filters (time range, harness) and registry (signal view titles, Pipeline.tsx:90-91).
- Native `title` tooltips on coverage chips. No storage, no writes.

## Cache efficiency (`dashboard/src/views/Cache.tsx`)

### Features: Cache efficiency

| ID        | Feature                                     | Kind    | What it shows or does (options, defaults, states)                                           | Purpose                      | Source                       | New home |
| --------- | ------------------------------------------- | ------- | ------------------------------------------------------------------------------------------- | ---------------------------- | ---------------------------- | -------- |
| CACHE-F01 | Page header and question                    | text    | Title, question, V2, Queried chip                                                           | Frame                        | `Shell`                      |          |
| CACHE-F02 | Time range filter                           | control | Select default Last 7 days                                                                  | Scope                        | `useView`                    |          |
| CACHE-F03 | Harness filter                              | control | Select default All harnesses                                                                | Scope                        | `Cache.tsx:41-43`            |          |
| CACHE-F04 | Input tokens KPI                            | figure  | Total input; detail completed operations                                                    | Volume                       | `Cache.tsx:63-68`            |          |
| CACHE-F05 | Input cache utilization KPI                 | figure  | Cached ÷ input percent; detail cached of input                                              | Headline efficiency          | `Cache.tsx:69-74`            |          |
| CACHE-F06 | Uncached input KPI                          | figure  | Input − cached; detail percent of input                                                     | Uncached volume              | `Cache.tsx:75-80`            |          |
| CACHE-F07 | Output tokens KPI                           | figure  | Output total; detail usage-active sessions                                                  | Output volume                | `Cache.tsx:81-86`            |          |
| CACHE-F08 | Daily token consumption chart               | chart   | Stacked uncached/cached/output per day; View as table                                       | Where tokens go over time    | `Cache.tsx:89-116`           |          |
| CACHE-F09 | Daily input cache utilization chart         | chart   | Line per harness, 0-100%, View as table                                                     | Trend of efficiency          | `Cache.tsx:117-141`          |          |
| CACHE-F10 | By harness table                            | table   | Input, Cached, Utilization, Uncached, Output, Operations, Sessions                          | Compare harnesses            | `Cache.tsx:144-202`          |          |
| CACHE-F11 | By model table                              | table   | Harness, Provider, Model, Operations, Input, Utilization, Uncached, Output                  | Compare models               | `Cache.tsx:203-246`          |          |
| CACHE-F12 | Sessions with the most uncached input table | table   | Top 20: Harness, Session link, Models, Uncached, Utilization, Output, Operations, Last seen | Find sessions to investigate | `Cache.tsx:249-297`          |          |
| CACHE-F13 | Session link                                | link    | Opens Session page for the row                                                              | Drill to L3                  | `components.tsx SessionLink` |          |
| CACHE-F14 | KPI/panel info and harness legends          | control | Info footer and harness legend under every KPI and panel                                    | Definitions, colour key      | `components.tsx`             |          |
| CACHE-F15 | Loading/error state                         | state   | Shared fetch states                                                                         |                              | `App`                        |          |

### Signals: Cache efficiency

| ID        | Signal              | Rule (thresholds → state, colour, word)                     | Computed in   | Source         | New home |
| --------- | ------------------- | ----------------------------------------------------------- | ------------- | -------------- | -------- |
| CACHE-S01 | Utilization display | Percent formatted by fmtPct; no thresholds or colour states | UI formatting | `Cache.tsx:26` |          |

### Calculations: Cache efficiency

| ID        | Output                       | Method (function and inputs, or the in-page formula written out)  | Where                    | Owned by the data source?               | New home |
| --------- | ---------------------------- | ----------------------------------------------------------------- | ------------------------ | --------------------------------------- | -------- |
| CACHE-C01 | KPI totals                   | sumRows(kpi, input/cached/output/operations/sessions)             | Cache.tsx:31             | UI (should move to the source of truth) |          |
| CACHE-C02 | Input cache utilization      | ratio(cached, input)                                              | Cache.tsx:71             | UI (should move to the source of truth) |          |
| CACHE-C03 | Uncached input (KPI)         | input − cached                                                    | Cache.tsx:77             | UI (should move to the source of truth) |          |
| CACHE-C04 | Uncached share               | ratio(input − cached, input)                                      | Cache.tsx:78             | UI (should move to the source of truth) |          |
| CACHE-C05 | Row utilization              | utilization(row)=ratio(cached, input) in three tables             | Cache.tsx:26,178,229,280 | UI (should move to the source of truth) |          |
| CACHE-C06 | Row uncached                 | input − cached per row in three tables                            | Cache.tsx:184,235,274    | UI (should move to the source of truth) |          |
| CACHE-C07 | Daily partition              | group daily by day: uncached=input−cached, cached, output, sorted | Cache.tsx:33-40          | UI (should move to the source of truth) |          |
| CACHE-C08 | Daily utilization by harness | pivotDaily sums input/cached then ratio                           | Cache.tsx:44-49          | UI (should move to the source of truth) |          |

### Dependencies and side effects: Cache efficiency

- Fetches `/api/view/cache` (kpi, daily, models, sessions) via useView; reads shared time range and harness filters.
- SessionLink navigates to the Session page. No storage.

## Reasoning effort (`dashboard/src/views/Effort.tsx`)

### Features: Reasoning effort

| ID      | Feature                                    | Kind    | What it shows or does (options, defaults, states)                                                                      | Purpose                           | Source                       | New home |
| ------- | ------------------------------------------ | ------- | ---------------------------------------------------------------------------------------------------------------------- | --------------------------------- | ---------------------------- | -------- |
| EFF-F01 | Page header and question                   | text    | Title, question, V3, Queried chip                                                                                      | Frame                             | `Shell`                      |          |
| EFF-F02 | Time range filter                          | control | Select default Last 7 days                                                                                             | Scope                             | `useView`                    |          |
| EFF-F03 | Harness filter                             | control | Select default All harnesses                                                                                           | Scope                             | `useView`                    |          |
| EFF-F04 | Reasoning tokens KPI                       | figure  | Total reasoning; detail calls (Claude Code: turns)                                                                     | Spend                             | `Effort.tsx:71-76`           |          |
| EFF-F05 | Reasoning share of output KPI              | figure  | reasoning ÷ output percent                                                                                             | Reasoning weight                  | `Effort.tsx:77-82`           |          |
| EFF-F06 | Tasks KPI                                  | figure  | Task count; detail clean completion percent                                                                            | Outcome volume                    | `Effort.tsx:83-88`           |          |
| EFF-F07 | Heavy-effort task share KPI                | figure  | heavy ÷ tasks percent (high or xhigh)                                                                                  | Effort mix                        | `Effort.tsx:89-94`           |          |
| EFF-F08 | Daily reasoning tokens by effort chart     | chart   | Stacked by effort; View as table                                                                                       | Spend over time                   | `Effort.tsx:97-119`          |          |
| EFF-F09 | Daily task mix by effort chart             | chart   | Stacked tasks started per day                                                                                          | Mix over time                     | `Effort.tsx:120-142`         |          |
| EFF-F10 | Daily clean completion by effort chart     | chart   | Line per effort 0-100%, noisy caveat subtitle                                                                          | Outcome trend                     | `Effort.tsx:143-166`         |          |
| EFF-F11 | Daily median task duration by effort chart | chart   | Line per effort, seconds; no subtitle                                                                                  | Duration trend                    | `Effort.tsx:167-190`         |          |
| EFF-F12 | Task outcomes by reasoning effort table    | table   | 11 columns: tasks, clean, interrupted, follow-up, errored, P50/P90, steps, reasoning/task, share; correlational caveat | Core effort-vs-outcome comparison | `Effort.tsx:193-283`         |          |
| EFF-F13 | Reasoning spend by effort table            | table   | Calls, Reasoning, Of all reasoning, Per call, Share of output                                                          | Who spends reasoning              | `Effort.tsx:284-334`         |          |
| EFF-F14 | Model × effort table                       | table   | Holds model fixed (≥3 tasks): tasks, clean, P50, steps, reasoning/step                                                 | Controlled comparison             | `Effort.tsx:335-389`         |          |
| EFF-F15 | Top 25 tasks by reasoning tokens table     | table   | Harness, Session link, Model, Effort, Started, Duration, Steps, Reasoning, Output, Clean yes/no/—                      | Find heaviest tasks               | `Effort.tsx:392-454`         |          |
| EFF-F16 | Session link                               | link    | Opens Session page                                                                                                     | Drill to L3                       | `components.tsx SessionLink` |          |
| EFF-F17 | KPI/panel info and legends                 | control | Info footer, harness legend and effort legend                                                                          | Definitions                       | `components.tsx`             |          |
| EFF-F18 | Loading/error state                        | state   | Shared fetch states                                                                                                    |                                   | `App`                        |          |

### Signals: Reasoning effort

| ID      | Signal        | Rule (thresholds → state, colour, word)                                                         | Computed in | Source               | New home |
| ------- | ------------- | ----------------------------------------------------------------------------------------------- | ----------- | -------------------- | -------- |
| EFF-S01 | Effort colour | EFFORT_COLOR per level (low..xhigh sequential blue, mixed purple, unset grey), fallback #6b7280 | UI          | `Effort.tsx:21-28`   |          |
| EFF-S02 | Clean cell    | clean_completion null → '—'; 1 → yes; else no                                                   | UI mapping  | `Effort.tsx:444-449` |          |
| EFF-S03 | Heavy effort  | high or xhigh requested counts as heavy (computed in source, ratio in UI)                       | Source flag | `Effort.tsx:91-93`   |          |

### Calculations: Reasoning effort

| ID      | Output                                            | Method (function and inputs, or the in-page formula written out)                | Where                    | Owned by the data source?               | New home |
| ------- | ------------------------------------------------- | ------------------------------------------------------------------------------- | ------------------------ | --------------------------------------- | -------- |
| EFF-C01 | Spend and task sums                               | sumRows(usage: reasoning/output/calls; outcomes: tasks/heavy/clean_n/clean_den) | Effort.tsx:36-37         | UI (should move to the source of truth) |          |
| EFF-C02 | Reasoning share KPI                               | ratio(reasoning, output)                                                        | Effort.tsx:79            | UI (should move to the source of truth) |          |
| EFF-C03 | Clean completion KPI                              | ratio(clean_n, clean_den)                                                       | Effort.tsx:86            | UI (should move to the source of truth) |          |
| EFF-C04 | Heavy-effort share                                | ratio(heavy, tasks)                                                             | Effort.tsx:91            | UI (should move to the source of truth) |          |
| EFF-C05 | Daily pivots                                      | pivotDaily by effort for reasoning, tasks, clean ratio                          | Effort.tsx:38-56         | UI (should move to the source of truth) |          |
| EFF-C06 | Daily p50 by effort                               | reduce daily_tasks into per-day object of p50_duration                          | Effort.tsx:57-64         | UI (should move to the source of truth) |          |
| EFF-C07 | Row percent (clean/interrupted/follow-up/errored) | ratio(n, den) per row                                                           | Effort.tsx:30-31,221-239 | UI (should move to the source of truth) |          |
| EFF-C08 | Steps per task                                    | steps ÷ max(1, tasks)                                                           | Effort.tsx:258           | UI (should move to the source of truth) |          |
| EFF-C09 | Reasoning per task                                | reasoning ÷ reasoning_den                                                       | Effort.tsx:266           | UI (should move to the source of truth) |          |
| EFF-C10 | Row reasoning share                               | ratio(reasoning, reasoning_output)                                              | Effort.tsx:276           | UI (should move to the source of truth) |          |
| EFF-C11 | Of all reasoning                                  | ratio(row reasoning, total reasoning)                                           | Effort.tsx:315           | UI (should move to the source of truth) |          |
| EFF-C12 | Reasoning per call                                | reasoning ÷ max(1, calls)                                                       | Effort.tsx:322           | UI (should move to the source of truth) |          |
| EFF-C13 | Reasoning per step                                | reasoning ÷ max(1, avg_steps × tasks)                                           | Effort.tsx:381-382       | UI (should move to the source of truth) |          |
| EFF-C14 | Sort order                                        | sort by harness then effortRank                                                 | Effort.tsx:17-19,281,332 | UI (should move to the source of truth) |          |

### Dependencies and side effects: Reasoning effort

- Fetches `/api/view/effort` (usage, outcomes, daily_reasoning, daily_tasks, model_effort, heaviest) via useView; reads shared filters.
- SessionLink navigates to Session page. No storage.

## Tool failures (`dashboard/src/views/Tools.tsx`)

### Features: Tool failures

| ID       | Feature                 | Kind    | What it shows or does (options, defaults, states)                | Purpose             | Source                                      | New home |
| -------- | ----------------------- | ------- | ---------------------------------------------------------------- | ------------------- | ------------------------------------------- | -------- |
| TOOL-F01 | Tool calls KPI          | figure  | Total calls; detail = calls without explicit outcome             | Volume              | `dashboard/src/views/Tools.tsx:61-66`       |          |
| TOOL-F02 | Tool failure rate KPI   | figure  | failed / calls with outcome, with n                              | Headline failure    | `dashboard/src/views/Tools.tsx:67-72`       |          |
| TOOL-F03 | Tasks affected KPI      | figure  | failed tasks / tool-using tasks                                  | Breadth of failures | `dashboard/src/views/Tools.tsx:73-78`       |          |
| TOOL-F04 | Repeated attempts KPI   | figure  | repeat tasks / tasks, identical calls                            | Retry waste         | `dashboard/src/views/Tools.tsx:79-84`       |          |
| TOOL-F05 | Daily tool failure rate | chart   | Line per harness, % format, harness filter respected             | Trend by harness    | `dashboard/src/views/Tools.tsx:87-98`       |          |
| TOOL-F06 | Failure rate as table   | control | View-as-table toggle: Day + % per harness                        | Accessible data     | `dashboard/src/views/Tools.tsx:99-109`      |          |
| TOOL-F07 | Daily tool calls        | chart   | Stacked columns by harness                                       | Volume trend        | `dashboard/src/views/Tools.tsx:111-122`     |          |
| TOOL-F08 | Calls as table          | control | View-as-table: Day + calls per harness                           | Accessible data     | `dashboard/src/views/Tools.tsx:123-133`     |          |
| TOOL-F09 | Failures by tool        | table   | Harness, tool, calls, failed, rate, failed tasks; server-ordered | Find failing tools  | `dashboard/src/views/Tools.tsx:137-163`     |          |
| TOOL-F10 | Failure signatures      | table   | Normalized error line, count, tasks, example session link        | Group root causes   | `dashboard/src/views/Tools.tsx:164-193`     |          |
| TOOL-F11 | Repeated attempts       | table   | Session, tool, command, attempts, failed                         | Find retry sessions | `dashboard/src/views/Tools.tsx:196-226`     |          |
| TOOL-F12 | Tool loops              | table   | Run length, failed, started UTC; empty state text                | Find loops          | `dashboard/src/views/Tools.tsx:227-263`     |          |
| TOOL-F13 | Session link            | link    | Example/session column links to session page                     | Drill to session    | `dashboard/src/views/Tools.tsx:184,212,243` |          |

### Signals: Tool failures

| ID       | Signal                  | Rule (thresholds → state, colour, word)          | Computed in | Source                                    | New home |
| -------- | ----------------------- | ------------------------------------------------ | ----------- | ----------------------------------------- | -------- |
| TOOL-S01 | tools.calls             | Defined in signal registry; page only references | Registry    | `dashboard/src/views/Tools.tsx:65,114`    |          |
| TOOL-S02 | tools.failure_rate      | Registry                                         | Registry    | `dashboard/src/views/Tools.tsx:71,90,140` |          |
| TOOL-S03 | tools.tasks_affected    | Registry                                         | Registry    | `dashboard/src/views/Tools.tsx:77,140`    |          |
| TOOL-S04 | tools.repeats           | Registry                                         | Registry    | `dashboard/src/views/Tools.tsx:83,199`    |          |
| TOOL-S05 | tools.failure_signature | Registry                                         | Registry    | `dashboard/src/views/Tools.tsx:167`       |          |
| TOOL-S06 | tools.loops             | Registry                                         | Registry    | `dashboard/src/views/Tools.tsx:230`       |          |

### Calculations: Tool failures

| ID       | Output             | Method (function and inputs, or the in-page formula written out)                  | Where                                   | Owned by the data source?               | New home |
| -------- | ------------------ | --------------------------------------------------------------------------------- | --------------------------------------- | --------------------------------------- | -------- |
| TOOL-C01 | KPI sums           | sumRows(kpi, calls, explicit, failed, unknown, tasks, failed_tasks, unattributed) | `dashboard/src/views/Tools.tsx:24-32`   | UI (should move to the source of truth) |          |
| TOOL-C02 | Repeat sums        | sumRows(repeat_summary,...)                                                       | `dashboard/src/views/Tools.tsx:33-37`   | UI (should move to the source of truth) |          |
| TOOL-C03 | Failure rate KPI   | failed/explicit via ratio                                                         | `dashboard/src/views/Tools.tsx:69`      | UI (should move to the source of truth) |          |
| TOOL-C04 | Tasks affected KPI | failed_tasks/tasks                                                                | `dashboard/src/views/Tools.tsx:75`      | UI (should move to the source of truth) |          |
| TOOL-C05 | Repeats KPI        | repeat_tasks/tasks                                                                | `dashboard/src/views/Tools.tsx:81`      | UI (should move to the source of truth) |          |
| TOOL-C06 | Daily failure rate | pivotDaily per harness failed/explicit                                            | `dashboard/src/views/Tools.tsx:46-51`   | UI (should move to the source of truth) |          |
| TOOL-C07 | Daily calls        | pivotDaily per harness calls                                                      | `dashboard/src/views/Tools.tsx:52-57`   | UI (should move to the source of truth) |          |
| TOOL-C08 | Row rate           | ratio(failed, explicit) per row                                                   | `dashboard/src/views/Tools.tsx:156-158` | UI (should move to the source of truth) |          |
| TOOL-C09 | Loop start         | String(started).slice(0,16)                                                       | `dashboard/src/views/Tools.tsx:257`     | UI (should move to the source of truth) |          |

### Dependencies and side effects: Tool failures

- Fetches `/api/view/tools` (datasets kpi, repeat_summary, daily, by_tool, signatures, repeats, loops); reads shared filters (time range, harness) via useView; SessionLink navigates to session page. No storage.

## Friction (`dashboard/src/views/Friction.tsx`)

### Features: Friction

| ID       | Feature                        | Kind    | What it shows or does (options, defaults, states)                   | Purpose                    | Source                                     | New home |
| -------- | ------------------------------ | ------- | ------------------------------------------------------------------- | -------------------------- | ------------------------------------------ | -------- |
| FRIC-F01 | Clean completion KPI           | figure  | clean_n/clean_den with n of tasks that observe it                   | Headline                   | `dashboard/src/views/Friction.tsx:70-75`   |          |
| FRIC-F02 | Interrupted tasks KPI          | figure  | interrupted rate                                                    | Interruption               | `dashboard/src/views/Friction.tsx:76-81`   |          |
| FRIC-F03 | Quick follow-up KPI            | figure  | follow_up rate                                                      | Re-work                    | `dashboard/src/views/Friction.tsx:82-87`   |          |
| FRIC-F04 | Recovery after failure KPI     | figure  | recovered/failed ops, marked inferred                               | Recovery                   | `dashboard/src/views/Friction.tsx:88-93`   |          |
| FRIC-F05 | Daily clean completion         | chart   | Line per harness, max 100                                           | Trend                      | `dashboard/src/views/Friction.tsx:96-108`  |          |
| FRIC-F06 | Clean completion as table      | control | View-as-table toggle                                                | Accessible data            | `dashboard/src/views/Friction.tsx:109-119` |          |
| FRIC-F07 | Daily interrupted and steered  | chart   | Stacked columns, literal colours                                    | Trend of explicit friction | `dashboard/src/views/Friction.tsx:121-135` |          |
| FRIC-F08 | Interrupt/steer as table       | control | View-as-table toggle                                                | Accessible data            | `dashboard/src/views/Friction.tsx:136-143` |          |
| FRIC-F09 | Friction components by harness | table   | Per harness tasks, observes, 5 component rates; — when not observed | Compare components         | `dashboard/src/views/Friction.tsx:147-191` |          |
| FRIC-F10 | Recovery by harness            | table   | Failed ops, recovered, rate, P50 to recover                         | Recovery comparison        | `dashboard/src/views/Friction.tsx:193-222` |          |
| FRIC-F11 | Operations that fail most      | table   | Failed op groups and recovered                                      | Find failing ops           | `dashboard/src/views/Friction.tsx:224-241` |          |
| FRIC-F12 | Latest friction tasks          | table   | Session, model, started, duration, four yes/no/— flags              | Exemplars                  | `dashboard/src/views/Friction.tsx:245-298` |          |
| FRIC-F13 | Session link                   | link    | Session column                                                      | Drill to session           | `dashboard/src/views/Friction.tsx:260`     |          |

### Signals: Friction

| ID       | Signal                    | Rule (thresholds → state, colour, word)         | Computed in | Source                                     | New home |
| -------- | ------------------------- | ----------------------------------------------- | ----------- | ------------------------------------------ | -------- |
| FRIC-S01 | friction.clean_completion | Registry; page shows —/no rows chips when den=0 | Registry+UI | `dashboard/src/views/Friction.tsx:72,99`   |          |
| FRIC-S02 | friction.interrupt        | Registry                                        | Registry    | `dashboard/src/views/Friction.tsx:80`      |          |
| FRIC-S03 | friction.quick_follow_up  | Registry                                        | Registry    | `dashboard/src/views/Friction.tsx:86`      |          |
| FRIC-S04 | friction.recovery         | Registry                                        | Registry    | `dashboard/src/views/Friction.tsx:92`      |          |
| FRIC-S05 | friction.steer            | Registry                                        | Registry    | `dashboard/src/views/Friction.tsx:124`     |          |
| FRIC-S06 | friction.error            | Registry                                        | Registry    | `dashboard/src/views/Friction.tsx:154`     |          |
| FRIC-S07 | Not observed              | den == 0 → — (never zero)                       | UI          | `dashboard/src/views/Friction.tsx:183-187` |          |

### Calculations: Friction

| ID       | Output                | Method (function and inputs, or the in-page formula written out) | Where                                        | Owned by the data source?               | New home |
| -------- | --------------------- | ---------------------------------------------------------------- | -------------------------------------------- | --------------------------------------- | -------- |
| FRIC-C01 | KPI sums              | sumRows(kpi, tasks + n/den for 5 components)                     | `dashboard/src/views/Friction.tsx:37`        | UI (should move to the source of truth) |          |
| FRIC-C02 | Rate and detail       | ratio(n,den); text 'n of den tasks'                              | `dashboard/src/views/Friction.tsx:38-41`     | UI (should move to the source of truth) |          |
| FRIC-C03 | Recovery sums         | sumRows(recovery_summary, failed_ops, recovered)                 | `dashboard/src/views/Friction.tsx:63-66`     | UI (should move to the source of truth) |          |
| FRIC-C04 | Recovery KPI          | recovered/failed_ops                                             | `dashboard/src/views/Friction.tsx:90`        | UI (should move to the source of truth) |          |
| FRIC-C05 | Daily clean           | pivotDaily clean_n/clean_den                                     | `dashboard/src/views/Friction.tsx:50-55`     | UI (should move to the source of truth) |          |
| FRIC-C06 | Daily interrupt/steer | groupSum(daily, day, interrupted_n, steered_n) sorted            | `dashboard/src/views/Friction.tsx:56-62`     | UI (should move to the source of truth) |          |
| FRIC-C07 | Component cells       | den>0 ? ratio(n,den) : —                                         | `dashboard/src/views/Friction.tsx:182-187`   | UI (should move to the source of truth) |          |
| FRIC-C08 | Recovery rate col     | ratio(recovered, failed_ops)                                     | `dashboard/src/views/Friction.tsx:211-212`   | UI (should move to the source of truth) |          |
| FRIC-C09 | Yes/no flag           | flag(): null→—, 1→yes, else no; slice started to 16              | `dashboard/src/views/Friction.tsx:31-32,268` | UI (should move to the source of truth) |          |

### Dependencies and side effects: Friction

- Fetches `/api/view/friction` (kpi, daily, recovery_summary, recovery_ops, tasks); shared filters via useView; SessionLink navigates. No storage.

## Guardrails (`dashboard/src/views/Guardrails.tsx`)

### Features: Guardrails

| ID        | Feature                 | Kind    | What it shows or does (options, defaults, states)       | Purpose          | Source                                       | New home |
| --------- | ----------------------- | ------- | ------------------------------------------------------- | ---------------- | -------------------------------------------- | -------- |
| GUARD-F01 | Rejected tool calls KPI | figure  | rejected/decided approvals                              | Headline         | `dashboard/src/views/Guardrails.tsx:69-74`   |          |
| GUARD-F02 | Approval decisions KPI  | figure  | count, decided by user                                  | Volume, who      | `dashboard/src/views/Guardrails.tsx:75-80`   |          |
| GUARD-F03 | Quality-gate bypass KPI | figure  | bypass commands, tasks, shell commands                  | Gate integrity   | `dashboard/src/views/Guardrails.tsx:81-86`   |          |
| GUARD-F04 | Command churn KPI       | figure  | tasks with 3+ commands on one file, files               | Churn            | `dashboard/src/views/Guardrails.tsx:87-92`   |          |
| GUARD-F05 | Daily rejection rate    | chart   | Line per harness, 12-span                               | Trend            | `dashboard/src/views/Guardrails.tsx:95-106`  |          |
| GUARD-F06 | Rejection rate as table | control | View-as-table toggle                                    | Accessible data  | `dashboard/src/views/Guardrails.tsx:107-117` |          |
| GUARD-F07 | Decisions by source     | chart   | BarList by harness · source; non-harness grey           | Who decides      | `dashboard/src/views/Guardrails.tsx:127-137` |          |
| GUARD-F08 | Decisions table         | table   | Harness, decision, source, decisions                    | Exact counts     | `dashboard/src/views/Guardrails.tsx:138-150` |          |
| GUARD-F09 | Bypass commands         | table   | At UTC, session, command head+sub, outcome; empty state | Exemplars        | `dashboard/src/views/Guardrails.tsx:155-188` |          |
| GUARD-F10 | Command churn           | table   | Session, file, commands, calls, failed; empty state     | Exemplars        | `dashboard/src/views/Guardrails.tsx:191-216` |          |
| GUARD-F11 | Session link            | link    | Session column                                          | Drill to session | `dashboard/src/views/Guardrails.tsx:175,206` |          |

### Signals: Guardrails

| ID        | Signal              | Rule (thresholds → state, colour, word) | Computed in | Source                                      | New home |
| --------- | ------------------- | --------------------------------------- | ----------- | ------------------------------------------- | -------- |
| GUARD-S01 | guard.rejections    | Registry                                | Registry    | `dashboard/src/views/Guardrails.tsx:73,98`  |          |
| GUARD-S02 | guard.decisions     | Registry                                | Registry    | `dashboard/src/views/Guardrails.tsx:73,124` |          |
| GUARD-S03 | guard.gate_bypass   | Registry                                | Registry    | `dashboard/src/views/Guardrails.tsx:85,157` |          |
| GUARD-S04 | guard.command_churn | Registry                                | Registry    | `dashboard/src/views/Guardrails.tsx:91,193` |          |

### Calculations: Guardrails

| ID        | Output            | Method (function and inputs, or the in-page formula written out)    | Where                                        | Owned by the data source?               | New home |
| --------- | ----------------- | ------------------------------------------------------------------- | -------------------------------------------- | --------------------------------------- | -------- |
| GUARD-C01 | Decided total     | sumRows(decisions, n)                                               | `dashboard/src/views/Guardrails.tsx:27`      | UI (should move to the source of truth) |          |
| GUARD-C02 | Rejected          | sum n where rejected===1                                            | `dashboard/src/views/Guardrails.tsx:28-31`   | UI (should move to the source of truth) |          |
| GUARD-C03 | By user           | sum n where source starts with 'user' (case-insensitive)            | `dashboard/src/views/Guardrails.tsx:32-37`   | UI (should move to the source of truth) |          |
| GUARD-C04 | Bypass/churn sums | sumRows(bypass; churn_summary)                                      | `dashboard/src/views/Guardrails.tsx:38-39`   | UI (should move to the source of truth) |          |
| GUARD-C05 | Rejection KPI     | rejected/decided                                                    | `dashboard/src/views/Guardrails.tsx:71`      | UI (should move to the source of truth) |          |
| GUARD-C06 | Daily rejection   | pivotDaily rejected/decisions                                       | `dashboard/src/views/Guardrails.tsx:47-52`   | UI (should move to the source of truth) |          |
| GUARD-C07 | Source buckets    | group decisions by harness·source, sum, sort desc, unknown fallback | `dashboard/src/views/Guardrails.tsx:53-65`   | UI (should move to the source of truth) |          |
| GUARD-C08 | Bar colour        | isHarness ? harness colour : #6b7280                                | `dashboard/src/views/Guardrails.tsx:132-134` | UI (should move to the source of truth) |          |
| GUARD-C09 | Command text      | command_head + command_sub trimmed; at sliced                       | `dashboard/src/views/Guardrails.tsx:164,182` | UI (should move to the source of truth) |          |

### Dependencies and side effects: Guardrails

- Fetches `/api/view/guardrails` (decisions, decisions_daily, bypass, churn_summary, bypass_calls, churn); shared filters via useView; SessionLink navigates. No storage.

## Provider (`dashboard/src/views/Provider.tsx`)

### Features: Provider

| ID       | Feature                     | Kind    | What it shows or does (options, defaults, states)                                                       | Purpose                               | Source                                             | New home |
| -------- | --------------------------- | ------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------- | -------------------------------------------------- | -------- |
| PROV-F01 | Time range select           | control | UTC range, default Last 7 days; shared filter bar                                                       | Scope all figures                     | shared filter bar                                  |          |
| PROV-F02 | Harness select              | control | All harnesses by default; narrows series                                                                | Scope to one harness                  | `Provider.tsx:73-79`                               |          |
| PROV-F03 | Model calls KPI             | figure  | Count of calls; detail shows failed and user-cancelled counts; per-harness coverage chips               | Volume and denominator                | `Provider.tsx:115-120`                             |          |
| PROV-F04 | Call error rate KPI         | figure  | failed / (calls − cancelled), 2 decimals                                                                | Headline failure rate                 | `Provider.tsx:121-126`                             |          |
| PROV-F05 | Retries KPI                 | figure  | Extra attempts beyond first, summed over harness routes                                                 | Retry pressure                        | `Provider.tsx:127-132`                             |          |
| PROV-F06 | Unknown outcomes KPI        | figure  | Calls without success flag, error or finish reason                                                      | Outcome data quality                  | `Provider.tsx:133-138`                             |          |
| PROV-F07 | Daily call error rate chart | chart   | Line per harness, % axis, 1 decimal                                                                     | Failure trend                         | `Provider.tsx:141-163`                             |          |
| PROV-F08 | Error rate table view       | table   | Toggle "View as table", day × harness %                                                                 | Exact values                          | `Provider.tsx:152-162`                             |          |
| PROV-F09 | Daily median TTFT chart     | chart   | Line per harness, seconds                                                                               | Responsiveness trend                  | `Provider.tsx:164-188`                             |          |
| PROV-F10 | TTFT table view             | table   | Toggle, day × harness seconds                                                                           | Exact values                          | `Provider.tsx:177-187`                             |          |
| PROV-F11 | Latency and TTFT by model   | table   | Harness, model, calls, P50/P95 latency, P50/P95 TTFT; sorted by calls; Codex uses sampling-step latency | Find slow models                      | `Provider.tsx:190-238`                             |          |
| PROV-F12 | Call errors by class        | table   | Harness, model, class, calls, last seen UTC; empty text when none                                       | Which errors happen                   | `Provider.tsx:241-261`                             |          |
| PROV-F13 | Retries by harness          | table   | Harness, extra attempts, measured-as text; empty text when none                                         | Retry basis per harness               | `Provider.tsx:262-285`                             |          |
| PROV-F14 | Panel info button           | control | i button on each KPI and panel opens signal definitions and footer                                      | Definitions                           | `Provider.tsx:119,125,131,137,144,167,194,241,265` |          |
| PROV-F15 | Harness coverage legend     | state   | Chips per harness with "no rows"/"not applicable" states                                                | Show which producers reach the signal | shared `Kpi`/`Panel`                               |          |

### Signals: Provider

| ID       | Signal                    | Rule (thresholds → state, colour, word)          | Computed in          | Source                     | New home |
| -------- | ------------------------- | ------------------------------------------------ | -------------------- | -------------------------- | -------- |
| PROV-S01 | provider.calls            | Registry signal; no state thresholds on the page | data source registry | `Provider.tsx:119`         |          |
| PROV-S02 | provider.error_rate       | Registry signal; no state thresholds on the page | data source registry | `Provider.tsx:125,144,241` |          |
| PROV-S03 | provider.retries          | Registry signal; no state thresholds on the page | data source registry | `Provider.tsx:131,265`     |          |
| PROV-S04 | provider.unknown_outcomes | Registry signal; no state thresholds on the page | data source registry | `Provider.tsx:137`         |          |
| PROV-S05 | provider.ttft             | Registry signal                                  | data source registry | `Provider.tsx:167,194`     |          |
| PROV-S06 | provider.latency          | Registry signal                                  | data source registry | `Provider.tsx:194`         |          |

### Calculations: Provider

| ID       | Output                                      | Method (function and inputs, or the in-page formula written out)                  | Where                 | Owned by the data source?               | New home |
| -------- | ------------------------------------------- | --------------------------------------------------------------------------------- | --------------------- | --------------------------------------- | -------- |
| PROV-C01 | KPI totals                                  | sumRows(kpi, calls, failed, cancelled, unknown, retried, attempt_den)             | `Provider.tsx:21-28`  | UI (should move to the source of truth) |          |
| PROV-C02 | Call error rate                             | failed / (calls − cancelled)                                                      | `Provider.tsx:123`    | UI (should move to the source of truth) |          |
| PROV-C03 | Codex extra attempts                        | max(0, attempts − steps) per harness from sampling                                | `Provider.tsx:30-37`  | UI (should move to the source of truth) |          |
| PROV-C04 | Retry rows and total                        | Codex rows + omp retries + Claude retried; extraAttempts = sum of extra           | `Provider.tsx:44-72`  | UI (should move to the source of truth) |          |
| PROV-C05 | Daily error rate                            | pivotDaily: failed / calls per harness per day                                    | `Provider.tsx:81-86`  | UI (should move to the source of truth) |          |
| PROV-C06 | Daily TTFT pivot                            | day → harness ttft_p50                                                            | `Provider.tsx:87-94`  | UI (should move to the source of truth) |          |
| PROV-C07 | Latency table merge                         | Codex rows take p50/p95 from sampling step by harness+model; sorted by calls desc | `Provider.tsx:95-111` | UI (should move to the source of truth) |          |
| PROV-C08 | Latency and TTFT percentiles, error classes | Server-side query results, formatted only                                         | `/api/view/provider`  | Yes                                     |          |

### Dependencies and side effects: Provider

- Reads `/api/view/provider` (datasets kpi, codex_sampling, claude_retries, omp_retries, daily, latency, errors); refetches on time-range/harness filter change via `useView`.
- No storage writes; no cross-page state beyond the shared filters.

## Recurrence (`dashboard/src/views/Recurrence.tsx`)

### Features: Recurrence

| ID        | Feature                          | Kind    | What it shows or does (options, defaults, states)                                               | Purpose                              | Source                                               | New home |
| --------- | -------------------------------- | ------- | ----------------------------------------------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------- | -------- |
| RECUR-F01 | Time range select                | control | UTC range, default Last 7 days                                                                  | Scope                                | shared filter bar                                    |          |
| RECUR-F02 | Harness select                   | control | All harnesses by default                                                                        | Scope                                | `Recurrence.tsx:110-116`                             |          |
| RECUR-F03 | Actionable repeats KPI           | figure  | Count of clusters crossing the threshold over 7 London days to window end                       | Headline answer                      | `Recurrence.tsx:127-132`                             |          |
| RECUR-F04 | Recurring failure clusters KPI   | figure  | Recurring of total clusters, seen in ≥ 2 tasks                                                  | Breadth of repeats                   | `Recurrence.tsx:133-138`                             |          |
| RECUR-F05 | Recurring targets KPI            | figure  | Files touched in ≥ 2 tasks of all files                                                         | File-level repeats                   | `Recurrence.tsx:139-144`                             |          |
| RECUR-F06 | Localized signatures KPI         | figure  | Share of signatures with ≥ 80% in one project, with n of n                                      | Whether repeats are project-specific | `Recurrence.tsx:145-150`                             |          |
| RECUR-F07 | Daily tool failures chart        | chart   | Stacked bars per harness, count per Europe/London day                                           | Trend                                | `Recurrence.tsx:152-164`                             |          |
| RECUR-F08 | Daily failures table view        | table   | Toggle, day × harness                                                                           | Exact values                         | `Recurrence.tsx:165-175`                             |          |
| RECUR-F09 | Actionable repeats table         | table   | Tool family, failure class, harness breakdown, tools, count, tasks, days, last seen; empty text | Clusters to act on                   | `Recurrence.tsx:178-190`                             |          |
| RECUR-F10 | Recurring failure clusters table | table   | Same columns for all clusters seen in ≥ 2 tasks                                                 | Wider repeat list                    | `Recurrence.tsx:192-200`                             |          |
| RECUR-F11 | Recurring targets table          | table   | File, harnesses, tasks, days, calls, failed                                                     | Hot files                            | `Recurrence.tsx:201-218`                             |          |
| RECUR-F12 | Project concentration table      | table   | Harness, signature, count, projects, top project, top share                                     | Localized vs spread                  | `Recurrence.tsx:219-246`                             |          |
| RECUR-F13 | Harness breakdown cell           | list    | Per-harness counts inside a cluster row, registry order                                         | Compare harnesses unranked           | `Recurrence.tsx:65-79`                               |          |
| RECUR-F14 | Panel info button                | control | i on each KPI and panel                                                                         | Definitions                          | `Recurrence.tsx:131,137,143,149,156,182,196,204,222` |          |
| RECUR-F15 | Harness coverage legend          | state   | Chips, "no rows"/"not applicable"                                                               | Producer coverage                    | shared                                               |          |

### Signals: Recurrence

| ID        | Signal                      | Rule (thresholds → state, colour, word)                                                                            | Computed in             | Source                      | New home |
| --------- | --------------------------- | ------------------------------------------------------------------------------------------------------------------ | ----------------------- | --------------------------- | -------- |
| RECUR-S01 | recur.actionable            | ≥ 3 occurrences in ≥ 2 tasks on ≥ 2 days, or ≥ 5 occurrences in ≥ 3 tasks, over 7 London days (stated in subtitle) | data source             | `Recurrence.tsx:181`        |          |
| RECUR-S02 | recur.signatures            | Cluster seen in ≥ 2 tasks                                                                                          | data source             | `Recurrence.tsx:135,195`    |          |
| RECUR-S03 | recur.targets               | File touched in ≥ 2 tasks                                                                                          | data source             | `Recurrence.tsx:142,203`    |          |
| RECUR-S04 | recur.project_concentration | Localized when top_count ≥ 0.8 × attributed                                                                        | page code (`LOCALIZED`) | `Recurrence.tsx:16,107-109` |          |

### Calculations: Recurrence

| ID        | Output               | Method (function and inputs, or the in-page formula written out)                                                                      | Where                        | Owned by the data source?               | New home |
| --------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------- | --------------------------------------- | -------- |
| RECUR-C01 | Cluster rows         | recombineClusters: group by tool family + failure class; occurrences and tasks summed across harness rows; tools union; last seen max | `Recurrence.tsx:27-62`       | UI (should move to the source of truth) |          |
| RECUR-C02 | Actionable count     | length of recombined actionable rows                                                                                                  | `Recurrence.tsx:104,129`     | UI (should move to the source of truth) |          |
| RECUR-C03 | Target totals        | sumRows(target_summary, targets, recurring)                                                                                           | `Recurrence.tsx:102`         | UI (should move to the source of truth) |          |
| RECUR-C04 | Localized signatures | count(top_count ≥ 0.8 × attributed) / rows                                                                                            | `Recurrence.tsx:107-109,147` | UI (should move to the source of truth) |          |
| RECUR-C05 | Top share            | top_count / attributed                                                                                                                | `Recurrence.tsx:241`         | UI (should move to the source of truth) |          |
| RECUR-C06 | Daily failures       | pivotDaily sum of failures by harness                                                                                                 | `Recurrence.tsx:117-122`     | UI (should move to the source of truth) |          |
| RECUR-C07 | Window end label     | filters.end first 10 chars                                                                                                            | `Recurrence.tsx:123`         | UI (should move to the source of truth) |          |

### Dependencies and side effects: Recurrence

- Reads `/api/view/recurrence` (target_summary, cluster_summary, actionable, clusters, concentration, targets, daily); refetches on filter change.
- No storage; shared filter state only.

## Intent and corrections (`dashboard/src/views/Intent.tsx`)

### Features: Intent and corrections

| ID         | Feature                         | Kind    | What it shows or does (options, defaults, states)                                                           | Purpose                        | Source                                                   | New home |
| ---------- | ------------------------------- | ------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------ | -------------------------------------------------------- | -------- |
| INTENT-F01 | Time range select               | control | UTC range, default Last 7 days                                                                              | Scope                          | shared filter bar                                        |          |
| INTENT-F02 | Harness select                  | control | All harnesses default                                                                                       | Scope                          | `Intent.tsx:128-134`                                     |          |
| INTENT-F03 | Labelled tasks KPI              | figure  | Labelled share with n of n                                                                                  | Coverage of the classifier     | `Intent.tsx:166-171`                                     |          |
| INTENT-F04 | Corrected by next prompt KPI    | figure  | Rate with n of n                                                                                            | Headline correction rate       | `Intent.tsx:172-177`                                     |          |
| INTENT-F05 | Frustrated follow-ups KPI       | figure  | Rate of labelled next prompts                                                                               | Sentiment cost                 | `Intent.tsx:178-183`                                     |          |
| INTENT-F06 | Repeated corrections KPI        | figure  | Count of project × kind in ≥ 2 tasks and ≥ 2 sessions                                                       | Recurring corrections          | `Intent.tsx:184-189`                                     |          |
| INTENT-F07 | Daily corrected rate chart      | chart   | Line per harness, 0-100%                                                                                    | Trend                          | `Intent.tsx:192-216`                                     |          |
| INTENT-F08 | Corrected rate table view       | table   | Toggle day × harness %                                                                                      | Exact values                   | `Intent.tsx:205-215`                                     |          |
| INTENT-F09 | Task type mix                   | chart   | Bar list of labelled tasks by type with share and per-harness counts                                        | What users ask for             | `Intent.tsx:219-246`                                     |          |
| INTENT-F10 | Task type table view            | table   | Toggle harness, type, tasks                                                                                 | Exact values                   | `Intent.tsx:234-245`                                     |          |
| INTENT-F11 | Correction kinds                | chart   | Bar list of tasks by correction kind                                                                        | What is corrected              | `Intent.tsx:247-277`                                     |          |
| INTENT-F12 | Correction kinds table view     | table   | Toggle harness, kind, tasks                                                                                 | Exact values                   | `Intent.tsx:261-276`                                     |          |
| INTENT-F13 | By harness table                | table   | Tasks, labelled, labelled share, corrected next, frustrated next per harness                                | Per-harness counts             | `Intent.tsx:278-319`                                     |          |
| INTENT-F14 | Effort payoff by task type      | table   | Task type × effort, tasks, corrected next, clean completion; note to compare within a task type             | Does effort reduce corrections | `Intent.tsx:321-348`                                     |          |
| INTENT-F15 | Repeated corrections by project | table   | Project, kind, harnesses, tasks, sessions, task types, last seen; empty text                                | Where corrections repeat       | `Intent.tsx:350-390`                                     |          |
| INTENT-F16 | Recent corrected tasks          | table   | Latest 25: harness, session link, start, task type, effort, correction, next sentiment, project; empty text | Drill to examples              | `Intent.tsx:391-441`                                     |          |
| INTENT-F17 | Session link                    | link    | Opens the Session page for the row                                                                          | Evidence                       | `Intent.tsx:407-409`                                     |          |
| INTENT-F18 | Panel info button               | control | i on KPIs and panels                                                                                        | Definitions                    | `Intent.tsx:170,176,182,188,195,222,250,281,325,354,394` |          |
| INTENT-F19 | Harness coverage legend         | state   | Chips with "no rows"/"not applicable"                                                                       | Producer coverage              | shared                                                   |          |

### Signals: Intent and corrections

| ID         | Signal                 | Rule (thresholds → state, colour, word)                      | Computed in | Source               | New home |
| ---------- | ---------------------- | ------------------------------------------------------------ | ----------- | -------------------- | -------- |
| INTENT-S01 | intent.labelled        | Registry signal; no thresholds                               | data source | `Intent.tsx:170`     |          |
| INTENT-S02 | intent.corrected       | Next prompt within 10 minutes corrected the agent (subtitle) | data source | `Intent.tsx:176,194` |          |
| INTENT-S03 | intent.frustration     | Registry signal                                              | data source | `Intent.tsx:182`     |          |
| INTENT-S04 | intent.correction_kind | Repeat = ≥ 2 tasks and ≥ 2 sessions                          | page code   | `Intent.tsx:160-162` |          |
| INTENT-S05 | intent.task_type       | Registry signal                                              | data source | `Intent.tsx:222`     |          |
| INTENT-S06 | intent.effort_payoff   | Registry signal                                              | data source | `Intent.tsx:325`     |          |
| INTENT-S07 | effort.level           | Shared effort signal                                         | data source | `Intent.tsx:325`     |          |

### Calculations: Intent and corrections

| ID         | Output                                | Method (function and inputs, or the in-page formula written out) | Where                    | Owned by the data source?               | New home |
| ---------- | ------------------------------------- | ---------------------------------------------------------------- | ------------------------ | --------------------------------------- | -------- |
| INTENT-C01 | KPI totals                            | sumRows(kpi, tasks, labelled, corrected, frustrated n/den)       | `Intent.tsx:127`         | UI (should move to the source of truth) |          |
| INTENT-C02 | Labelled, corrected, frustrated rates | n / den                                                          | `Intent.tsx:168,174,180` | UI (should move to the source of truth) |          |
| INTENT-C03 | Daily corrected rate                  | pivotDaily corrected_n / corrected_den                           | `Intent.tsx:135-140`     | UI (should move to the source of truth) |          |
| INTENT-C04 | Task type totals and shares           | groupSum by type; share = tasks / labelled total                 | `Intent.tsx:141-154,230` | UI (should move to the source of truth) |          |
| INTENT-C05 | Correction kind totals                | groupSum by kind                                                 | `Intent.tsx:155-157`     | UI (should move to the source of truth) |          |
| INTENT-C06 | Effort payoff                         | effortPayoff: sum counts by type × effort, ratio after           | `Intent.tsx:96-119,122`  | UI (should move to the source of truth) |          |
| INTENT-C07 | Repeated corrections                  | recombineCorrections sums tasks and sessions per project × kind  | `Intent.tsx:58-93`       | UI (should move to the source of truth) |          |
| INTENT-C08 | Repeat count                          | rows with tasks ≥ 2 and sessions ≥ 2                             | `Intent.tsx:160-162,186` | UI (should move to the source of truth) |          |
| INTENT-C09 | Per-harness shares                    | labelled / tasks; corrected and frustrated n / den               | `Intent.tsx:301-314`     | UI (should move to the source of truth) |          |

### Dependencies and side effects: Intent and corrections

- Reads `/api/view/intent` (kpi, daily, task_types, correction_kinds, effort_payoff, repeated, recent); refetches on filter change.
- Session links navigate to the Session page (cross-page link); no storage.

## Interventions (`dashboard/src/views/Interventions.tsx`)

### Features: Interventions

| ID       | Feature                              | Kind    | What it shows or does (options, defaults, states)                                                                                                                                                                                         | Purpose                                                  | Source                      | New home |
| -------- | ------------------------------------ | ------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------- | --------------------------- | -------- |
| INTV-F01 | Page header and question             | text    | V10, title Interventions, question 'Which findings became interventions, and did applied interventions reduce what they targeted?'                                                                                                        | Frames the page                                          | `App.tsx:390-405`           |          |
| INTV-F02 | Active findings KPI                  | figure  | Count of findings in scope; detail = count with state actionable                                                                                                                                                                          | Size of the backlog                                      | `Interventions.tsx:265-270` |          |
| INTV-F03 | Proposals KPI                        | figure  | Count of proposals in scope; detail = count with state pending                                                                                                                                                                            | Pipeline of proposed changes                             | `Interventions.tsx:271-276` |          |
| INTV-F04 | Applied interventions KPI            | figure  | Count of proposals with applied_at; detail = validated, regressed, inconclusive verdict counts                                                                                                                                            | Headline result of interventions                         | `Interventions.tsx:277-282` |          |
| INTV-F05 | Repeated corrections KPI             | figure  | Count of findings whose detector is facts.repeated_correction; fixed detail text                                                                                                                                                          | Uncodified practices                                     | `Interventions.tsx:283-288` |          |
| INTV-F06 | Post-intervention recurrence table   | table   | One row per intervention applied inside the range: target, finding, tier, applied time, window days, before/after matched and task counts, rate change %, verdict ('not evaluated' when null), recorded ratio; empty state text when none | Answers whether an intervention reduced what it targeted | `Interventions.tsx:290-339` |          |
| INTV-F07 | Recurrence baseline chart            | chart   | Daily line chart, two series (cluster failures per 100 tasks, corrections per 100 labelled tasks), one y-axis, 170px, legend, hover readout                                                                                               | Background rate before/after interventions               | `Interventions.tsx:340-363` |          |
| INTV-F08 | Recurrence baseline View as table    | control | Disclosure that lists the chart data as a table (closed by default)                                                                                                                                                                       | Exact values, non-visual reading                         | `Interventions.tsx:352-362` |          |
| INTV-F09 | Active findings table                | table   | Detector, subject, harness chips, impact, state, count, tasks, days, last seen; 340px scroll box; empty 'No findings recorded.'                                                                                                           | Backlog of findings                                      | `Interventions.tsx:366-411` |          |
| INTV-F10 | Proposals table                      | table   | Target, finding, tier, state, success metric (or 'free text (legacy)'), applied, verdict, ratio, baseline → evaluation rate                                                                                                               | Proposal status and evaluation                           | `Interventions.tsx:412-463` |          |
| INTV-F11 | Enforcement-tier audit table         | table   | Proposals per tier; null tier shows 'not recorded'                                                                                                                                                                                        | Tier mix                                                 | `Interventions.tsx:464-477` |          |
| INTV-F12 | Uncodified practice recurrence table | table   | Project, correction kind, harnesses, task types, sessions, tasks, state                                                                                                                                                                   | Where users repeat corrections                           | `Interventions.tsx:478-505` |          |
| INTV-F13 | Harness scope filter                 | control | Harness selection keeps findings/proposals whose harness list includes it; older rows without a list show under All only                                                                                                                  | Filter                                                   | `Interventions.tsx:187-196` |          |
| INTV-F14 | Panel info control (x9)              | control | 'i' button expands registry note in the panel footer                                                                                                                                                                                      | Definition and route per signal                          | `components.tsx:250-272`    |          |

### Signals: Interventions

| ID       | Signal                                                                          | Rule (thresholds → state, colour, word)                                      | Computed in                                    | Source                              | New home |
| -------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ---------------------------------------------- | ----------------------------------- | -------- |
| INTV-S01 | Finding state                                                                   | Word from the store (e.g. actionable, pending); printed raw, no colour       | Data source (facts sync)                       | `Interventions.tsx:398,268,274`     |          |
| INTV-S02 | Verdict                                                                         | validated / regressed / inconclusive / not evaluated; printed raw, no colour | Data source; 'not evaluated' substituted in UI | `Interventions.tsx:327,442`         |          |
| INTV-S03 | Tier                                                                            | Enforcement tier string, null shown as 'not recorded'                        | Data source; label in UI                       | `Interventions.tsx:247`             |          |
| INTV-S04 | Signal ids intervene.findings, tier_audit, post_recurrence, practice_recurrence | Registry supplies definition and applicability per panel                     | Registry                                       | `Interventions.tsx:269,275,281,287` |          |

### Calculations: Interventions

| ID       | Output                                   | Method (function and inputs, or the in-page formula written out)                                                                                                                                                                                                                                             | Where                                                   | Owned by the data source?               | New home |
| -------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------- | --------------------------------------- | -------- |
| INTV-C01 | Findings / actionable counts             | findings.length and filter(state === 'actionable') after harness scope                                                                                                                                                                                                                                       | Interventions.tsx:190-196,267-268                       | UI (should move to the source of truth) |          |
| INTV-C02 | Proposals / pending counts               | proposals.length and filter(state === 'pending') after scope                                                                                                                                                                                                                                                 | Interventions.tsx:196,273-274                           | UI (should move to the source of truth) |          |
| INTV-C03 | Applied count and verdict counts         | proposals with applied_at; groupSum of verdict over applied rows                                                                                                                                                                                                                                             | Interventions.tsx:197,212-218,279-280                   | UI (should move to the source of truth) |          |
| INTV-C04 | Repeated corrections count               | findings filtered to detector facts.repeated_correction                                                                                                                                                                                                                                                      | Interventions.tsx:198-200,285                           | UI (should move to the source of truth) |          |
| INTV-C05 | Harness scope                            | harness in metric_harnesses, else harnesses; else hidden under a filter                                                                                                                                                                                                                                      | Interventions.tsx:190-194                               | UI (should move to the source of truth) |          |
| INTV-C06 | Interventions in range                   | applied_at strictly between filters.start and filters.end                                                                                                                                                                                                                                                    | Interventions.tsx:206-210                               | UI (should move to the source of truth) |          |
| INTV-C07 | Before/after windows (prePost)           | Equal runs of whole London days before and after the application day, clipped to the range, shorter side sets both; matched = tasks in the finding's cluster (or corrected tasks in project and kind) summed over metric harnesses; tasks = all tasks (or labelled tasks in project); rate = matched / tasks | Interventions.tsx:78-165 (uses londonDay/addDays 24-33) | UI (should move to the source of truth) |          |
| INTV-C08 | Rate change %                            | 100 × (post_rate − pre_rate) / pre_rate, blank when pre is 0 or a rate is missing                                                                                                                                                                                                                            | Interventions.tsx:316-322                               | UI (should move to the source of truth) |          |
| INTV-C09 | Daily failures per 100 tasks             | ratio(sum occurrences per day, sum tasks per day) via groupSum and ratio                                                                                                                                                                                                                                     | Interventions.tsx:219-239 with format.ts:83-110         | UI (should move to the source of truth) |          |
| INTV-C10 | Daily corrections per 100 labelled tasks | ratio(sum corrected, sum tasks) from correction_daily                                                                                                                                                                                                                                                        | Interventions.tsx:220-238                               | UI (should move to the source of truth) |          |
| INTV-C11 | Proposals per tier                       | groupSum of 1 per proposal by tier                                                                                                                                                                                                                                                                           | Interventions.tsx:240-249                               | UI (should move to the source of truth) |          |
| INTV-C12 | Baseline → evaluation rate               | 100 × baseline_rate and 100 × evaluation_rate formatted as percentages                                                                                                                                                                                                                                       | Interventions.tsx:454-458                               | UI (should move to the source of truth) |          |
| INTV-C13 | Recorded ratio                           | evaluated_ratio formatted to 2 decimals with ×                                                                                                                                                                                                                                                               | Interventions.tsx:179-180                               | Yes (value from data; formatting only)  |          |

### Dependencies and side effects: Interventions

- Network: `GET /api/view?view=interventions&start&end&harness` via App (`App.tsx:269-279`) returning findings, proposals, cluster_daily, correction_daily, task_daily; `GET /api/registry` once.
- Storage: none; filters in URL query. Cross-page state: harness/time filters carried through nav links (`App.tsx:236`).
- Side effects: heading focus on view change (`App.tsx:305-308`).

## Session (`dashboard/src/views/Session.tsx`)

### Features: Session

| ID       | Feature                       | Kind  | What it shows or does (options, defaults, states)                                                                                                                                                                                | Purpose                                         | Source                | New home |
| -------- | ----------------------------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------- | --------------------- | -------- |
| SESS-F01 | Page header and question      | text  | Eyebrow 'Drill-down', title Session, question 'Tasks, usage, tool calls, and user signals for one session.'; no filter bar (harness comes from the URL)                                                                          | Frames the page                                 | `App.tsx:390-405,418` |          |
| SESS-F02 | Session head                  | state | Harness name with colour dot, session id in mono, and '{n} tasks · {n} tool calls · {n} failed'                                                                                                                                  | Identifies the session and sizes it             | `Session.tsx:16-22`   |          |
| SESS-F03 | Tasks table                   | table | Started (UTC), model, effort, task type, duration, steps, reasoning tokens, interrupted, steered, errored, follow-up, clean, corrected next (with kind), friction observed; 340px scroll box; empty message for no task boundary | Per-task outcome and friction                   | `Session.tsx:23-93`   |          |
| SESS-F04 | Model usage table             | table | Model, effort, operations, input, cached, output, reasoning tokens per model and effort                                                                                                                                          | Token spend by model and effort                 | `Session.tsx:95-130`  |          |
| SESS-F05 | Model calls table             | table | Outcome, error class, calls, TTFT p50                                                                                                                                                                                            | Call success and latency                        | `Session.tsx:131-152` |          |
| SESS-F06 | Tool calls table              | table | At (UTC time), tool, outcome, command, exit code, failure signature, targets; 340px scroll box                                                                                                                                   | Tool call log                                   | `Session.tsx:154-189` |          |
| SESS-F07 | User and policy signals table | table | At, signal, detail, source, tool; empty message when none                                                                                                                                                                        | Interrupts, steers, approvals, sandbox outcomes | `Session.tsx:190-210` |          |
| SESS-F08 | Live query status             | state | 'Queried HH:MM:SS UTC' (no elapsed time)                                                                                                                                                                                         | Freshness                                       | `App.tsx:406-416`     |          |

### Signals: Session

| ID       | Signal                         | Rule (thresholds → state, colour, word)              | Computed in | Source                        | New home |
| -------- | ------------------------------ | ---------------------------------------------------- | ----------- | ----------------------------- | -------- |
| SESS-S01 | Yes/no flag                    | 1 → 'yes', 0 → 'no', null → '—'; no colour           | UI          | `Session.tsx:6-7`             |          |
| SESS-S02 | Corrected next                 | 1 with correction kind → 'yes (kind)'; else flag     | UI          | `Session.tsx:81-84`           |          |
| SESS-S03 | Outcome / error class / signal | Printed raw from the data source; empty string → '—' | Data source | `Session.tsx:135-139,165,177` |          |

### Calculations: Session

| ID       | Output                          | Method (function and inputs, or the in-page formula written out) | Where                         | Owned by the data source?               | New home |
| -------- | ------------------------------- | ---------------------------------------------------------------- | ----------------------------- | --------------------------------------- | -------- |
| SESS-C01 | Task count                      | tasks.length                                                     | Session.tsx:11,20             | UI (should move to the source of truth) |          |
| SESS-C02 | Tool call count                 | calls.length                                                     | Session.tsx:12,20             | UI (should move to the source of truth) |          |
| SESS-C03 | Failed call count               | calls.filter(outcome === 'failed').length                        | Session.tsx:13                | UI (should move to the source of truth) |          |
| SESS-C04 | Command                         | command_head + ' ' + command_sub, trimmed                        | Session.tsx:170               | UI (should move to the source of truth) |          |
| SESS-C05 | Time formatting                 | slice(0,19) of started/at; slice(11,19) for tool call time       | Session.tsx:31,162,198        | Yes (formatting only)                   |          |
| SESS-C06 | Duration / TTFT / token formats | fmtSeconds and fmtCount over source values                       | Session.tsx:44,51,106-124,146 | Yes (formatting only)                   |          |

### Dependencies and side effects: Session

- Network: `GET /api/session?harness&session` (`App.tsx:272-273`) returning tasks, usage, model_calls, calls, signals; `GET /api/registry` once.
- Storage: none; harness and session id are in the URL. Reached from `SessionLink` (`components.tsx:350-367`), which carries current filters.
- Side effects: heading focus on load; document title `Session · Agent introspection` (`App.tsx:305-308`).
