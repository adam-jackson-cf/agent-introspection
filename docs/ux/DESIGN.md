---
version: alpha
name: Instrument Panel
description: >
  The visual standard for the Agent Introspection dashboard. UX rules live in docs/ux/principles.md; this file owns how things look.
colors:
  primary: "#8b5cf6"
  ink: "#090a0d"
  surface: "#13161c"
  raised: "#171a20"
  rule: "#292d37"
  text: "#f2f3f5"
  text-2: "#c3c7d0"
  muted: "#969cab"
  axis: "#3a404c"
  selection: "#211a33"
  primary-soft: "#a78bfa"
  status-healthy: "#0ca30c"
  status-no-events: "#969cab"
  status-idle: "#969cab"
  status-not-emitted: "#969cab"
  status-stray-explained: "#fab219"
  status-possible-break: "#ec835a"
  status-stray-unexplained: "#ef5350"
  status-alignment-aligned: "#c3c7d0"
  status-alignment-differs: "#d9a441"
  status-alignment-not-emitted: "#969cab"
  cat-1: "#3987e5"
  cat-2: "#d95926"
  cat-3: "#199e70"
  cat-4: "#c98500"
  cat-5: "#d55181"
  cat-6: "#9ce9f0"
  cat-7: "#e8d7a5"
  cat-8: "#dbade0"
  seq-effort-1: "#184f95"
  seq-effort-2: "#2a78d6"
  seq-effort-3: "#6da7ec"
  seq-effort-4: "#b7d3f6"
  effort-mixed: "#9085e9"
  other: "#6b7280"
typography:
  page-title:
    fontFamily: Inter
    fontSize: 1.625rem
    fontWeight: 700
    lineHeight: 1.2
    letterSpacing: -0.02em
  band-title:
    fontFamily: Inter
    fontSize: 1.0625rem
    fontWeight: 600
    lineHeight: 1.3
  panel-title:
    fontFamily: Inter
    fontSize: 0.9375rem
    fontWeight: 600
    lineHeight: 1.35
  figure-lg:
    fontFamily: Inter
    fontSize: 1.875rem
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: -0.02em
    fontFeature: '"tnum" 1, "lnum" 1'
  body-md:
    fontFamily: Inter
    fontSize: 0.875rem
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: Inter
    fontSize: 0.8125rem
    fontWeight: 500
    lineHeight: 1.4
    fontFeature: '"tnum" 1, "lnum" 1'
  caption:
    fontFamily: Inter
    fontSize: 0.75rem
    fontWeight: 400
    lineHeight: 1.45
    fontFeature: '"tnum" 1, "lnum" 1'
  mono:
    fontFamily: ui-monospace
    fontSize: 0.75rem
    fontWeight: 400
    lineHeight: 1.45
rounded:
  sm: 4px
  md: 8px
  lg: 10px
  pill: 999px
spacing:
  unit: 4px
  xs: 4px
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
  gutter: 16px
  page-x: 32px
  band-gap: 32px
  measure: 80ch
  content-max: 2560px
