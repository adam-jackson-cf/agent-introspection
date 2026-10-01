# Discovery: Agent Introspection dashboard

What the app has today and what each part is for, as found on 2026-09-30. Every row cites `file:line`. **Basis**
is `observed` (code, comments, docs or labels say so), `inferred` (follows from how it's used) or `guessed`
(nothing supports it beyond the name). **Confidence** is `high` or `low`; only `low` rows become questions in
the brief.

App root: repository root. UI: `dashboard/` (Bun + React 19 + TypeScript, hand-written SVG charts, no
component or chart library). Row prefixes: unprefixed = shell/shared; `V1…` = Cache, Effort, Friction,
Guardrails, Interventions; `V2…` = Pipeline, Provider, Recurrence, Session, Tools.

## App purpose

| Item | Finding | Basis | Confidence | Evidence |
| --- | --- | --- | --- | --- |
| Users and expertise | One person exploring local omp, Codex app-server/CLI/exec, and Claude Code model usage and agent process; no expertise level specified. | observed (user count); expertise unknown | low | README.md:62-64 |
| Main job | Surface producer-supported measures so a user can explore model usage/process and find problems and improvements. | observed | high | docs/dashboard-v3-plan.md:7-15; README.md:62-64 |
| Second job | Track findings/intervention proposals and approval history; does not apply proposals. | observed | high | README.md:62-66 |

## Existing standards and repo

| ID | Element | Where | What it is | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| R01 | UX standards | repo root, `docs/` | None: no `DESIGN.md`, `docs/ux/`, rubric, brief or reviews | — | observed | high |
| R02 | Root agent file | repo root | No `AGENTS.md`/`CLAUDE.md` tracked; `.agents/skills/` exists | Agent instructions live in skills only | observed | high |
| R03 | Git-ignored private folder | `.gitignore:3` | `.tmp/` is ignored and already holds screenshots (`.tmp/recurrence-react-after.png`) | Private run output, including UI screenshots | observed | high |
| R04 | Plans | `docs/dashboard-v3-plan.md`, `docs/dashboard-measure-v3.md`, `docs/dashboard-data-gaps.md` | View/measure plan, signal registry spec, data-gap list | Canonical intent for views and measures | observed | high |
| R05 | Unapproved draft standards | `/tmp/ux-preview/DESIGN.md`, `/tmp/ux-preview/principles.md` (outside repo) | Earlier draft from a previous version of this skill | Source of proposed defaults only; not approved | observed | high |
| R06 | Stack | `dashboard/package.json` | React 19, React DOM, TypeScript, Bun; charts in `dashboard/src/charts.tsx` | — | observed | high |

## Pages

| ID | Page | Route and file | What it is (reads or changes data) | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| PG1 | Pipeline (V1) | `/pipeline` — dashboard/src/App.tsx:39-46; dashboard/src/views/Pipeline.tsx:1-444 | Reads loader freshness, route coverage, parity, project sync, and harness support. | Check whether fact loading is fresh, complete, and correct for every harness. | observed | high |
| PG2 | Cache efficiency (V2) | `/cache` — dashboard/src/App.tsx:48-55; dashboard/src/views/Cache.tsx:1-315 | Reads usage, cache, model and session aggregates. | Locate token consumption and cached input share. | observed | high |
| PG3 | Reasoning effort (V3) | `/effort` — dashboard/src/App.tsx:56-62; dashboard/src/views/Effort.tsx:1-458 | Reads reasoning, effort and task outcome aggregates. | Assess whether heavier reasoning earns its cost. | observed | high |
| PG4 | Tool failures (V4) | `/tools` — dashboard/src/App.tsx:63-70; dashboard/src/views/Tools.tsx:1-267 | Reads tool outcomes, repeats, loops and signatures. | Find failing/repeating tools and affected tasks. | observed | high |
| PG5 | Friction (V5) | `/friction` — dashboard/src/App.tsx:71-78; dashboard/src/views/Friction.tsx:1-303 | Reads interruptions, steering, follow-ups, recovery and completion. | Inspect user steering and task completion signals. | observed | high |
| PG6 | Guardrails (V6) | `/guardrails` — dashboard/src/App.tsx:79-86; dashboard/src/views/Guardrails.tsx:1-262 | Reads decisions, sandbox outcomes, bypass and churn. | Inspect approvals, sandbox and quality-gate blocks or bypasses. | observed | high |
| PG7 | Provider (V7) | `/provider` — dashboard/src/App.tsx:87-94; dashboard/src/views/Provider.tsx:1-328 | Reads model-call outcomes, latency, disconnects, retries, errors. | Find model call failures, latency, disconnects, retries and model mismatch. | observed | high |
| PG8 | Recurrence (V8) | `/recurrence` — dashboard/src/App.tsx:95-102; dashboard/src/views/Recurrence.tsx:1-197 | Reads recurring files/failure signatures and actionability/concentration. | Identify recurrence warranting intervention. | observed | high |
| PG9 | Interventions (V9) | `/interventions` — dashboard/src/App.tsx:103-110; dashboard/src/views/Interventions.tsx:1-305 | Reads findings, proposals, applied interventions and historical comparison. | Track intervention lifecycle and whether application reduced targeted failures. | observed | high |
| PG10 | Session drill-down | `/session?harness=…&session=…` — dashboard/src/App.tsx:138-154; dashboard/src/views/Session.tsx:1-200 | Reads one session's tasks, usage, tool calls, model calls and non-prompt signals. | Inspect signals for a single session. | observed | high |
| PG11 | Intent and corrections (V9; Interventions is now V10) | `/intent` — dashboard/src/App.tsx:103-110; dashboard/src/views/Intent.tsx | Reads task types, effort and next-prompt correction rows; renders trends and tables with session links (added after the first discovery pass; refreshed 2026-09-30) | Show what kind of work users ask for and how often their next prompt corrects the agent. | observed (VIEWS question) | high |
| V1PG1 | Cache | Route not evidenced here; `dashboard/src/views/Cache.tsx:35` | Reads usage KPI, daily, model, and session rows; renders totals, trends and tables. | Understand input-cache utilization and where uncached input occurs, down to harness/model/session. | observed | high |
| V1PG2 | Effort | Route not evidenced here; `dashboard/src/views/Effort.tsx:33` | Reads usage, outcomes, daily and task rows; renders trends and comparisons. | Relate reasoning effort/spend to task outcomes and inspect the heaviest reasoning tasks. | observed | high |
| V1PG3 | Friction | Route not evidenced here; `dashboard/src/views/Friction.tsx:34` | Reads friction KPI, daily, recovery and task rows. | Show observed task-friction rates and recovery, with per-harness observability distinguished from zero. | observed | high |
| V1PG4 | Guardrails | Route not evidenced here; `dashboard/src/views/Guardrails.tsx:24` | Reads decisions, sandbox, bypass and command-churn rows. | Inspect approval/rejection, sandbox denials, bypasses and command churn. | observed | high |
| V1PG5 | Interventions | Route not evidenced here; `dashboard/src/views/Interventions.tsx:105` | Reads findings, proposals, signatures and task aggregates; no mutation shown. | Monitor findings/proposals and compare target recurrence before and after applied interventions. | observed | high |
| V2PG1 | Pipeline | route [not established here]; `dashboard/src/views/Pipeline.tsx:87` | Reads loaders, freshness, coverage, parity, sanitization, recombination, project attribution/sync/rejections, stray and unrouted rows | Lets an operator check freshness, completeness, correctness, privacy sanitation and project attribution of the measurement pipeline. | observed | high |
| V2PG2 | Provider | route [not established here]; `dashboard/src/views/Provider.tsx:17` | Reads provider call, daily, latency, retry, error and conformance aggregates | Helps compare model-call reliability and latency by harness/model and inspect errors, retries and model mismatch. | observed | high |
| V2PG3 | Recurrence | route [not established here]; `dashboard/src/views/Recurrence.tsx:15` | Reads recurring failure signatures, targets, actionable repeats, concentration and daily aggregates | Surfaces repeated failures and targets, highlighting evidence that crosses actionable thresholds. | observed | high |
| V2PG4 | Session | route [not established here]; `dashboard/src/views/Session.tsx:9` | Reads one session's tasks, model usage/calls, tool calls and user/policy signals | Provides a session-level chronological/detail view of task, model, tool and user/policy activity. | inferred | high |
| V2PG5 | Tools | route [not established here]; `dashboard/src/views/Tools.tsx:20` | Reads tool call, failure, task, repetition, loop and daily aggregates | Helps locate tool failures and repeated attempts/loops by tool and drill into example sessions. | observed | high |

## Shared components

| ID | Component | Where | What it is | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| CO1 | View context / not-emitted handling | dashboard/src/components.tsx:19-58 | Provides registry, contributions, filters, session URL; determines absent signal support. | Coordinate consistent harness attribution and avoid presenting unsupported signals as zero. | observed | high |
| CO2 | Contributors | dashboard/src/components.tsx:80-120 | Per-harness row contribution indicators with native `title` details. | Show which harnesses contributed data to panel signals. | observed | high |
| CO3 | InfoNote | dashboard/src/components.tsx:123-183 | Registry-driven signal question, formula, route, alignment and row counts. | Explain definitions and per-harness production routes. | observed | high |
| CO4 | Panel / Boundary / NotEmittedNotice | dashboard/src/components.tsx:185-280 | Panel frame, error boundary, unsupported-signal notice and expandable info. | Keep panel status, explanation, and content consistently framed. | observed | high |
| CO5 | Kpi / Section | dashboard/src/components.tsx:282-316 | Headline metric within panel; titled section and grid. | Structure summary measures and related panels. | inferred | high |
| CO6 | HarnessSelector / HarnessName | dashboard/src/components.tsx:318-344 | Selector and colored harness label. | Filter/identify harness-specific results. | inferred | high |
| CO7 | SessionLink | dashboard/src/components.tsx:346-365 | Link to session route, shortened ID. | Navigate from aggregate rows to session evidence. | inferred | high |
| CO8 | DailyChart / Legend | dashboard/src/charts.tsx:39-96 | Responsive SVG stacked-column or line chart with continuous days, hover/focus values. | Explore time trends and gaps. | observed | high |
| CO9 | DataTable / TableView / BarList | dashboard/src/charts.tsx:300-428 | Tabular and bar-list visualizations; table view disclosed with `<details>`. | Read chart data as values or ranked rows. | inferred | high |
| V1CP1 | Section | `dashboard/src/views/Cache.tsx:69`; `dashboard/src/components.tsx:292-` | Groups page content under a heading. | Give each view a scannable section hierarchy. | observed | high |
| V1CP2 | Kpi | `dashboard/src/views/Cache.tsx:70`; `dashboard/src/components.tsx:254-` | A `Panel` with one headline value and optional detail. | Surface a compact at-a-glance measure. | observed | high |
| V1CP3 | Panel | `dashboard/src/components.tsx:188-252` | Titled panel with subtitle, contributors, expandable registry info and body; replaces body with not-emitted explanation where applicable. | Frame a measure and explain its data support rather than misstate absent emissions as zero. | observed | high |
| V1CP4 | Charts and tables | `dashboard/src/views/Cache.tsx:1` | Views use `DailyChart`, `DataTable`, and `TableView` from charts module. | Present time series and tabular detail. | inferred | high |
| V1CP5 | HarnessName / SessionLink | `dashboard/src/views/Cache.tsx:4-9` | Shared identity rendering and session navigation. | Identify producer and allow opening a session from detailed rows. | inferred | high |
| V2CP1 | Section | `dashboard/src/components.tsx:315-327` | Heading plus grid containing section content | Groups related panels under a scannable section heading. | observed | high |
| V2CP2 | Panel / Kpi | `dashboard/src/components.tsx:250-312` | Signal-aware card; KPI is panel with headline value and optional detail | Presents a measured signal with contributor indication and expandable registry notes; KPIs summarize it. | observed | high |
| V2CP3 | HarnessName | `dashboard/src/components.tsx:352-359` | Harness label with identity colour marker | Makes harness identity recognizable in tables and filter-context content. | observed | high |
| V2CP4 | SessionLink | `dashboard/src/components.tsx:361-365` | Context-backed link to a harness/session | Links aggregate examples to session detail. | observed | high |
| V2CP5 | DataTable / DailyChart / TableView | `dashboard/src/views/Provider.tsx:1` | Imported tabular, chart, and chart-data table components | Provides visual and tabular ways to inspect aggregates and daily trends. | inferred | high |

## Tokens: definitions and consumers

`colour_usage.py` summary: 26 files scanned; 6 files hold 85 colour literals, all outside any allow-list
(`styles.css` 62, `format.ts` 18, `Friction.tsx` 2, `Effort.tsx` 1, `Guardrails.tsx` 1, `index.html` 1). One
file defines custom properties (`styles.css`, 15) and one reads them (`styles.css`, 61 reads). No TS file reads
a CSS variable. Font: `13px/1.45 Inter, ui-sans-serif, …` (`dashboard/src/styles.css:18-19`); tabular numbers
in charts, tables and values (`styles.css:438,493,551,592`).

| ID | Kind (definition or consumer) | Where | Idiom (CSS variable, TS/JS constant, SVG prop, canvas, library option, Python) | Values or tokens | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| TK1 | definition | dashboard/src/styles.css:3-17 | CSS custom properties | `--bg #090a0d`, `--side #0d0f13`, `--panel #13161c`, `--line #292d37`, `--grid #252a34`, `--text #f2f3f5`, `--secondary #c3c7d0`, `--muted #969cab`, `--purple #8b5cf6`, `--purple2 #a78bfa`, `--good #0ca30c`, `--warning #fab219`, `--serious #ec835a`, `--critical #d03b3b`, `--radius 10px` | Dark surfaces, text hierarchy, accents, state tones, corner rounding. | observed | high |
| TK2 | consumer | dashboard/src/styles.css:31-41,57-59,101-105,112-115,128-135,149,168,183,187-190,202-204,235,251,263-265 | CSS variable | `--bg`, `--text`, `--side`, `--line`, `--purple`, `--purple2`, `--muted`, `--secondary`, `--panel`, `--radius` | Base/page, navigation, headings, filter and panel surfaces. | inferred | high |
| TK3 | consumer | dashboard/src/styles.css:328-365,388,401,414,424,436-441,455,487,492,510,535,550,556 | CSS variable | `--secondary`, `--muted`, `--purple2`, `--grid`, `--panel`, `--text` | Contributor, notes, charts, legends, tables and value typography. | inferred | high |
| TK4 | consumer | dashboard/src/styles.css:574,621,649,653,657,661,679,682,735,759-760,765 | CSS variable | `--line`, `--purple2`, `--good`, `--warning`, `--serious`, `--critical`, `--muted`, `--text`, `--side` | Table/status tones, keyboard focus and compact navigation. | inferred | high |
| TK5 | consumer | dashboard/src/format.ts:8-23; dashboard/src/components.tsx:108-112,340-344 | TS constant; inline CSS background | `HARNESS_COLOR`: omp `#3987e5`, Codex app-server `#d95926`, Codex CLI `#199e70`, Codex exec `#c98500`, Claude Code `#d55181`; `SERIES` same sequence | Stable harness categorical identity, independent of rank. | observed | high |
| TK6 | consumer | dashboard/src/format.ts:22-23; dashboard/src/views/Cache.tsx:57-66; dashboard/src/views/Interventions.tsx:217-223 | TS constant; SVG prop via series | `SERIES` slots blue, orange, green, ochre, pink in order | Categorical series palette for non-harness chart values. | observed | high |
| TK7 | definition | dashboard/src/format.ts:25-45 | TS constants | effort order low, medium, high, xhigh, mixed, unset; colors `#184f95`, `#2a78d6`, `#6da7ec`, `#b7d3f6`, `#9085e9`, `#6b7280` | Encode effort's ordinal ramp while keeping mixed/unset apart. | observed | high |
| TK8 | consumer | dashboard/src/views/Effort.tsx:21-28; dashboard/src/charts.tsx:229-230 | TS constant; SVG stroke prop | `EFFORT_COLOR`, unknown fallback `#6b7280` | Color effort series in charts. | observed | high |
| TK9 | consumer | dashboard/src/styles.css:69-79,91,95-100,114,129,142-143,150,189,204,211,221-224,244,265,298,339,374,393,398,408,445,459,467,479,575-576,586-587,645,650,654,658,662,694-697,708-709,724-725 | CSS literal | Every non-variable colour literal on these lines is categorized in respective rows below; remaining CSS source contains no colour literals. | Preserve bespoke brand, interaction, form, chart, table, state, error, loading and skip-link colors. | inferred | high |
| TK10 | consumer | dashboard/src/styles.css:69-100 | CSS literal | brand muted `#646b78`; mark gradient `#5b21b6`, glow `#8b5cf64d`; nav `#b9bec9`, hover `#171a20/#fff`, active `#211a33/#fff` | Brand mark and navigation states. | inferred | high |
| TK11 | consumer | dashboard/src/styles.css:114-150 | CSS literal | `#111319`, `#090a0dec`, `#13161b`, `#b8bdc8`, `#fff` | Note/card, topbar, refresh/details control and hover surfaces/text. | inferred | high |
| TK12 | consumer | dashboard/src/styles.css:189-224,244 | CSS literal | `#111319`, `#7f8694`, `#343945`, `#171a20`, `#e7e9ed`, `#c9cdd5` | Filter, live metadata and section headings. | inferred | high |
| TK13 | consumer | dashboard/src/styles.css:265-298,339,374,393,398,408,445,459,467,479 | CSS literal | `#101319`, `#4a505d`, `#222630`, `#3a404c`, `#6b5a2c`, `#ffffff10`, `#0d0f13f2` | Panel gradient, contributors, table separators, alignment, chart strokes/focus and tooltip. | inferred | high |
| TK14 | consumer | dashboard/src/styles.css:575-587 | CSS literal | `#12151a`, `#7f8795`, `#222630`, `#cbd0d8` | Table header/body surface and text. | inferred | high |
| TK15 | consumer | dashboard/src/styles.css:645-662 | CSS literal | neutral chip `#262a33`, foreground `#fff`/`#111` with state variables | State chip neutral and foreground contrast. | inferred | high |
| TK16 | consumer | dashboard/src/styles.css:694-697,708,724-725 | CSS literal | Error notice `#6e3c3c`, `#261718`, `#f3c6c6`; skeleton `#20242d`, `#303642`; focused skip link `#fff/#000` | Error, loading and keyboard skip-link presentation. | inferred | high |
| TK17 | consumer | dashboard/src/views/Friction.tsx:129-131; dashboard/src/views/Guardrails.tsx:158-160 | SVG prop | `#3987e5`, `#d95926`, unknown harness fallback `#6b7280` | Signal-series colors and fallback. | inferred | high |
| TK18 | consumer | dashboard/index.html:6 | HTML theme-color | `#090a0d` | Browser UI dark theme hint matching page background. | inferred | high |
| TK19 | consumer | dashboard/src/charts.tsx:13-14 | TS constant | chart height `170`, padding top/right/bottom/left `10/8/22/48` | Chart geometry. | observed | high |
| V1TK1 | consumer | `dashboard/src/views/Effort.tsx:26-29` | TS/JS constant | `EFFORT_COLOR[effort]`, fallback `#6b7280` | Color effort categories in ordered series. | observed | high |
| V1TK2 | consumer | `dashboard/src/views/Friction.tsx:129-131` | SVG/chart series prop | Interrupt `#3987e5`; Steer `#d95926` | Distinguish interrupt and steer in the stacked daily chart. | observed | high |
| V1TK3 | consumer | `dashboard/src/views/Guardrails.tsx:160-162` | TS/JS constant / chart item prop | Harness color; unknown-harness fallback `#6b7280` | Encode harness identity in approval-source bars. | observed | high |
| V1TK4 | definition | `dashboard/src/format.ts:3-16` | TS/JS constant | harness: omp `#3987e5`, Codex app-server `#d95926`, Codex CLI `#199e70`, Codex exec `#c98500`, Claude Code `#d55181`; `SERIES` same five in order | Stable categorical identity colors and non-harness series colors. | observed | high |
| V1TK5 | definition | `dashboard/src/format.ts:18-36` | TS/JS constant | effort order low, medium, high, xhigh, mixed, unset; colors respectively `#184f95`, `#2a78d6`, `#6da7ec`, `#b7d3f6`, `#9085e9`, `#6b7280` | Ordinal effort ramp with mixed/unset set apart. | observed | high |
| V2TK1 | definition | `dashboard/src/format.ts:3-15` | TS/JS constant | Harness colors: omp `#3987e5`, Codex app-server `#d95926`, Codex CLI `#199e70`, Codex exec `#c98500`, Claude Code `#d55181`; labels defined beside them | Keep harness identity consistent across series and inline markers; comments say color does not follow rank. | observed | high |
| V2TK2 | consumer | `dashboard/src/views/Provider.tsx:42-46` | TS/JS constant passed as chart series color | `HARNESS_COLOR[harness]` with `HARNESS_LABEL[harness]` | Colors/labels daily trend series consistently by harness. | observed | high |
| V2TK3 | consumer | `dashboard/src/views/Recurrence.tsx:28-32` | TS/JS constant passed as chart series color | `HARNESS_COLOR[harness]` with `HARNESS_LABEL[harness]` | Colors/labels daily failure series by harness. | observed | high |
| V2TK4 | consumer | `dashboard/src/views/Tools.tsx:29-33` | TS/JS constant passed as chart series color | `HARNESS_COLOR[harness]` with `HARNESS_LABEL[harness]` | Colors/labels daily tool series by harness. | observed | high |
| V2TK5 | consumer | `dashboard/src/views/Pipeline.tsx:119-123` | TS/JS constant passed as chart series color | `HARNESS_COLOR[harness]` with `HARNESS_LABEL[harness]` | Colors/labels daily fact-row series by harness. | observed | high |
| V2TK6 | consumer | `dashboard/src/views/Pipeline.tsx:16-24` | CSS class/tone name | `good`, `neutral`, `warning`, `serious`, `critical` | Assigns semantic state styling to coverage-state chips. | observed | high |

## Chart palettes

| ID | Palette | Defined at | Role (categorical, ordinal/sequential, neutral) | Members in order, with meaning | How charts receive it | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| CP1 | Harness identity | dashboard/src/format.ts:8-20 | categorical | blue omp; orange Codex app-server; green Codex CLI; ochre Codex exec; pink Claude Code | Harness series map `HARNESS_COLOR` into `Series.color`, passed as SVG stroke/fill and legend style (dashboard/src/views/Cache.tsx:62-66; dashboard/src/charts.tsx:229-230,293-296). | Preserve harness identity; explicitly not rank. | observed | high |
| CP2 | Non-harness series | dashboard/src/format.ts:22-23 | categorical | blue, orange, green, ochre, pink | Selected slots passed into chart series (dashboard/src/views/Cache.tsx:57-60). | Distinguish series parts. | inferred | high |
| CP3 | Effort | dashboard/src/format.ts:25-45 | ordinal/sequential | low dark blue, medium blue, high light blue, xhigh pale blue, mixed purple, unset grey | Effort chart creates series by `EFFORT_COLOR` (dashboard/src/views/Effort.tsx:21-28). | Convey effort ordering; separate mixed/unset. | observed | high |
| V1CPa1 | Harness identity | `dashboard/src/format.ts:3-16` | categorical | `#3987e5` omp; `#d95926` Codex app-server; `#199e70` Codex CLI; `#c98500` Codex exec; `#d55181` Claude Code | Views map harness to `{key,label,color}` series (`Cache.tsx:50-55`, `Friction.tsx:40-45`, `Guardrails.tsx:33-38`). | Keep producer identity recognizable across charts. | observed | high |
| V1CPa2 | General series | `dashboard/src/format.ts:17` | categorical | `SERIES`: blue, orange, green, ochre, pink in listed order; specific semantic assignment is by consumer. | Passed as `color` in series (`Cache.tsx:46-49`, `Interventions.tsx:219-224`). | Supply fixed categorical colors for non-harness series. | observed | high |
| V1CPa3 | Effort | `dashboard/src/format.ts:19-36` | ordinal/sequential | low `#184f95`, medium `#2a78d6`, high `#6da7ec`, xhigh `#b7d3f6`; mixed `#9085e9`; unset `#6b7280` | Effort view maps category through `EFFORT_COLOR` to series color (`Effort.tsx:17-30`). | Represent effort ordering while distinguishing mixed and provider-default unset. | observed | high |
| V1CPa4 | Interrupt/steer | `dashboard/src/views/Friction.tsx:129-131` | categorical | Interrupt `#3987e5`; Steer `#d95926` | Inline `DailyChart` series entries. | Differentiate two daily event counts. | observed | high |
| V2TK7 | Harness identity palette | `dashboard/src/format.ts:3-15` | categorical | `#3987e5` omp; `#d95926` Codex app-server; `#199e70` Codex CLI; `#c98500` Codex exec; `#d55181` Claude Code | Views create `series` entries `{key,label,color}` and pass them to `DailyChart`, e.g. `dashboard/src/views/Provider.tsx:42-46,97-103`. | Preserve stable harness identity across charts; explicit comment says not rank-coded. | observed | high |

## States

One row per status scale. Never merge scales.

| ID | Scale | Defined at | Levels (count) | Words in order | Colours in order | Thresholds | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| STS1 | Pipeline coverage status | dashboard/server/pipeline.ts:105-160; dashboard/src/views/Pipeline.tsx:14-23; dashboard/src/styles.css:648-662 | 7 | healthy, idle, possible break, no events, not emitted, stray (explained), stray (unexplained) | healthy `--good #0ca30c`; idle/no events/not emitted neutral `#262a33`; possible break `--serious #ec835a`; stray explained `--warning #fab219`; stray unexplained `--critical #d03b3b` | Coverage logic: active harness with expected rows but route has none -> possible break; `expect=events` zero -> no events; other support/stray logic per server/pipeline.ts:121-160. No numeric thresholds specified for state mapping. | Show, per signal and harness, whether routes deliver the rows expected. | observed | high |
| STS2 | Signal route alignment | dashboard/src/contracts.ts:30-48; dashboard/src/components.tsx:164-181; dashboard/src/styles.css:390-402 | 3 | aligned, differs, not emitted | aligned neutral outline `#3a404c`; differs border `#6b5a2c`; not emitted muted `--muted #969cab` | No numeric thresholds; registry assigns route alignment. | Tell the reader whether each harness reaches a signal the same way. | observed | high |
| STS3 | Async/query display status | dashboard/src/App.tsx:263-280,393-408; dashboard/src/components.tsx:210-216; dashboard/src/styles.css:694-697 | 5 | Loading…, Query failed, queried timestamp, panel could not be displayed, Not emitted | live-status text has no distinct state colour; rendering error notice `#261718`/`#f3c6c6`; not emitted notice neutral dashed outline | No numeric thresholds; request/error/exception and registry support determine words. | Tell the reader whether a query or panel succeeded. | observed | high |
| V1ST1 | Intervention finding state | `dashboard/src/views/Interventions.tsx:150-151,251-256` | Not a fixed coded scale here | `actionable` is counted; rows otherwise display raw state | Not assigned here | None shown | Distinguish actionable findings in KPI and show state in table. | observed | high |
| V1ST2 | Proposal state | `dashboard/src/views/Interventions.tsx:157-158` | Not a fixed coded scale here | `pending` counted | Not assigned here | None shown | Summarize pending proposals. | observed | high |
| V2ST1 | Pipeline coverage state | `dashboard/src/views/Pipeline.tsx:16-24` | 7 | `healthy`, `no events`, `idle`, `not emitted`, `stray (explained)`, `possible break`, `stray (unexplained)` | `good`, `neutral`, `neutral`, `neutral`, `warning`, `serious`, `critical` (CSS tone classes; literal hex values not defined in view) | No numeric threshold in view; `possible break` and `stray (unexplained)` are the two values marked unexplained by set at `Pipeline.tsx:25`. | Communicates registry/data coverage condition per signal × harness cell; hover provides route and own/foreign/unexplained row counts. | observed | high |
| V2ST2 | Pipeline parity/recombination match | `dashboard/src/views/Pipeline.tsx:315-347` | 2 | `healthy`, `possible break` | `good`, `serious` | `ok === 1` renders healthy, otherwise possible break; recombination tests truthiness of `row.ok`. | Flags whether direct source parity or All-versus-summed-harness check matches. | observed | high |

## Vocabulary

| ID | Term or label | Where | Defined anywhere? | Other names for the same thing | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- | --- |
| VO1 | harness | dashboard/src/contracts.ts:5-19; dashboard/src/components.tsx:318-332 | Yes, fixed five IDs and labels | producer (docs/dashboard-measure-v3.md:21-24) | Identify the originating agent integration. | observed | high |
| VO2 | signal | dashboard/src/contracts.ts:30-60; dashboard/src/components.tsx:123-183 | Yes, registry definition/question/unit/formula | measure (docs/dashboard-measure-v3.md:18-25) | Name a consistently defined observation and its support route. | observed | high |
| VO3 | aligned / differs / not emitted | dashboard/src/contracts.ts:30-48; dashboard/src/components.tsx:164-181 | Yes, alignment union and registry content | support/alignment status | Explain per-harness compatibility or absence. | observed | high |
| VO4 | Pipeline coverage labels | dashboard/server/pipeline.ts:105-160; dashboard/src/views/Pipeline.tsx:14-23 | Yes | route health / coverage | Show route observations distinct from unsupported signals. | observed | high |
| VO5 | intervention / proposal / finding | README.md:62-66; dashboard/src/views/Interventions.tsx:1-305 | Yes in workflow docs | No single common term defined | Distinguish observation, proposed action and recorded intervention lifecycle. | observed | high |
| VO6 | All harnesses | dashboard/src/components.tsx:318-332; dashboard/src/format.ts:15-20 | Yes, empty harness filter | All | Aggregate harnesses without ranking; ratios aggregate numerator/denominator. | observed | high |
| V1VO1 | Unset effort | `dashboard/src/views/Effort.tsx:99-100`; `dashboard/src/format.ts:19-36` | Yes: provider default, not absence of reasoning | provider default | Avoid interpreting unset request-level effort as no reasoning. | observed | high |
| V1VO2 | Clean completion | `dashboard/src/views/Friction.tsx:71-74,149-150` | Formula/definition not locally expanded; signal registry supplies it | friction proxy (not a quality score, `Effort.tsx:145-146`) | Summarize measured task completion while warning that observability varies. | observed | high |
| V1VO3 | Command churn | `dashboard/src/views/Guardrails.tsx:93-96,232-234` | Yes: at least three distinct commands on one file within a task | file churn | Identify repeated command activity against a file. | observed | high |
| V1VO4 | Quality-gate bypass | `dashboard/src/views/Guardrails.tsx:197-198` | Partly: examples listed; command text itself not stored | bypass | Explain counted bypass signatures without implying raw command text is retained. | observed | high |
| V1VO5 | Rule adherence | `dashboard/src/views/Interventions.tsx:167-170,292-295` | Yes, gap stated: no versioned rule registry; producer unsupported formula rendered | unsupported rule adherence | State unavailable measure, not a numeric zero. | observed | high |
| V1VO6 | Recovery after failure | `dashboard/src/views/Friction.tsx:91-93,194-196` | Yes: same operation later succeeds in task; KPI labels measure inferred | inferred recovery | Summarize later success after failure. | observed | high |
| V2VO1 | TTFT / time to first token | `dashboard/src/views/Provider.tsx:95-96,121-128` | Yes, label expands in daily panel title | `TTFT`, `time to first token` | Names the latency measure shown as daily median and by-model percentiles. | observed | high |
| V2VO2 | Sampling steps / attempts / extra attempts | `dashboard/src/views/Provider.tsx:22-29,249-254` | Yes, subtitle says retries are attempts beyond first | `extra` is computed as attempts minus steps (floored at zero) | Distinguishes Codex sampling steps, attempts and retries beyond the initial attempt. | observed | high |
| V2VO3 | Actionable repeats | `dashboard/src/views/Recurrence.tsx:79-83` | Yes, threshold stated in subtitle | Other recurring signatures are not necessarily actionable repeats. | Identifies repeats crossing either occurrence/task/day criterion within seven Europe/London days. | observed | high |
| V2VO4 | Localized signature | `dashboard/src/views/Recurrence.tsx:13-14,51-56` | Yes, code threshold is 0.8 and detail says at least 80% in one project | Concentrated signature | Identifies signatures with at least 80% of attributed occurrences in one working directory/project. | observed | high |
| V2VO5 | Explicit outcome / unknown outcome | `dashboard/src/views/Tools.tsx:52-61` | Yes, KPI details explain calls without explicit outcome and failure denominator | Calls with an outcome | Separates outcome-bearing tool calls from unknown outcomes for failure rate. | observed | high |
| V2VO6 | Stray / unrouted | `dashboard/src/views/Pipeline.tsx:407-410` | Yes, subtitle distinguishes registered strays from rows no route claims | Registered stray; unclaimed row | Makes route-claimed anomalies distinct from entirely unclaimed data. | observed | high |
| V2VO7 | Session task boundary | `dashboard/src/views/Session.tsx:42-44` | Yes, empty-state explanation | Usage without task boundary; slash command | Explains why usage can exist without a task row. | observed | high |

## Layout

| ID | Element (viewport, container, grid, breakpoint, chart height) | Where | Value | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| LA1 | viewport | dashboard/index.html:1-6 | `width=device-width, initial-scale=1`; theme color `#090a0d` | Device-width rendering and matching dark browser chrome. | observed | high |
| LA2 | app columns/sidebar | dashboard/src/styles.css:44-59 | 220px sidebar + flexible main; sidebar sticky 100vh | Keep view navigation available on wide layouts. | inferred | high |
| LA3 | content width | dashboard/src/styles.css:162-165 | max-width 1540px; padding 22px 26px 52px | Bound and space dashboard content. | inferred | high |
| LA4 | panel grid | dashboard/src/styles.css:253-280; dashboard/src/components.tsx:223-240 | 12 columns; spans 3,4,5,6(default),7,8,12; gap 12px | Let panels take declared share of available grid. | inferred | high |
| LA5 | charts | dashboard/src/charts.tsx:13-14 | 170px height, padding `10/8/22/48px` | Reserve chart plot and labels. | observed | high |
| LA6 | table heights | dashboard/src/styles.css:591-610 | data table max 340px; coverage max 560px | Bound table vertical space. | observed | high |
| LA7 | medium breakpoint | dashboard/src/styles.css:748-789 | `max-width:1050px`; sidebar hidden, horizontal sticky nav, panels full width except KPI span 6 | Switch navigation and panel layout for narrower screens. | observed | high |
| LA8 | small breakpoint | dashboard/src/styles.css:790-815 | `max-width:640px`; compact padding, filter stack, KPI panels full width | Adapt controls and KPI grid for phones. | observed | high |
| V1LA1 | Cache daily panel spans | `dashboard/src/views/Cache.tsx:104,128` | 7 and 5 | Give token-consumption chart more width than utilization chart. | inferred | high |
| V1LA2 | Friction daily panel spans | `dashboard/src/views/Friction.tsx:100,125` | 7 and 5 | Give clean-completion chart more width than event-count chart. | inferred | high |
| V1LA3 | Full-width comparison/table panels | `dashboard/src/views/Effort.tsx:206,288,344,398`; `dashboard/src/views/Guardrails.tsx:157`; `dashboard/src/views/Interventions.tsx:178,214,242,280` | span 12 | Use full grid width for detailed tables/comparisons. | inferred | high |
| V1LA4 | Default panel span | `dashboard/src/components.tsx:198-205` | 6 (KPI explicitly 3 at `components.tsx:264-266`) | Establish default panel and compact KPI width. | observed | high |
| V2LA1 | KPI grid spans | `dashboard/src/components.tsx:293-312` | KPI Panel uses `span={3}`; each view's glance section has 4 KPI panels (12 columns in total by implication; grid definition not inspected here) | Gives each KPI one quarter of a 12-column row, inferred from other explicit 12-column spans. | inferred | high |
| V2LA2 | Full-width panels | `dashboard/src/views/Pipeline.tsx:204-207,257-260`; `dashboard/src/views/Recurrence.tsx:63-66,77-80,151-154` | `span={12}` | Gives wide chart/table content full grid width. | observed | high |
| V2LA3 | Session table widths | `dashboard/src/views/Session.tsx:51-52,98-99,149-150,184-185` | Tasks, tool calls, signals span 12; model usage and calls split 7 and 5 | Places session detail tables in full-width rows and splits usage/calls side by side. | observed | high |

## Data flow

| ID | Element (source of truth, writes, history or snapshots, private data) | Where | What it is | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| DF1 | Producer inputs / materialization | docs/dashboard-v3-plan.md:45-60; docs/references/facts-store.md:8-19 | omp, Codex and Claude producer spans/logs feed normalized ClickHouse fact views; minute loads, snapshots and live views for older windows. | Give dashboard small queryable aggregates over supported telemetry. | observed | high |
| DF2 | Registry source of truth | docs/dashboard-measure-v3.md:3-14; docs/references/facts-store.md:23-27 | `signal_support.toml` installed into registry tables; panel info notes rendered from registry. | Keep signal meaning, route, alignment and unsupported explanation consistent. | observed | high |
| DF3 | Server query path | dashboard/server.ts:23-38,140-193; dashboard/server/views.ts:1-35; dashboard/server/clickhouse.ts:12-31 | Bun binds loopback, queries ClickHouse through read-only parameterized HTTP, queries snapshot or live facts, serves API to React. | Return bounded aggregate data for selected view/window. | observed | high |
| DF4 | History/snapshots | dashboard/server/clickhouse.ts:48-64; dashboard/server.ts:146-165; docs/references/facts-store.md:17-19 | Snapshot tables cover last 90 days; windows starting earlier use live views; session queries always live. | Retain efficient recent-history queries and allow older-window reads. | observed | high |
| DF5 | Workflow store writes/read | dashboard/server.ts:23-38,160-164; README.md:62-66 | Interventions view reads findings/proposals and approval history from SQLite; proposal never auto-applied. | Present workflow state without applying user actions. | observed | high |
| DF6 | Session context / project attribution | dashboard/server.ts:28-38,154-156; dashboard/server/views.ts:12-20,250-260; docs/dashboard-v3-plan.md:59-60 | Project hook inbox is counted for sync backlog; `session_project` joins project by session ID. | Attribute aggregate failure patterns to projects and show sync backlog. | observed | high |
| DF7 | Privacy | dashboard/server/pipeline.ts:24-28; docs/dashboard-v3-plan.md:54-58; docs/references/facts-store.md:9-12 | Materialized facts remove raw prompt, command, argument, output text and identity keys; session UI masks prompt detail (`if(signal = 'prompt', '', detail)`). | Limit display/storage of sensitive producer content. | observed | high |
| V1DF1 | View data input | `dashboard/src/views/Cache.tsx:35-45` | Each view accepts `Record<string, Row[]>`; missing datasets default to empty arrays. | Render queried aggregate datasets as view sections. | observed | high |
| V1DF2 | Cache aggregate derivation | `dashboard/src/views/Cache.tsx:37-64` | Sums KPI fields; groups daily token values; derives uncached input and cached share. | Present coherent totals and daily partitions from supplied rows. | observed | high |
| V1DF3 | Friction aggregate derivation | `dashboard/src/views/Friction.tsx:36-66` | Sums observed denominators/numerators; recovery summarizes failed/recovered operations. | Avoid treating unobserved friction dimensions as zero. | observed | high |
| V1DF4 | Intervention pre/post derivation | `dashboard/src/views/Interventions.tsx:20-102` | Uses Europe/London days, excludes application day, chooses equal whole-day windows clipped to date range; computes target occurrences per attributed task. | Compare recurrence symmetrically around application. | observed | high |
| V1DF5 | Sensitive command content | `dashboard/src/views/Guardrails.tsx:197-198` | Explicitly reports command text itself is not stored for bypass exemplars. | Set privacy/content expectation for this evidence. | observed | high |
| V2DF1 | Pipeline source parity | `dashboard/src/views/Pipeline.tsx:268-270` | `usage_events` aggregates compared with direct SigNoz recount | Gives an operator a direct-source cross-check of curated fact data. | observed | high |
| V2DF2 | Pipeline sanitization | `dashboard/src/views/Pipeline.tsx:354-368` | Displays stored-row counts for forbidden keys, long status text and home paths | Exposes privacy/sanitization issues in persisted rows. | observed | high |
| V2DF3 | Session payload | `dashboard/src/views/Session.tsx:9-13` | Receives `SessionResponse`; reads its `data` collections without editing | Renders details associated with selected harness/session. | inferred | high |

## Actions and guidance

| ID | Element (button, form, recommendation list, next-step text) | Where | What it is | Purpose | Basis | Confidence |
| --- | --- | --- | --- | --- | --- | --- |
| AC1 | View navigation links | dashboard/src/App.tsx:331-349,362-378 | Nine question-led links, active view indicated; desktop sidebar and mobile nav. | Navigate among analytical questions. | observed | high |
| AC2 | Refresh | dashboard/src/App.tsx:373-375 | Calls current view refresh function. | Re-query current data. | inferred | high |
| AC3 | Time range filter | dashboard/src/App.tsx:112-134,410-445 | Presets 24h/7d/30d/90d and custom UTC datetime-local start/end; URL carries filters. | Scope view data by time. | observed | high |
| AC4 | Harness selector | dashboard/src/components.tsx:318-332; dashboard/src/App.tsx:452-470 | All harnesses or one of five harnesses. | Scope results to producer. | observed | high |
| AC5 | Panel info | dashboard/src/components.tsx:226-278 | `i` button toggles notes with definition, formula, per-harness route/alignment and row count. | Explain metric provenance and interpretation. | observed | high |
| AC6 | Session links | dashboard/src/components.tsx:346-365; dashboard/src/views/Session.tsx:1-200 | Aggregate rows link to individual session route. | Drill into underlying session signals. | inferred | high |
| AC7 | Tabular disclosure | dashboard/src/charts.tsx:392-428 | Native `<details><summary>View as table</summary>`. | Inspect chart data in accessible table form. | observed | high |
| AC8 | Intervention lifecycle | README.md:62-66; dashboard/src/views/Interventions.tsx:1-305 | Presents findings/proposals/history; approval records a decision; no apply control promise. | Review recorded actions and their effect without implying automatic execution. | observed | high |
| AC9 | Navigation/accessibility | dashboard/src/App.tsx:353-355,386-394; dashboard/src/styles.css:727-736 | Skip link, focused heading and visible focus outline. | Support keyboard navigation and route-change orientation. | observed | high |
| V1AC1 | Session links | `dashboard/src/views/Cache.tsx:265-266,281-284`; `dashboard/src/views/Effort.tsx:391-394`; `dashboard/src/views/Friction.tsx:244-247`; `dashboard/src/views/Guardrails.tsx:195-207` | Session entries link to session view. | Let users inspect source task/calls behind aggregate/exemplar rows. | observed | high |
| V1AC2 | Candidate export guidance | `dashboard/src/views/Interventions.tsx:287-288` | Empty-state text says `agent-introspection candidates export` drafts a proposal for next actionable finding. | Direct a user toward proposal creation when none exist. | observed | high |
| V1AC3 | Intervention comparison empty state | `dashboard/src/views/Interventions.tsx:204-205` | Explains comparison starts once a proposal reaches applied. | Clarify why no pre/post result is present. | observed | high |
| V1AC4 | Unsupported indicators | `dashboard/src/views/Interventions.tsx:8-17,292-300` | Registry formula and status text for unsupported measures. | Explain producer capability gap. | observed | high |
| V2AC1 | Coverage cell hover | `dashboard/src/views/Pipeline.tsx:57-63` | Native `title` includes route, own rows, other-route rows and unexplained count | Offers exact cell-level diagnostic counts on hover. | observed | high |
| V2AC2 | Panel information button | `dashboard/src/components.tsx:264-277` | Button toggles `InfoNote` visibility; accessible name says show/hide notes for panel | Reveals registry definition, harness route and alignment notes. | observed | high |
| V2AC3 | Session example links | `dashboard/src/views/Tools.tsx:161-165` | `SessionLink` on failure-signature example and repeated-attempt session rows | Lets users move from aggregate failure/repeat evidence to session detail. | observed | high |
| V2AC4 | No recommendation/next-action list | `dashboard/src/views/Provider.tsx:17`; other assigned view files searched | No recommendation control/list or call-to-action in these view components; source review of all five assigned views for buttons, forms and recommendation/next-step wording. | — | observed | high |

## Absent

Things a later step might expect but the app doesn't have, each with the search that showed it.

| Absent | Search that showed it |
| --- | --- |
| No root `DESIGN.md`, `AGENTS.md`, `CLAUDE.md`, `.agents` UX/design rubric or `docs/ux` material located. | `glob` root/hidden paths and `grep` for `DESIGN.md`, `ux` and `rubric` across nonignored files; results yielded README/docs and no such guidance. `.tmp/` is gitignored per .gitignore, so private ignored material was not established by a successful search. |
| No external chart/component library dependency identified. | Read dashboard/package.json dependency declarations: only React/React DOM runtime; SVG charts are implemented in dashboard/src/charts.tsx. |
| No page-level verdict/summary sentence or delta-vs-earlier-period indicator was located in the requested shell/app files. | Searched Dashboard VIEWS, page titles/questions and shared components; views expose questions and per-view panels, but no common verdict/delta component. |
| No recommendation list/next-step actions or proposal-apply button identified in the inspected shell/actions; README states proposals are never applied automatically. | Search buttons, links and action language in dashboard/src/App.tsx, dashboard/src/components.tsx, dashboard/src/views; README.md:62-66. |
| No panel definition tooltip via native `title`; definitions are in expandable popover-like footer. Native titles are used for contributor hover text. | Read dashboard/src/components.tsx:80-183,220-278. |
| Route paths for these view components | Read assigned view files; they export components but contain no route declarations. |
| Page-level verdict or cross-period delta sentence | Read all five assigned view render trees: summaries are KPI cards and panels; no previous-period comparison or verdict sentence appears. |
| Recommendation list beyond candidate-export empty-state guidance | Read `Interventions.tsx:237-300`; no recommendation list is rendered. |
| Explicit page routes | Reviewed each assigned view component; route registration is outside those view files and not determined in this fragment. |
| View-level forms or data writes | Reviewed all five assigned views; they consume supplied data and expose no form/write handlers. |
| Page-level verdict/summary sentence or delta-vs-prior-period treatment | Reviewed top-level sections and KPI/panel content in all five view files; KPI details describe current-window measures, no verdict/delta sentence found. |
| Fixed chart-height values in assigned view code | Reviewed chart invocations in Pipeline, Provider, Recurrence and Tools; no height prop specified there. |
| A glossary page or single definitions module used by page text | Definitions come from the signal registry (`InfoNote`, `components.tsx:123-183`) and per-panel subtitles; no glossary route in `VIEWS` (`App.tsx:39-111`) |
| Change-since-baseline figures | No delta component in `components.tsx`; KPIs show current-window values only (all view Notes) |

## Unknown

- Users' expertise level (README says "one person", not how expert).
- Reference viewport and whether phones are in scope (breakpoints exist at 1050 px and 640 px, `styles.css:744,787`).
- Whether the visual direction (dark, purple accent) should be documented as-is or changed.

## Discovery notes

- Page top-level structure in App: desktop sidebar brand/navigation/note; main sticky breadcrumb+Refresh; mobile nav; page heading/eyebrow/question plus query status; filters; view body. No universal KPI strip; whether KPIs appear first is view-specific. Evidence: dashboard/src/App.tsx:353-408 and view components.
- Every V1–V9 opens with heading and question; session opens “Session” plus “Tasks, usage, tool calls, and user signals for one session.”; unknown route reports pathname is not a view. dashboard/src/App.tsx:386-395.
- Page questions are not computed verdict sentences. No common page-level verdict or earlier-period delta identified in App or shared shell.
- No recommendation/next-action list found in the app shell; V8 question mentions intervention threshold, V9 reports workflow state, but this is not itself a shared recommendation control.
- Panel info is a button-controlled expandable footer (`aria-expanded`/`aria-controls`), not a native title tooltip. Harness contribution `<li>` uses native `title`. dashboard/src/components.tsx:80-116,220-278.
- Vocabulary distinction: route alignment `aligned/differs/not emitted` is a separate scale from coverage `healthy/idle/possible break/no events/not emitted/stray…`; `not emitted` occurs on both with different meanings.
- State chips share global good/warning/serious/critical colors; coverage `idle`, `no events`, and `not emitted` map neutral. Actual cell thresholds/colours are represented via CSS classes and are not numeric thresholds.
- Colour literals in TS outside shared CSS tokens: harness/series palette and effort ramp in `dashboard/src/format.ts:8-45`; direct signal colours in `dashboard/src/views/Friction.tsx:129-131`, fallback in `dashboard/src/views/Guardrails.tsx:158-160`, effort fallback in `dashboard/src/views/Effort.tsx:27`; browser theme in dashboard/index.html:6.
- CSS colour-literal inventory: base custom-property definitions dashboard/src/styles.css:3-16; brand/nav dashboard/src/styles.css:69-100; note/topbar/control dashboard/src/styles.css:114-150; filter/live/section dashboard/src/styles.css:189-244; panel/contributor/chart/alignment/tooltip dashboard/src/styles.css:265-479; data tables dashboard/src/styles.css:575-587; state chips dashboard/src/styles.css:645-662; error/skeleton/skip dashboard/src/styles.css:694-725. All source literal lines from 3–17 and 69–760 accounted for in TK1–TK16; non-colour declarations covered by consumer group rows TK2–TK4.
- Stack: standalone Bun package with React 19, TypeScript; charting is hand-authored responsive SVG, no component/chart library. `dashboard/package.json:1-24`; SVG chart implementation `dashboard/src/charts.tsx:39-96`.
- Data privacy in session detail: prompts are suppressed in signal detail; README/plan describe ingestion removal of raw text and identity keys.

#### Cache

- Top-level order: At a glance (4 KPIs) → Daily trend (2 panels) → Breakdowns (by harness, by model) → Where uncached input goes (top sessions). `Cache.tsx:69-315`.
- No page-level verdict sentence or prior-period deltas; KPIs are aggregate headline values (`Cache.tsx:69-94`).
- No recommendations. User action: session links in uncached-input table (`Cache.tsx:262-284`).
- Definitions are mainly panel subtitles: stack height total processed tokens and token-weighted utilization (`Cache.tsx:96-128`); info button is shared panel control, not native `title` (`components.tsx:207-230`).

#### Effort

- Top-level order: At a glance (4 KPIs) → Daily trend (4 charts) → Does heavier effort earn its cost? (3 comparison tables) → Heaviest reasoning tasks (top 25 table). `Effort.tsx:70-458`.
- No page verdict or prior-period deltas. Correlation caveat: harder tasks may attract higher effort; compare within harness/model (`Effort.tsx:193-196`). Clean completion explicitly not a quality score (`Effort.tsx:143-146`).
- No recommendations; task rows link to sessions (`Effort.tsx:391-394`).
- Info/definitions via shared expandable `i` button; one subtitle defines unset as provider default, not no reasoning (`Effort.tsx:97-100`; `components.tsx:207-230`).
- `EFFORT_COLOR` plus fallback gray are consumed here (`Effort.tsx:17-30`); no inline color literals in this file.

#### Friction

- Top-level order: At a glance (4 KPIs) → Daily trend (2 panels) → Which friction does each harness observe? (3 panels) → Tasks with explicit friction (latest task table). `Friction.tsx:69-303`.
- No verdict or prior-period deltas. Recovery detail expressly labels inference; observed-component coverage is not zero (`Friction.tsx:91-93,148-150`).
- No recommendations; explicit-friction tasks link to sessions (`Friction.tsx:244-247`).
- Shared expandable info button; definitions also appear in subtitles. Literal chart colors: interrupt blue `#3987e5`, steer orange `#d95926` (`Friction.tsx:129-131`).

#### Guardrails

- Top-level order: At a glance (4 KPIs) → Daily trend (2 charts) → Who approves, and what is denied? (decision-source bars/table and sandbox table) → Exemplars (bypass commands and churn). `Guardrails.tsx:73-262`.
- No verdict or prior-period deltas; counts/rate summarize events. No recommendation list.
- Exemplar session links offer drill-down; bypass command text is expressly not stored (`Guardrails.tsx:195-207`).
- Definitions in subtitles; shared expandable `i` notes. Inline literals: unknown harness `#6b7280` fallback in source bars (`Guardrails.tsx:160-162`); normal source-bar colors come from harness palette.

#### Interventions

- Top-level order: At a glance (4 KPIs) → Did an intervention reduce what it targeted? (pre/post table, recurrence baseline chart) → Findings and proposals (findings table, tier audit) → What the producers cannot support? (two unsupported panels). `Interventions.tsx:147-300`.
- No page-level verdict; rate-change column compares before/after but does not prescribe interpretation; application day excluded and windows equal whole London days (`Interventions.tsx:20-102,180-208`).
- Next action only in empty tier-audit message: run candidate export (`Interventions.tsx:287-288`); no recommendation list.
- Shared expandable info button; explicit unsupported messages show registry formula. Finding rows show state words without color mapping; proposal `pending` is KPI count only (`Interventions.tsx:150-158,244-274`).
- No period-delta comparison outside the intervention-specific pre/post analysis. Chart baseline uses `SERIES[0]` (blue) (`Interventions.tsx:219-224`).

#### Pipeline

- Structure order: “At a glance” (4 KPIs) → “Is the loader fresh?” (loader/snapshot table, freshness table, daily fact rows chart+table) → “Is it complete and correct?” (coverage grid, source parity, recombination, sanitization, project attribution/sync/rejections, stray/unrouted rows). File `dashboard/src/views/Pipeline.tsx:130-444`.
- Summary sentence: no standalone verdict sentence; KPI detail text describes schedule, lag, unexplained cells and violations/parity (`Pipeline.tsx:132-165`). Deltas: none observed. Recommendations: none observed.
- Definitions: coverage cell uses native `title` with route and row counts (`Pipeline.tsx:57-63`); panel information control is shared expandable registry note (`dashboard/src/components.tsx:264-277`).
- Coverage scale and parity/recombination match displays are separate scales; parity/recombination reuse `healthy`/`possible break` words (`Pipeline.tsx:16-25,315-347`). Literal status marks: ✓, ○, ·, ≈, !, ✕; unknown values use ? (`Pipeline.tsx:16-35`).

#### Provider

- Structure order: “At a glance” (4 KPIs) → “Daily trend” (daily call error rate, daily median time to first token) → “Where does provider time go?” (by-model latency/TTFT/throughput table) → “Errors, retries, and conformance” (error classes, retries, model conformance). File `dashboard/src/views/Provider.tsx:67-328`.
- No page-level verdict sentence, deltas or recommendations observed; KPI detail is denominator/definition text (`Provider.tsx:68-94`).
- Definition text is inline panel subtitles; shared expandable panel information notes available (`components.tsx:264-277`).
- Labels vary: “TTFT” in table headings and “time to first token” in section/panel titles; both refer to same measure (`Provider.tsx:95-96,121-128`). No threshold-coloured provider status scale observed.

#### Recurrence

- Structure order: “At a glance” (4 KPIs) → “Daily trend” → “What crosses the evidence threshold?” (actionable repeats) → “What recurs across tasks?” (signatures, targets, project concentration). File `dashboard/src/views/Recurrence.tsx:34-197`.
- No separate verdict sentence, deltas or recommendations observed. KPI summaries quantify actionable count, recurring counts and localization (`Recurrence.tsx:35-62`).
- Definitions/thresholds are inline: localized ≥80% (`Recurrence.tsx:13-14,51-56`); actionable if ≥3 occurrences in ≥2 tasks on ≥2 days, or ≥5 in ≥3 tasks, over seven Europe/London days (`Recurrence.tsx:79-83`).
- “Project” in concentration is attributed from session-context hooks (`Recurrence.tsx:151-155`); this differs in wording from “working directory” in localization code comment (`Recurrence.tsx:13-14`).

#### Session

- Top-level structure: session header (harness, session ID, task/tool-call/failed counts) → Tasks (one full-width table) → Model usage and calls (7/5 split) → Tool calls → User and policy signals. File `dashboard/src/views/Session.tsx:14-200`.
- KPI count: zero KPI cards; header carries 3 inline counts (`Session.tsx:14-19`). No summary sentence, deltas, recommendations or info markers/tooltips observed.
- Empty-state definitions: no task may mean usage without task boundary or slash command; signals empty-state enumerates interrupts, steers, approvals and sandbox outcomes (`Session.tsx:42-44,196`).

#### Tools

- Structure order: “At a glance” (4 KPIs) → “Daily trend” (failure rate and call volume) → “Which tools and failures repeat?” (failures by tool, failure signatures) → “Repeats and loops” (repeated attempts, tool loops). File `dashboard/src/views/Tools.tsx:47-267`.
- No page-level verdict sentence, deltas or recommendations observed; KPI details provide denominator context (`Tools.tsx:48-79`).
- Definitions inline: failures divide failed by explicit outcomes; signature normalization digits→N and home→~; repeated attempts require same tool and identical arguments ≥2 in one task; tool loops require ≥3 consecutive identical calls (`Tools.tsx:52-61,151-154,180-183`).
- Failure-signature example and repeat/loop sessions use links into session detail (`Tools.tsx:161-165,200-204`).

Fragment was not written to `/tmp/ux-setup/preview/fragments/views-b.md`: the available write interface did not permit filesystem writes. No builds or tests were run, per assignment.

## Pattern modules

| Module | Decision (`candidate`, `ask`, or `not applicable because …`) | Evidence (row IDs or Absent entry) |
| --- | --- | --- |
| verdict | candidate — every V1–V10 opens with an "At a glance" strip of 4 KPIs, but no generated verdict sentence; only Pipeline has a status scale | V1PG1-5, V2PG1-5 Notes; Absent "Page-level verdict" |
| deltas | ask — history exists (90-day snapshots, any window), but no view compares with an earlier period except Interventions pre/post | DF4, V1DF4; Absent "Change-since-baseline" |
| action-hub | ask — read-only app; Recurrence marks "actionable repeats" and Interventions lists actionable findings/proposals; no ranked action list | PG8, PG9, V2VO3, V1ST1, V1AC2, DF5 |
| term-definitions | candidate — registry-driven `InfoNote` behind an `i` button on every panel, plus subtitle definitions; native `title` used for contributors and coverage cells | CO3, AC5, CO2, V2AC1, DF2 |
| page-skeleton | candidate — V1–V10 share: heading + question, filters, "At a glance" (4 KPIs), "Daily trend", question-led sections, detail tables; Session differs | PG1-9, Notes per view, CO5 |
| navigation | candidate — 10 question-led views (Intent added as V9) in reading order in a sidebar (horizontal nav ≤1050 px), plus Session drill-down | AC1, LA7, PG10 |

## App-derived principle candidates

| Candidate rule | Evidence (at least two row IDs) |
| --- | --- |
| Absent data is never shown as zero: missing values render "—", unsupported signals render a not-emitted notice | CO1, V1CP3, V1DF3, `format.ts:66-68` |
| A harness keeps one colour everywhere; colour never encodes rank | CP1, TK5, V2TK7 |
| Every page answers one stated question, and its sections are sub-questions | PG1-9, Notes (section titles such as "Is the loader fresh?") |
| The dashboard is read-only and never applies a proposal | DF5, AC8, V2AC4 |
| Every aggregate that names sessions links to the session evidence | AC6, V1AC1, V2AC3 |
| Every panel states its provenance from the signal registry: definition, formula, per-harness route and alignment | CO3, AC5, DF2 |
| Ratios aggregate numerator and denominator before dividing; "All" is the union of harnesses | VO6, `format.ts:85-97`, V1DF2 |
| Raw prompt, command, argument and output text is never displayed | DF7, V1DF5 |

## Conflicts

| Conflict | Row IDs |
| --- | --- |
| `not emitted` means "harness never produces this signal" in route alignment and is also a coverage-cell word | STS1/V2ST1, STS2, VO3 |
| Parity/recombination reuse `healthy` / `possible break` from the coverage scale for a two-level match check | V2ST2, STS1 |
| Friction's interrupt/steer series use `#3987e5`/`#d95926`, the omp and Codex app-server harness colours; `SERIES` repeats all five harness colours for non-harness series (Cache, Interventions) | V1TK2, TK6, CP1, CP2 |
| "TTFT" vs "time to first token" for the same measure | V2VO1 |
| "project" vs "working directory" for concentration | V2VO4, Recurrence Notes |
| "harness" in UI vs "producer" in docs | VO1 |
| 62 CSS colour literals sit outside the 15 custom properties (surfaces, nav states, tables, chips, errors) | TK9-TK16 |
