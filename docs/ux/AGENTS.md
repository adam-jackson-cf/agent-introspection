# UI and design work

This file is the only place design and UX instructions live for agents. Read the standards below in order,
going only as deep as the task needs:

1. [`principles.md`](principles.md): what a page must do: the core, this app's pattern modules and its own
   principles. Always read this.
2. [`DESIGN.md`](DESIGN.md): how it looks (tokens, palettes, components, layout). Read it for any visual
   change, and lint it after editing: `npx @google/design.md lint docs/ux/DESIGN.md`.
3. [`dashboard/src/feature-audit.titles.json`](../../dashboard/src/feature-audit.titles.json): every guarded
   panel, figure and section title, and the decided changes that may remove one. Read it before you remove or
   rename a title.

Grade a changed page with the rubric in [`rubrics/ux-page.yaml`](rubrics/ux-page.yaml). If this file ever
disagrees with those standards, the standards win: fix this file.

## Where things live

| What                                             | Path                                                        |
| ------------------------------------------------ | ----------------------------------------------------------- |
| Pages                                            | `dashboard/src/views/`                                      |
| Shared components                                | `dashboard/src/components.tsx`, `dashboard/src/charts.tsx`  |
| Token mirror (values)                            | `dashboard/src/tokens.css`                                  |
| Token readers for code                           | `dashboard/src/tokens.ts`                                   |
| Chart palettes                                   | `dashboard/src/format.ts`                                   |
| Numbers and logic                                | `dashboard/server/views.ts`, `dashboard/server/pipeline.ts` |
| Guarded titles and decided changes               | `dashboard/src/feature-audit.titles.json`                   |
| Review screenshots and score files (git-ignored) | `docs/ux/reviews/captures/`                                 |

## Conventions

- **`DESIGN.md` decides values.** Change a value in `DESIGN.md` first, then mirror it in `tokens.css`.
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
- **Titles don't disappear.** A guarded title may move to another page or component. To remove one, the user
  must agree a decided change first: add it to `decisions` in the titles file as `DCn`, then set the title's
  `removed` to that ID. Each edit to the titles file needs an entry in
  [golden changes](../references/golden-changes.md).
- Add a new expert term to the definitions source first, then mark it in page text.
- New pages use the shared page layout and keep its order.
- Every page is listed in navigation, in its job's group.

## Enforce

Enforced today: `dashboard/src/feature-audit.test.ts` fails when a title in
`dashboard/src/feature-audit.titles.json` is no longer rendered, or when its `removed` names an ID missing from
`decisions`. It runs in the T1 tier of `bash scripts/run-ci-quality-gates.sh`, so every commit and CI run
includes it.

Candidate checks, not yet enforced: no colour literals outside the token mirror (`git grep -nE` with the
rubric's `colour_literal_pattern` over `dashboard/src`); no calculations in pages; every marked term exists in
the signal registry; every page is listed in navigation. Add one as a `check_<name>` function in
`scripts/run-ci-quality-gates.sh` with a claim in [`proofs.toml`](../../proofs.toml) that names its
enforcement points and canaries; an instruction here enforces nothing.