components:
  page:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.text}"
    typography: "{typography.body-md}"
    padding: "{spacing.page-x}"
  page-header:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.text}"
    typography: "{typography.page-title}"
  page-question:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.text-2}"
    typography: "{typography.body-md}"
  band-title:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.text}"
    typography: "{typography.band-title}"
  band-rule:
    backgroundColor: "{colors.rule}"
    height: 1px
  panel:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-2}"
    rounded: "{rounded.lg}"
    padding: 16px
  panel-title:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.panel-title}"
  panel-subtitle:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.caption}"
  figure:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
    typography: "{typography.figure-lg}"
    padding: 16px
  figure-label:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
  popover:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.text}"
    typography: "{typography.caption}"
    rounded: "{rounded.md}"
    padding: 12px
  term-marker:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary-soft}"
    typography: "{typography.caption}"
  link:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.primary-soft}"
    typography: "{typography.label}"
  session-id:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.primary-soft}"
    typography: "{typography.mono}"
  nav-item-active:
    backgroundColor: "{colors.selection}"
    textColor: "{colors.text}"
    rounded: "{rounded.md}"
  nav-accent:
    backgroundColor: "{colors.primary}"
    width: 2px
  filter-control:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.text}"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: 8px 12px
  table-header:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    typography: "{typography.caption}"
  table-cell:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text-2}"
    typography: "{typography.label}"
  chip-status-healthy:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-healthy}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-status-no-events:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-no-events}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-status-idle:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-idle}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-status-not-emitted:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-not-emitted}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-status-stray-explained:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-stray-explained}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-status-possible-break:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-possible-break}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-status-stray-unexplained:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.status-stray-unexplained}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-alignment-aligned:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.status-alignment-aligned}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-alignment-differs:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.status-alignment-differs}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  chip-alignment-not-emitted:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.status-alignment-not-emitted}"
    typography: "{typography.caption}"
    rounded: "{rounded.pill}"
    padding: 2px 8px
  callout-error:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.status-stray-unexplained}"
    rounded: "{rounded.md}"
    padding: 12px 16px
  not-emitted-notice:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.muted}"
    rounded: "{rounded.md}"
    padding: 12px
  chart-reference-line:
    backgroundColor: "{colors.axis}"
    height: 1px
  chart-series-cat-1:
    backgroundColor: "{colors.cat-1}"
    height: 2px
  chart-series-cat-2:
    backgroundColor: "{colors.cat-2}"
    height: 2px
  chart-series-cat-3:
    backgroundColor: "{colors.cat-3}"
    height: 2px
  chart-series-cat-4:
    backgroundColor: "{colors.cat-4}"
    height: 2px
  chart-series-cat-5:
    backgroundColor: "{colors.cat-5}"
    height: 2px
  chart-series-cat-6:
    backgroundColor: "{colors.cat-6}"
    height: 2px
  chart-series-cat-7:
    backgroundColor: "{colors.cat-7}"
    height: 2px
  chart-series-cat-8:
    backgroundColor: "{colors.cat-8}"
    height: 2px
  chart-series-seq-effort-1:
    backgroundColor: "{colors.seq-effort-1}"
    height: 2px
  chart-series-seq-effort-2:
    backgroundColor: "{colors.seq-effort-2}"
    height: 2px
  chart-series-seq-effort-3:
    backgroundColor: "{colors.seq-effort-3}"
    height: 2px
  chart-series-seq-effort-4:
    backgroundColor: "{colors.seq-effort-4}"
    height: 2px
  chart-series-effort-mixed:
    backgroundColor: "{colors.effort-mixed}"
    height: 2px
  chart-series-other:
    backgroundColor: "{colors.other}"
    height: 2px
---

# Instrument Panel

## Overview

A dark, quiet instrument panel for one expert reading telemetry: near-black `ink`, one step of `surface` for
panels and one of `raised` for overlays, with violet kept for marks, links and focus. Inter carries everything
at readable sizes, with tabular figures in every number. The data is the only colourful thing on the page:
harness colours are saturated and fixed, and everything else steps back so a trend or a break is the first
thing the eye finds.

## Colors

- **ink `#090a0d`**: the page and the sidebar. **surface `#13161c`**: panels. **raised `#171a20`**: popovers,
  filter controls, alignment chips and hover states. Depth comes from these steps, not from shadows or
  gradients.
- **rule `#292d37`**: hairlines between bands and table rows, panel borders, and chart gridlines.
- **axis `#3a404c`**: neutral marks that aren't text: chart baselines, crosshairs and reference lines.
- **text `#f2f3f5`**, **text-2 `#c3c7d0`**, **muted `#969cab`**: primary, secondary and supporting text.
  Muted is the lowest allowed for any text; it passes 4.5:1 on every background.
- **primary `#8b5cf6`** is the identity colour, for marks only: focus rings, the selected state and
  highlights. It's never used for body text. **selection `#211a33`** marks the active navigation item.
- **primary-soft `#a78bfa`** is for links, session IDs and the info `i` marker.

