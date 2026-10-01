# UX principles: Agent Introspection dashboard

The rules for every page in `dashboard/src/views/`. They come from [`discovery.md`](discovery.md) and the app
brief in [`app-brief.md`](app-brief.md) (2026-09-30). The visual system lives in [`DESIGN.md`](DESIGN.md).
Pages are graded with the `ux-page` rubric ([`rubrics/ux-page.yaml`](rubrics/ux-page.yaml)).

The principles have three parts: a **core** that applies to every app (P0–P6), **pattern modules** this app
adopted because it has that concept (M1, M2, …), and **app principles** derived from how this app already
works (A1, A2, …).

## Who it's for and what it's for

- **Users:** one person, the operator of their own coding agents (omp, Codex app-server, Codex CLI, Codex exec
  and Claude Code), fluent in agent, token, cache and telemetry vocabulary.
- **Main job:** explore model usage and agent process across every harness, and find problems and
  improvements. Each view answers one question, from "Is the fact loader fresh?" to "Did applied
  interventions reduce what they targeted?"
- **Second job:** track findings and intervention proposals with their approval history. The dashboard never
  applies a proposal.

## Core

### P0. Start zoomed out, drill on demand (overrides everything else)

Every page opens on its answer: the thing a reader came to that page to learn. Depth is always one deliberate
step away, never in the way.

| Level | What it holds | How you get there |
| --- | --- | --- |
| **L1: glance** | The page's answer, in the form it takes on this page: a state, a few figures, a short list or one chart. Visible without scrolling at the fold budget. | Opening the page |
| **L2: explain** | The chart, table, drivers or comparison behind the answer. | In place, next to what it explains: an expander, tab or popover. The L1 context stays visible. |
| **L3: investigate** | Raw data, method, assumptions and history. | Its own page or view, linked from the L2 element it deepens. |

Rules:

1. A page that needs scrolling before it shows its answer has failed P0.
2. Each L2 block sits next to the L1 element it explains, not in a long scroll away from it.
3. An L3 page opens on its own answer too. Zooming in resets to zoomed out.
4. Nothing that isn't L1 content is open by default.

### P1. Every number explains itself

A figure is finished only when a reader can answer *what, in what units, over what period, as of when, from
where*:

1. **Label and unit** on the figure.
2. **Basis:** state any adjustment (per item or total, gross or net, sampled or complete) once per page, and
   label any exception.
3. **Period:** "last 30 days", "this run", "since the first trace".
4. **Freshness:** an as-of time for synced or external data. Stale or missing inputs are flagged next to the
   number they affect.
5. **Source:** the dataset, query or function behind it, at L2.
6. **One formatter per kind:** one function each for durations, counts, percentages, money and dates; the same
   precision for the same measure; tabular numerals; aligned decimals in tables.
7. **Uncertainty is shown.** An estimate shows its range or sample size, never a bare point at L1.

### P2. Keep the load low

- **One primary visual per L1 zone.** Supporting charts go to L2.
- **L1 reading budget:** labels of four words or fewer, and no paragraphs.
- **Group, don't stack.** Related controls and figures sit together.
- **Controls sit beside what they change,** and their current value is visible at L1.
- **Say it once.** A number or explanation appears once per level.
- **Words and marks carry meaning, not emoji.** Use the status colours with their words and the icons in
  `DESIGN.md`.
- **Language:** Terms of art are fine where the users know them; use plain words for everything else.

### P3. Consistent everywhere

- The same thing always looks, reads and behaves the same: figures, states, chips, series colours, number
  formats and vocabulary.
- One status scale means one set of words and colours on every page. Pipeline coverage uses healthy, no
  events, idle, not emitted, stray (explained), possible break and stray (unexplained), in that order. The
  parity and recombination checks use healthy and possible break from the same scale. Route alignment uses
  aligned, differs and not emitted, in that order.
- Build pages from the shared components in `dashboard/src/components.tsx` and `dashboard/src/charts.tsx`, and
  the tokens in the mirror named in `docs/ux/AGENTS.md`.
- Colour literals in pages and components are defects; colours come from the token mirror.

### P4. The data source owns the logic

- Every number, state and threshold comes from the SQL views in `dashboard/server/views.ts` and
  `dashboard/server/pipeline.ts`, over the ClickHouse facts and the signal registry. The UI formats results;
  it never computes business logic, states or thresholds.
- A calculation found in UI code is a defect to move, not a pattern to copy.

### P5. Privacy and control

- Raw prompt, command, argument and output text never reaches the dashboard: materialisation removes it, and
  the Session view masks prompt detail. No page may show it, even in a tooltip or table cell.
- Screenshots for reviews go to `docs/ux/reviews/captures/`, which git ignores.
- Decisions belong to the user. The UI proposes and explains, and never acts irreversibly without
  confirmation.

