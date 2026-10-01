# UI and design work

This file is the only place design and UX instructions live for agents. Read the standards below in order,
going only as deep as the task needs:

1. [`principles.md`](principles.md): what a page must do: the core, this app's pattern modules and its own
   principles. Always read this.
2. [`DESIGN.md`](DESIGN.md): how it looks (tokens, palettes, components, layout). Read it for any visual
   change, and lint it after editing: `npx @google/design.md lint docs/ux/DESIGN.md`.
3. [`discovery.md`](discovery.md): what each existing element is for. Read it before changing or removing one.
4. The newest dated files in `reviews/`, when any exist: current scores, the open backlog and the feature
   inventory. Every inventory row must still exist after your change; record its new home.

Grade a changed page with the rubric in [`rubrics/ux-page.yaml`](rubrics/ux-page.yaml). If this file ever
disagrees with those standards, the standards win: fix this file. The decisions behind them are in
[`app-brief.md`](app-brief.md).

## Where things live

| What | Path |
| --- | --- |
| Pages | `dashboard/src/views/` |
| Shared components | `dashboard/src/components.tsx`, `dashboard/src/charts.tsx` |
| Token mirror (values) | `dashboard/src/tokens.css` |
| Token readers for code | `dashboard/src/tokens.ts` |
| Chart palettes | `dashboard/src/format.ts` |
| Numbers and logic | `dashboard/server/views.ts`, `dashboard/server/pipeline.ts` |
| Review screenshots and score files (git-ignored) | `docs/ux/reviews/captures/` |

## Conventions

- **Colour, type and spacing from tokens only.** No colour literals, font names or pixel spacing in pages,
  components or chart code. CSS reads custom properties; code reads the token module.
- **States keep their scale.** Each status scale keeps its words, order and colours from `DESIGN.md`. Never add,
  merge or rename a level in page code.
- **Palettes keep their role.** Categorical colours for unordered groups, ordinal ramps in order for ordered
  levels, `other` for residual groups.
- **Numbers come from the data source.** Pages format results; they never compute business logic, states or
  thresholds.
- **Look at it.** Screenshot every page you change at the reference viewport, save it under the screenshot
  folder, and open the image before finishing.
- **Wording stays.** Keep every label, state word and term as it is; restyle, don't reword.
- Add a new expert term to the definitions source first, then mark it in page text.
- New pages use the shared page layout and keep its order.
- Every page is listed in navigation, in its job's group.

## Enforce

Candidate checks for CI (set up with the `setup-repository-guardrails` skill): no colour literals outside the
token mirror (`colour_usage.py --allow <mirror>` from `setup-ux-standards`); no calculations in pages.
Every marked term exists in the signal registry; every page is listed in navigation.