### Status

Status colours always appear with their word and are never decorative. Each passes 4.5:1 on every background.

- **Pipeline coverage** (`dashboard/src/views/Pipeline.tsx:14-22`, states assigned in
  `dashboard/server/pipeline.ts`): healthy (routes produce the expected rows), no events and idle (nothing to
  expect in the window), not emitted (the harness never produces this signal), stray (explained), possible
  break (an active harness should have rows but the route has none), stray (unexplained). Each state keeps its
  mark: ✓, ○, ○, ·, ≈, !, ✕. The parity and recombination checks (`Pipeline.tsx:315-347`) use healthy and
  possible break from this scale.
- **Route alignment** (`dashboard/src/contracts.ts:30-48`): aligned, differs, not emitted, shown as pills in
  the panel info note.

### Charts

- **Harness identity, categorical `cat-1`…`cat-5`**: omp, Codex app-server, Codex CLI, Codex exec, Claude Code,
  defined as `HARNESS_COLOR` in `dashboard/src/format.ts:8-14` and passed to charts as `Series.color`. Colour
  follows the harness, never its rank.
- **Parts of a total, categorical `cat-6`…`cat-8`**: series that aren't harnesses, such as uncached, cached
  and output tokens (`dashboard/src/views/Cache.tsx:57-61`), interrupt and steer
  (`dashboard/src/views/Friction.tsx:129-132`), and single-line trends (`dashboard/src/views/Interventions.tsx:219-223`).
  They are pale so they never read as a harness. Today these series reuse the harness colours through `SERIES`
  (`dashboard/src/format.ts:23`) and literals.
- **Reasoning effort, ordinal `seq-effort-1`…`seq-effort-4`**: low, medium, high, xhigh, dark to light blue
  (`EFFORT_COLOR`, `dashboard/src/format.ts:38-45`). **effort-mixed** marks tasks with more than one effort
  level and sits off the ramp. `seq-effort-1` (low) is below 3:1 on every background, so effort series always
  carry a direct label or legend entry, and bars are ordered low → xhigh.
- **Neutral `other`**: unset effort (provider default) and any unknown harness, instead of the `#6b7280`
  literals in `Effort.tsx:27` and `Guardrails.tsx:160`.

A chart colour means the same thing on every page. Categorical colours pass 3:1 on the chart background and
stay distinct from each other. Ordinal ramps keep their lightness order, and a step below 3:1 carries a
non-colour cue. The neutral `other` colour stays low in saturation and distinct from every categorical colour.

Charts sit on `surface`. Gridlines are `rule`, tick labels are `caption` in `muted`, lines are 2px, and every
mark has a hover and focus readout in a `popover`. Daily-trend charts are 280px high (up from 170px), so tick
labels and small differences stay readable.

## Typography

One family: **Inter**, kept from the existing app. It has true tabular lining figures, a large x-height that
holds at the dense sizes an expert dashboard needs, and needs no second face. Session IDs use the system
monospace, because they are identifiers a reader compares character by character.

The scale is one step larger than today: body text moves from 13px to 14px, and no text is smaller than 12px
(today table headers are 9px and chart ticks, contributors and captions 10–11px). Labels and table headers
use sentence case, not tracked capitals.

| Token         | Use                                                                      |
| ------------- | ------------------------------------------------------------------------ |
| `page-title`  | The view title                                                           |
| `band-title`  | Band headings, the sub-questions                                         |
| `panel-title` | Panel titles                                                             |
| `figure-lg`   | Headline figures in "At a glance"                                        |
| `body-md`     | The page question, notices and body text                                 |
| `label`       | Figure labels, legends, filter labels, table cells, links                |
| `caption`     | Panel subtitles, table headers, chart ticks, chips, popovers, info notes |
| `mono`        | Session IDs                                                              |

Numbers always use tabular lining figures (`tnum`, `lnum`), so columns align. Don't highlight one word in a
sentence with colour or italics.

## Layout