### P6. Built for a 2560 × 1440 window, on any monitor

- **The content column starts at the navigation.** It sits directly right of the sidebar, separated only by
  `page-x` padding, and never floats in the middle of the window. Spare width goes to the right of the
  content, never between the sidebar and the content.
- **Adapt up to a cap.** Content fills the space beside the sidebar up to 2560px (half a 5120 × 1440
  ultrawide), in **bands** of one logical group each. Smaller monitors get the same layout, narrower; wider
  windows leave empty space on the right.
- **Height is the scarce axis.** L1 plus the start of the first L2 band fit in 1330 px. Prefer wide, short
  charts 280–360 px high, and side-by-side comparisons over stacked ones.
- **Prose keeps a readable measure.** Paragraphs, callouts and lists are capped at about 80 characters a line
  *within their column*. The layout gets wide, but text blocks don't.
- **Side-by-side for two or three options; a table for more.**
- Below 1024 px wide the layout stacks to one column, with L1 still first. Phones are out of scope.

## Pattern modules

### M1. Every expert term has a definition one step away

- An expert term at L1 has an info marker. Hover, focus or tap shows a one-line plain definition instantly,
  in a styled popover rather than a native `title` tooltip.
- Definitions come from the signal registry, which the panel info note (`InfoNote` in
  `dashboard/src/components.tsx`) renders: definition, formula, and each harness's route and alignment. The
  note opens as an overlay anchored to the panel's `i` marker, so it never pushes the chart or table down.
- A page never writes its own definition of a term.

### M2. Pages share one structure

- Every page follows the same order: the page header with its question, the filters, an "At a glance" strip
  of headline figures, a "Daily trend" band, then bands titled as sub-questions, ending with detail tables.
  The Session view is exempt: it opens on its header counts, then its tables.
- The structure is built once in the shared layout, never per page.

### M3. Navigation follows the jobs

- Pages are grouped by the job they serve, and the order in each group follows the main job's flow.
- Related pages link to each other at the point of need. A warning links to where it can be investigated.
- Every link says where it goes: "Open run details", not "More".

## App principles

### A1. Absent is never zero

- A missing value renders "—", and a signal a harness doesn't produce renders the not-emitted notice. Neither
  is ever drawn as 0 in a figure, bar or line.
- Charts show gaps where days have no data; they don't interpolate across them.

**Evidence:** CO1, V1CP3, V1DF3, CO8.

### A2. A harness keeps one colour everywhere

- Each harness has one categorical colour on every page, chart, legend and chip. Colour never encodes rank.
- Series that aren't harnesses use their own palette, so a colour never means two things on one screen.

**Evidence:** CP1, TK5, V1TK2 (the conflict this resolves).

### A3. Every page answers one stated question

- The page header states the question; each band below it is a sub-question the band answers.

**Evidence:** PG1–PG9, PG11, V1/V2 Notes.

### A4. Read-only

- No page writes data or applies a proposal. Approval is recorded in the CLI workflow, never from the
  dashboard.

**Evidence:** DF5, AC8, V2AC4.

### A5. Aggregates link to their sessions

- Any row that names a session links to the Session view for it.

**Evidence:** AC6, V1AC1, V2AC3.

### A6. Every panel states its provenance

- Each panel's info note shows the registry definition, formula, and each harness's route and alignment, and
  the panel shows which harnesses contributed rows.

**Evidence:** CO2, CO3, AC5, DF2.

### A7. Ratios aggregate before dividing

- A rate sums its numerator and denominator across rows first, then divides. "All harnesses" is the union of
  the harnesses, never an average of their rates.

**Evidence:** VO6, V1DF2, `dashboard/src/format.ts:85-97`.

### A8. Each page leads to its conclusion

- A reader can go from the page's question to an answer without leaving the page: the headline figures
  answer it at a glance, and the bands below show why.
- Each panel's subtitle says how to read it: what the value measures, its denominator, and which direction
  is worse when direction has a meaning.
- Comparisons the question asks for sit side by side on a shared scale and unit.
- A caveat (inferred, correlation not cause, partial observability) sits next to the figure it qualifies,
  not in a separate note.

**Evidence:** PG1–PG9, PG11 (question per view), V1VO2 and Effort Notes (correlation caveat), V1VO6 (inferred
recovery), V2VO3 (thresholds in subtitles).

### A9. Language stays as it is

- Labels, state words and terms on the site are settled. Standards work changes layout, styling, type and data
  viz, not wording. New text uses the words already in use for the same thing.

**Evidence:** brief answers to Q10–Q15.

## Not adopted

- **verdict:** not applicable because views are question-led explorations with no page-level status scale
  except Pipeline; the headline figures stay under M2.
- **deltas:** not applicable because the time-range filter already lets the user compare any historical
  window.
- **action-hub:** not applicable because the dashboard is read-only and next actions happen in the CLI
  workflow.