The reference window is **2560 × 1440** (a 1440p monitor, or half a 5120 × 1440 ultrawide), desktop only. The
layout must also work on smaller monitors, down to 1024px wide, and on wider windows.

- **Left-anchored content.** The content column starts immediately right of the 220px sidebar, with only
  `page-x` padding between them. It is never centred: any width beyond `content-max` stays empty on the
  right. (Today `.content` is centred with `margin: auto` at `max-width: 1540px`, which opens a gap between
  the sidebar and the content on wide windows, `dashboard/src/styles.css:156-160`.)
- **Width cap `content-max` (2560px).** Below the cap, content fills the space beside the sidebar. Content
  runs in **bands** separated by `band-gap` and a hairline `rule`.
- **A 12-column grid** with a `gutter`. Typical spans: primary 8 + drivers 4, two comparisons at 6 each, or
  three at 4 each.
- **Prose is capped at `measure` (80ch)** inside whatever column it sits in, including the page question.
- **Fold budget at 1330 px** (1440 minus menu bar and browser chrome): the header and L1 take ≤ 30% of it.
  Charts are 280–360px high and as wide as their span.
- **Below 1024px** every span becomes 12, in source order: L1, then L2 bands.

Alignment: everything is left-aligned. Numbers are right-aligned in tables.

The sidebar stays at 220px. The "At a glance" strip is four figures across the full width, each a `figure`
with its `figure-label` and one line of detail.

## Elevation & Depth

Flat: depth comes from the background steps and hairline rules. Panels are flat `surface` with a `rule`
border (no gradient). Only popovers and chart readouts float, on `raised` with a soft shadow.

## Shapes

The radius follows the hierarchy: `sm` for inputs and bars, `md` for callouts, popovers, buttons and nav items,
`lg` for panels, and `pill` for chips.

## Components

- **Page.** `ink` background, `text` colour, `body-md` type and `page-x` side padding. The header holds the
  view title in `page-title` and its question in `body-md` `text-2`.
- **Bands.** Each band opens with its sub-question in `band-title`, over a 1px `rule`.
- **Panel.** A `surface` container with a `rule` border and `lg` radius: title in `panel-title`, a subtitle in
  `caption` `muted` that says how to read it, the contributing harnesses, then the chart or table.
- **Figures.** A `figure-label`, then the value in `figure-lg` with tabular figures, then one caption line of
  detail.
- **Info marker and popover.** An `i` in `primary-soft` in the panel header opens the registry note as a
  `popover` anchored to it, over the content rather than pushing it down. Contributor details and coverage-cell
  details use the same popover, never a native `title` attribute. Opens on hover, focus and tap; closes on
  Escape.
- **Links.** `primary-soft`; the text says the destination. Session IDs are links in `mono`.
- **Navigation.** The active item uses `selection` with a 2px `primary` accent. Icons are monochrome glyphs,
  not emoji.
- **Focus.** A 2px `primary` ring on every focusable element.
- **Filters.** Controls on `raised`, `label` type, sentence-case labels above them.
- **Tables.** Headers in `caption` `muted`, sentence case; cells in `label` `text-2`; numbers right-aligned;
  `rule` row dividers; sticky header.
- **Status chip.** Pill, caption size, status-coloured mark and word. Alignment pills sit on `raised`.
- **Callout.** Query errors only, on `raised` with a 3px `status-stray-unexplained` left edge.
- **Not-emitted notice.** A dashed `axis` outline on `surface`, `muted` text.
- **Charts.** Transparent background, `rule` gridlines, `muted` ticks, `axis` reference lines, and hover on
  every mark.

## Do's and Don'ts

- **Do** cap prose at 80ch, however wide the column.
- **Do** use tabular figures and one formatter per kind for every number.
- **Don't** use emoji as status or navigation icons.
- **Don't** use status colours decoratively or without their word.
- **Don't** hard-code colours in pages, components or chart code. Read tokens from the mirror, which follows this
  file.
- **Do** keep every label and state word as it is; restyle, don't reword.
- **Don't** set any text below 12px, or in tracked all-caps.
- **Don't** give a non-harness series a harness colour.
