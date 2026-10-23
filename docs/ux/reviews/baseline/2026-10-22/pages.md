# Pages: archetypes and blueprints pack: dashboard v4 style: project agreed: 2026-10-22

| Page              | Pack      | Archetype       | Unit of rows          | Opens on                        | Like-minded with                                                                              | Nav position | Route only |
| ----------------- | --------- | --------------- | --------------------- | ------------------------------- | --------------------------------------------------------------------------------------------- | ------------ | ---------- |
| pipeline          | dashboard | overview-map    | pipeline check        | pipeline-check-map              | tools, recurrence (inferred)                                                                  | 1            | no         |
| pipeline-coverage | dashboard | overview-map    | signal × harness cell | pipeline-coverage-coverage-grid | tools, recurrence (inferred)                                                                  |              | yes        |
| pipeline-checks   | dashboard | comparison      | harness, fact measure | -                               | tools, recurrence (inferred)                                                                  |              | yes        |
| provider          | dashboard | overview-map    | model call            | provider-kpi-call-error-rate    | tools, recurrence (inferred)                                                                  | 2            | no         |
| cache             | dashboard | comparison      | token                 | cache-kpi-cache-utilization     | tools, recurrence (inferred)                                                                  | 3            | no         |
| effort            | dashboard | comparison      | effort level          | effort-outcomes-by-effort       | tools, recurrence (inferred)                                                                  | 4            | no         |
| tools             | dashboard | ranked-worklist | tool, signature       | tools-failures-by-tool          | pipeline, pipeline-coverage, provider, cache, effort, friction, guardrails, intent (inferred) | 5            | no         |
| friction          | dashboard | comparison      | task                  | friction-kpi-clean-completion   | tools, recurrence (inferred)                                                                  | 6            | no         |
| guardrails        | dashboard | comparison      | tool call, command    | guardrails-kpi-rejected         | tools, recurrence (inferred)                                                                  | 7            | no         |
| recurrence        | dashboard | ranked-worklist | failure cluster       | recurrence-actionable-repeats   | pipeline, pipeline-coverage, provider, cache, effort, friction, guardrails, intent (inferred) | 8            | no         |
| intent            | dashboard | comparison      | labelled task         | intent-task-type-mix            | tools, recurrence (inferred)                                                                  | 9            | no         |
| glossary          | dashboard | reference       | signal                | glossary-entries                | tools, recurrence (inferred)                                                                  | 10           | no         |
| session           | dashboard | reference       | session               | session-tasks                   | tools, recurrence (inferred)                                                                  |              | yes        |

Notes:

- pipeline: Pipeline (is the fact loader fresh, complete and correct for every harness; route /pipeline?range=7)
- pipeline-coverage: Coverage grid (which signal × harness cells are not healthy, and why; route /pipeline/coverage?range=7)
- pipeline-checks: Pipeline checks (proposed, not built) (do fact counts match the raw source, and is anything unsanitized; route /pipeline/checks (not built))
- provider: Provider (are model calls failing, slow, or retried; route /provider?range=7)
- cache: Cache efficiency (where tokens are consumed and how much input the cache serves; route /cache?range=7)
- effort: Reasoning effort (does heavier reasoning effort earn its cost; route /effort?range=7)
- tools: Tool failures (which tools fail, how often, in which tasks, and whether they repeat; route /tools?range=7)
- friction: Friction (how often users interrupt, steer or follow up, and whether tasks finish cleanly; route /friction?range=7)
- guardrails: Verification and guardrails (how often approvals block calls and gates are bypassed; route /guardrails?range=7)
- recurrence: Recurring failures (which failures recur and cross the intervention threshold; route /recurrence?range=7)
- intent: Requests and corrections (what users ask for and how often the next prompt corrects the agent; route /intent?range=7)
- glossary: Glossary (one definition per signal and how each harness reaches it; route /glossary)
- session: Session (one session's tasks, model usage, tool calls and signals; route /session?range=7&harness=claude-code)

## Gaps

- cache: unit of rows 'token' is a measure; the comparison archetype compares subjects (harness, model); closest archetype comparison, accepted at the Step 4 STOP or changed
- friction, guardrails, intent: the share question's unit (task, tool call, labelled task) is the counted thing, not a compared subject; closest archetype comparison
- provider: a status question over model calls; the overview-map rows would be models and columns failure, latency and retry factors; closest archetype overview-map

## Pruned

- pipeline-coverage-by-view (on Pipeline): restates pipeline-coverage-coverage-by-view on Coverage grid at the same zoom level; keep the Coverage grid one, the Pipeline Coverage KPI links to it
- provider-kpi-retries and provider-retries: glance figure and detail table of one measure; keep both only if the table answers Q2.14 by class
- interventions (Findings and fixes, route /interventions): page dropped by the user at the Step 2 STOP; Q5.1 moves to recurrence (target signal), Q5.2 goes with the page (confirmed at the Step 4 STOP), Q5.3 and Q5.4 dropped
- `<page>`-timings (10 rows, every page but glossary and session): orphan; the timing half of the badge answers no question (keep 'Data as of', which answers Q1.1)

## Target signals

- pipeline: pipeline-check-map primary-visual chip (question Q1.1; one status chip per harness × check, laid out as the overview map); data available (the five checks per harness exist in the pipeline view)
- pipeline: pipeline-health-trend figure trend-line (question Q1.8; trend-dot intended, see the skill parser note in the run log); derivable, computed_by dashboard/server/pipeline.ts (previous-window check results)
- pipeline-coverage: pipeline-coverage-stray-unrouted-lists figure table (question Q1.7); data available, today rendered on Pipeline
- recurrence: recurrence-post-intervention-recurrence figure side-by-side-bars (question Q5.1); data available (intervene.post_recurrence), today on Findings and fixes

- cache: cache-by-harness figure side-by-side-bars (question Q2.3); current form table, derived from build; form proposal from the Step 5 form check
- cache: cache-by-model figure side-by-side-bars (question Q2.3); current form table, derived from build; form proposal from the Step 5 form check
- cache: cache-daily-tokens figure trend-line (question Q2.2); current form day-bars, derived from build; form proposal from the Step 5 form check
- cache: cache-kpi-cache-utilization primary-visual share-bar (question Q2.1); current form figure, derived from build; form proposal from the Step 5 form check
- cache: cache-kpi-input-tokens figure share-bar (question Q2.1); current form figure, derived from build; form proposal from the Step 5 form check
- cache: cache-kpi-output-tokens figure share-bar (question Q2.1); current form figure, derived from build; form proposal from the Step 5 form check
- cache: cache-kpi-uncached-input figure share-bar (question Q2.1); current form figure, derived from build; form proposal from the Step 5 form check
- cache: cache-top-uncached-sessions figure ranked-bars (question Q2.4); current form table, derived from build; form proposal from the Step 5 form check
- effort: effort-clean-by-effort figure side-by-side-bars (question Q2.8); current form dot-whisker, derived from build; form proposal from the Step 5 form check
- effort: effort-daily-effort-mix figure trend-line (question Q2.7); current form day-bars, derived from build; form proposal from the Step 5 form check
- effort: effort-daily-reasoning figure trend-line (question Q2.7); current form day-bars, derived from build; form proposal from the Step 5 form check
- effort: effort-duration-by-effort figure side-by-side-bars (question Q2.8); current form dot-whisker, derived from build; form proposal from the Step 5 form check
- effort: effort-kpi-heavy-share figure share-bar (question Q2.6); current form figure, derived from build; form proposal from the Step 5 form check
- effort: effort-kpi-reasoning-share figure share-bar (question Q2.6); current form figure, derived from build; form proposal from the Step 5 form check
- effort: effort-kpi-reasoning-tokens figure share-bar (question Q2.6); current form figure, derived from build; form proposal from the Step 5 form check
- effort: effort-kpi-tasks figure share-bar (question Q2.6); current form figure, derived from build; form proposal from the Step 5 form check
- effort: effort-model-by-effort figure side-by-side-bars (question Q2.9); current form table, derived from build; form proposal from the Step 5 form check
- effort: effort-outcomes-by-effort primary-visual side-by-side-bars (question Q2.5); current form table, derived from build; form proposal from the Step 5 form check
- effort: effort-spend-by-effort figure side-by-side-bars (question Q2.5); current form table, derived from build; form proposal from the Step 5 form check
- effort: effort-top-reasoning-tasks figure ranked-bars (question Q2.10); current form table, derived from build; form proposal from the Step 5 form check
- friction: friction-components-by-harness figure side-by-side-bars (question Q3.7); current form table, derived from build; form proposal from the Step 5 form check
- friction: friction-failing-operations figure ranked-bars (question Q3.8); current form table, derived from build; form proposal from the Step 5 form check
- friction: friction-failure-classes figure ranked-bars (question Q3.8); current form table, derived from build; form proposal from the Step 5 form check
- friction: friction-kpi-clean-completion primary-visual share-bar (question Q3.5); current form figure, derived from build; form proposal from the Step 5 form check
- friction: friction-kpi-interrupted figure share-bar (question Q3.5); current form figure, derived from build; form proposal from the Step 5 form check
- friction: friction-kpi-quick-follow-up figure share-bar (question Q3.5); current form figure, derived from build; form proposal from the Step 5 form check
- friction: friction-kpi-recovery figure side-by-side-bars (question Q3.7); current form figure, derived from build; form proposal from the Step 5 form check
- friction: friction-recovery-by-harness figure side-by-side-bars (question Q3.7); current form table, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-approvals-by-source figure share-bar (question Q3.13); current form bar-list, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-failures-caught figure chip (question Q3.12); current form table, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-kpi-approvals figure share-bar (question Q3.10); current form figure, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-kpi-command-churn figure share-bar (question Q3.10); current form figure, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-kpi-gate-bypass figure share-bar (question Q3.10); current form figure, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-kpi-rejected primary-visual share-bar (question Q3.10); current form figure, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-verification-by-kind figure chip (question Q3.12); current form bar-list, derived from build; form proposal from the Step 5 form check
- guardrails: guardrails-verification-runs figure chip (question Q3.12); current form figure, derived from build; form proposal from the Step 5 form check
- intent: intent-by-harness figure share-bar (question Q4.6); current form table, derived from build; form proposal from the Step 5 form check
- intent: intent-correction-kinds figure share-bar (question Q4.6); current form bar-list, derived from build; form proposal from the Step 5 form check
- intent: intent-effort-payoff figure side-by-side-bars (question Q4.7); current form table, derived from build; form proposal from the Step 5 form check
- intent: intent-kpi-corrected figure share-bar (question Q4.4); current form figure, derived from build; form proposal from the Step 5 form check
- intent: intent-kpi-frustrated figure share-bar (question Q4.6); current form figure, derived from build; form proposal from the Step 5 form check
- intent: intent-kpi-labelled figure share-bar (question Q4.4); current form figure, derived from build; form proposal from the Step 5 form check
- intent: intent-task-type-mix primary-visual share-bar (question Q4.4); current form bar-list, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-all-equals-sum figure side-by-side-bars (question Q1.3); current form table, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-check-map primary-visual chip (question Q1.1); current form matrix, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-coverage-by-view figure matrix (question Q1.4); current form table, derived from build; form proposal from the Step 5 form check
- pipeline-coverage: pipeline-coverage-coverage-by-view figure matrix (question Q1.4); current form table, derived from build; form proposal from the Step 5 form check
- pipeline-coverage: pipeline-coverage-coverage-grid primary-visual matrix (question Q1.4); current form table, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-excluded-signals figure table (question Q1.7); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-fact-rows-per-day figure trend-line (question Q1.6); current form day-bars, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-freshness-by-harness figure matrix (question Q1.2); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-health-trend figure trend-line (question Q1.8); current form trend-dot, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-hidden-signals figure table (question Q1.7); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-hook-spool figure matrix (question Q1.2); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-kpi-coverage figure chip (question Q1.1); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-kpi-loaders figure chip (question Q1.1); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-kpi-max-lag figure chip (question Q1.1); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-kpi-parity figure chip (question Q1.1); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-kpi-sanitization figure chip (question Q1.1); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-loader-refresh figure matrix (question Q1.2); current form figure, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-sanitization figure chip (question Q1.5); current form table, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-source-parity figure side-by-side-bars (question Q1.3); current form table, derived from build; form proposal from the Step 5 form check
- pipeline: pipeline-stray-unrouted figure table (question Q1.7); current form figure, derived from build; form proposal from the Step 5 form check
- provider: provider-errors-by-class figure matrix (question Q2.14); current form table, derived from build; form proposal from the Step 5 form check
- provider: provider-kpi-call-error-rate primary-visual chip (question Q2.11); current form figure, derived from build; form proposal from the Step 5 form check
- provider: provider-kpi-model-calls figure chip (question Q2.11); current form figure, derived from build; form proposal from the Step 5 form check
- provider: provider-kpi-retries figure chip (question Q2.11); current form figure, derived from build; form proposal from the Step 5 form check
- provider: provider-kpi-unknown-outcomes figure chip (question Q2.11); current form figure, derived from build; form proposal from the Step 5 form check
- provider: provider-latency-by-model figure matrix (question Q2.13); current form table, derived from build; form proposal from the Step 5 form check
- provider: provider-retries figure matrix (question Q2.14); current form table, derived from build; form proposal from the Step 5 form check
- recurrence: recurrence-actionable-repeats primary-visual ranked-bars (question Q4.1); current form table, derived from build; form proposal from the Step 5 form check
- recurrence: recurrence-daily-classed-failures figure trend-line (question Q4.3); current form day-bars, derived from build; form proposal from the Step 5 form check
- recurrence: recurrence-failure-clusters figure ranked-bars (question Q4.2); current form table, derived from build; form proposal from the Step 5 form check
- recurrence: recurrence-kpi-actionable figure ranked-bars (question Q4.1); current form figure, derived from build; form proposal from the Step 5 form check
- recurrence: recurrence-kpi-clusters figure ranked-bars (question Q4.2); current form figure, derived from build; form proposal from the Step 5 form check
- recurrence: recurrence-kpi-targets figure ranked-bars (question Q4.2); current form figure, derived from build; form proposal from the Step 5 form check
- recurrence: recurrence-recurring-targets figure ranked-bars (question Q4.2); current form table, derived from build; form proposal from the Step 5 form check
- tools: tools-daily-tool-calls figure trend-line (question Q3.3); current form day-bars, derived from build; form proposal from the Step 5 form check
- tools: tools-failure-signatures figure ranked-bars (question Q3.1); current form table, derived from build; form proposal from the Step 5 form check
- tools: tools-failures-by-tool primary-visual ranked-bars (question Q3.1); current form table, derived from build; form proposal from the Step 5 form check
- tools: tools-kpi-failure-rate figure share-bar (question Q3.2); current form figure, derived from build; form proposal from the Step 5 form check
- tools: tools-kpi-repeats figure share-bar (question Q3.2); current form figure, derived from build; form proposal from the Step 5 form check
- tools: tools-kpi-tasks-affected figure share-bar (question Q3.2); current form figure, derived from build; form proposal from the Step 5 form check
- tools: tools-kpi-tool-calls figure share-bar (question Q3.2); current form figure, derived from build; form proposal from the Step 5 form check
- tools: tools-repeated-calls figure ranked-bars (question Q3.4); current form table, derived from build; form proposal from the Step 5 form check

## Parked

- none

## pipeline (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-loaders       ] [kpi-max-lag       ] [kpi-coverage      ] [kpi-parity        ] [kpi-sanitization  ]             |
+-- is-the-loader-fresh -----------------------------------------------------------------------------------------------+
| [loader-refresh                                       ] [hook-spool                                           ]      |
| [freshness-by-harness                                 ]                                                              |
+-- is-it-complete-and-correct ----------------------------------------------------------------------------------------+
| [fact-rows-per-day                                                       ] [coverage-by-view                  ]      |
| [source-parity                                        ] [all-equals-sum                                       ]      |
| [sanitization                                         ]                                                              |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [pipeline-kpi-coverage, pipeline-kpi-loaders, pipeline-kpi-max-lag, pipeline-kpi-parity, pipeline-kpi-sanitization], is-the-loader-fresh [pipeline-freshness-by-harness, pipeline-hook-spool, pipeline-loader-refresh], is-it-complete-and-correct [pipeline-all-equals-sum, pipeline-coverage-by-view, pipeline-fact-rows-per-day, pipeline-sanitization, pipeline-source-parity]
Views: default, filter-pipeline-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against overview-map: Mismatch, redesign: the archetype opens on a harness × check matrix of status marks with a verdict; the page opens on five KPI cards and a table-heavy body (three tables of 5-16 rows), with no verdict sentence. The map (pipeline-check-map) is a target signal.

| Source element                  | Target page       | Filters set                         | count_source              | count_target                    |
| ------------------------------- | ----------------- | ----------------------------------- | ------------------------- | ------------------------------- |
| pipeline-kpi-loaders-links      | pipeline          | none                                | pipeline-kpi-loaders      | pipeline-loader-refresh         |
| pipeline-kpi-coverage-links     | pipeline-coverage | none (keeps the current time range) | pipeline-kpi-coverage     | pipeline-coverage-coverage-grid |
| pipeline-kpi-parity-links       | pipeline          | none                                | pipeline-kpi-parity       | pipeline-source-parity          |
| pipeline-kpi-sanitization-links | pipeline          | none                                | pipeline-kpi-sanitization | pipeline-sanitization           |
| pipeline-coverage-by-view-links | pipeline-coverage | none (keeps the current time range) | pipeline-coverage-by-view | pipeline-coverage-coverage-grid |

## pipeline-coverage (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- coverage-summary --------------------------------------------------------------------------------------------------+
| [coverage-by-view                                                                                              ]     |
+-- every-cell --------------------------------------------------------------------------------------------------------+
| [coverage-grid                                                                                                 ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, coverage-summary [pipeline-coverage-coverage-by-view], every-cell [pipeline-coverage-coverage-grid]
Views: default, filter-pipeline-coverage-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against overview-map: Partial match: the grid is the overview-map matrix, but rows are signals that grow with the catalogue (archetype anti-pattern) and the summary table repeats Pipeline's Coverage by view; no verdict or next zone.

| Source element                        | Target page | Filters set                         | count_source | count_target |
| ------------------------------------- | ----------- | ----------------------------------- | ------------ | ------------ |
| pipeline-coverage-coverage-grid-links | pipeline    | none (keeps the current time range) | -            | -            |

## provider (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-model-calls         ] [kpi-call-error-rate     ] [kpi-retries             ] [kpi-unknown-outcomes    ]          |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-error-rate                                     ] [daily-ttft                                           ]      |
+-- where-does-provider-time-go ---------------------------------------------------------------------------------------+
| [latency-by-model                                                                                              ]     |
+-- errors-and-retries ------------------------------------------------------------------------------------------------+
| [errors-by-class                                      ] [retries                                              ]      |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [provider-kpi-call-error-rate, provider-kpi-model-calls, provider-kpi-retries, provider-kpi-unknown-outcomes], daily-trend [provider-daily-error-rate, provider-daily-ttft], where-does-provider-time-go [provider-latency-by-model], errors-and-retries [provider-errors-by-class, provider-retries]
Views: default, filter-provider-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against overview-map: Mismatch, redesign: the status question needs a model × factor map; the page opens on four KPI cards, then daily charts and tables; no verdict.

| Source element                 | Target page | Filters set                         | count_source | count_target |
| ------------------------------ | ----------- | ----------------------------------- | ------------ | ------------ |
| provider-errors-by-class-links | friction    | none (keeps the current time range) | -            | -            |

## cache (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-input-tokens        ] [kpi-cache-utilization   ] [kpi-uncached-input      ] [kpi-output-tokens       ]          |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-tokens                                                  ] [daily-cache-utilization                    ]       |
+-- breakdowns --------------------------------------------------------------------------------------------------------+
| [by-harness                                                                                                    ]     |
| [by-model                                                                                                      ]     |
+-- where-uncached-input-goes -----------------------------------------------------------------------------------------+
| [top-uncached-sessions                                                                                         ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [cache-kpi-cache-utilization, cache-kpi-input-tokens, cache-kpi-output-tokens, cache-kpi-uncached-input], daily-trend [cache-daily-cache-utilization, cache-daily-tokens], breakdowns [cache-by-harness, cache-by-model], where-uncached-input-goes [cache-top-uncached-sessions]
Views: default, filter-cache-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against comparison: Mismatch, redesign: the share question needs a share bar per harness or model on one scale; the page opens on KPI cards and daily charts, and the harness and model comparison sits in two tables lower down. The unit 'token' is a measure, not a compared subject; the subjects are harness and model.

| Source element                    | Target page | Filters set                        | count_source | count_target |
| --------------------------------- | ----------- | ---------------------------------- | ------------ | ------------ |
| cache-top-uncached-sessions-links | session     | none (item page: harness, session) | -            | -            |

## effort (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-reasoning-tokens    ] [kpi-reasoning-share     ] [kpi-tasks               ] [kpi-heavy-share         ]          |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-reasoning                                      ] [daily-effort-mix                                     ]      |
+-- is-heavier-effort-cleaner-or-slower -------------------------------------------------------------------------------+
| [clean-by-effort                                      ] [duration-by-effort                                   ]      |
+-- does-heavier-effort-earn-its-cost ---------------------------------------------------------------------------------+
| [outcomes-by-effort                                                                                            ]     |
| [spend-by-effort                                                                                               ]     |
| [model-by-effort                                                                                               ]     |
+-- heaviest-reasoning-tasks ------------------------------------------------------------------------------------------+
| [top-reasoning-tasks                                                                                           ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [effort-kpi-heavy-share, effort-kpi-reasoning-share, effort-kpi-reasoning-tokens, effort-kpi-tasks], daily-trend [effort-daily-effort-mix, effort-daily-reasoning], is-heavier-effort-cleaner-or-slower [effort-clean-by-effort, effort-duration-by-effort], does-heavier-effort-earn-its-cost [effort-model-by-effort, effort-outcomes-by-effort, effort-spend-by-effort], heaviest-reasoning-tasks [effort-top-reasoning-tasks]
Views: default, filter-effort-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against comparison: Partial match: Task outcomes by reasoning effort answers the compare question, but as a table, below KPI cards and two daily charts; the contrast zone should be side-by-side bars on one scale, opening the page.

| Source element                   | Target page | Filters set                        | count_source | count_target |
| -------------------------------- | ----------- | ---------------------------------- | ------------ | ------------ |
| effort-top-reasoning-tasks-links | session     | none (item page: harness, session) | -            | -            |

## tools (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-tool-calls          ] [kpi-failure-rate        ] [kpi-tasks-affected      ] [kpi-repeats             ]          |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-failure-rate                                            ] [daily-tool-calls                           ]       |
+-- which-tools-and-failures-repeat -----------------------------------------------------------------------------------+
| [failures-by-tool                                     ] [failure-signatures                                   ]      |
+-- repeated-calls ----------------------------------------------------------------------------------------------------+
| [repeated-calls                                                                                                ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [tools-kpi-failure-rate, tools-kpi-repeats, tools-kpi-tasks-affected, tools-kpi-tool-calls], daily-trend [tools-daily-failure-rate, tools-daily-tool-calls], which-tools-and-failures-repeat [tools-failure-signatures, tools-failures-by-tool], repeated-calls [tools-repeated-calls]
Views: default, filter-tools-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against ranked-worklist: Partial match: Failures by tool is the ranking, but as a table below KPI cards and daily charts; the ranking zone (sorted bars, leader first) should open the page, the worklist below it.

| Source element                 | Target page | Filters set                         | count_source | count_target |
| ------------------------------ | ----------- | ----------------------------------- | ------------ | ------------ |
| tools-failure-signatures-links | session     | none (item page: harness, session)  | -            | -            |
| tools-failure-signatures-links | friction    | none (keeps the current time range) | -            | -            |
| tools-failure-signatures-links | recurrence  | none (keeps the current time range) | -            | -            |
| tools-repeated-calls-links     | session     | none (item page: harness, session)  | -            | -            |
| tools-repeated-calls-links     | friction    | none (keeps the current time range) | -            | -            |
| tools-repeated-calls-links     | recurrence  | none (keeps the current time range) | -            | -            |

## friction (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-clean-completion    ] [kpi-interrupted         ] [kpi-quick-follow-up     ] [kpi-recovery            ]          |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-clean                                                   ] [daily-interrupt-steer                      ]       |
+-- which-friction-does-each-harness-observe --------------------------------------------------------------------------+
| [components-by-harness                                                                                         ]     |
| [recovery-by-harness                                  ] [failing-operations                                   ]      |
+-- tasks-with-explicit-friction --------------------------------------------------------------------------------------+
| [failure-classes                                                                                               ]     |
| [latest-friction-tasks                                                                                         ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [friction-kpi-clean-completion, friction-kpi-interrupted, friction-kpi-quick-follow-up, friction-kpi-recovery], daily-trend [friction-daily-clean, friction-daily-interrupt-steer], which-friction-does-each-harness-observe [friction-components-by-harness, friction-failing-operations, friction-recovery-by-harness], tasks-with-explicit-friction [friction-failure-classes, friction-latest-friction-tasks]
Views: default, filter-friction-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against comparison: Mismatch, redesign: the share question needs a share bar per outcome; the page opens on KPI cards and two daily charts, and the harness comparison is a wide table.

| Source element                       | Target page | Filters set                        | count_source | count_target |
| ------------------------------------ | ----------- | ---------------------------------- | ------------ | ------------ |
| friction-latest-friction-tasks-links | session     | none (item page: harness, session) | -            | -            |

## guardrails (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-rejected            ] [kpi-approvals           ] [kpi-gate-bypass         ] [kpi-command-churn       ]          |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-rejection-rate                                                                                          ]     |
+-- does-the-agent-verify-its-work ------------------------------------------------------------------------------------+
| [verification-runs       ] [verification-by-kind                       ] [failures-caught                   ]        |
+-- who-approves ------------------------------------------------------------------------------------------------------+
| [approvals-by-source                                                                                           ]     |
+-- exemplars ---------------------------------------------------------------------------------------------------------+
| [bypass-commands                                      ] [command-churn                                        ]      |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [guardrails-kpi-approvals, guardrails-kpi-command-churn, guardrails-kpi-gate-bypass, guardrails-kpi-rejected], daily-trend [guardrails-daily-rejection-rate], does-the-agent-verify-its-work [guardrails-failures-caught, guardrails-verification-by-kind, guardrails-verification-runs], who-approves [guardrails-approvals-by-source], exemplars [guardrails-bypass-commands, guardrails-command-churn]
Views: default, filter-guardrails-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against comparison: Mismatch, redesign: the share question needs rejection and bypass shares on one scale; the page opens on KPI cards, then a daily chart and verification tables.

| Source element                   | Target page | Filters set                         | count_source | count_target |
| -------------------------------- | ----------- | ----------------------------------- | ------------ | ------------ |
| guardrails-bypass-commands-links | session     | none (item page: harness, session)  | -            | -            |
| guardrails-bypass-commands-links | tools       | none (keeps the current time range) | -            | -            |
| guardrails-command-churn-links   | session     | none (item page: harness, session)  | -            | -            |
| guardrails-command-churn-links   | tools       | none (keeps the current time range) | -            | -            |

## recurrence (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-actionable          ] [kpi-clusters            ] [kpi-targets             ]                                     |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-classed-failures                                                                                        ]     |
+-- what-crosses-the-evidence-threshold -------------------------------------------------------------------------------+
| [actionable-repeats                                                                                            ]     |
+-- what-recurs-across-tasks ------------------------------------------------------------------------------------------+
| [failure-clusters                                                                                              ]     |
| [recurring-targets                                                                                             ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [recurrence-kpi-actionable, recurrence-kpi-clusters, recurrence-kpi-targets], daily-trend [recurrence-daily-classed-failures], what-crosses-the-evidence-threshold [recurrence-actionable-repeats], what-recurs-across-tasks [recurrence-failure-clusters, recurrence-recurring-targets]
Views: default, filter-recurrence-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against ranked-worklist: Partial match: Actionable repeats is the worklist, but there are no sorted bars, and it sits below KPI cards and a daily chart; the threshold is not visible as a mark.

| Source element                     | Target page | Filters set                         | count_source | count_target |
| ---------------------------------- | ----------- | ----------------------------------- | ------------ | ------------ |
| recurrence-failure-clusters-links  | tools       | none (keeps the current time range) | -            | -            |
| recurrence-recurring-targets-links | guardrails  | none (keeps the current time range) | -            | -            |

## intent (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
| filters: Time range · UTC [select]   Harness [select] (i)                                                            |
+-- at-a-glance -------------------------------------------------------------------------------------------------------+
| [kpi-labelled            ] [kpi-corrected           ] [kpi-frustrated          ]                                     |
+-- daily-trend -------------------------------------------------------------------------------------------------------+
| [daily-corrected                                                                                               ]     |
+-- what-do-users-ask-for-and-what-do-they-correct --------------------------------------------------------------------+
| [task-type-mix                                        ] [correction-kinds                                     ]      |
| [by-harness                                                                                                    ]     |
+-- does-heavier-effort-reduce-corrections ----------------------------------------------------------------------------+
| [effort-payoff                                                                                                 ]     |
+-- which-tasks-were-corrected ----------------------------------------------------------------------------------------+
| [recent-corrected                                                                                              ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, filters, at-a-glance [intent-kpi-corrected, intent-kpi-frustrated, intent-kpi-labelled], daily-trend [intent-daily-corrected], what-do-users-ask-for-and-what-do-they-correct [intent-by-harness, intent-correction-kinds, intent-task-type-mix], does-heavier-effort-reduce-corrections [intent-effort-payoff], which-tasks-were-corrected [intent-recent-corrected]
Views: default, filter-intent-filters
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against comparison: Partial match: Task type mix answers the share question but sits below KPI cards and a daily chart; corrections by harness is a table.

| Source element                | Target page | Filters set                        | count_source | count_target |
| ----------------------------- | ----------- | ---------------------------------- | ------------ | ------------ |
| intent-recent-corrected-links | session     | none (item page: harness, session) | -            | -            |

## glossary (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
+-- pipeline ----------------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- provider ----------------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- cache-efficiency --------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- reasoning-effort --------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- tool-failures -----------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- friction ----------------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- verification-and-guardrails ---------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- recurring-failures ------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- requests-and-corrections ------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- findings-and-fixes ------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
| [entries                                                         ]                                                   |
+-- not-shown ---------------------------------------------------------------------------------------------------------+
| [entries                                                         ]                                                   |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, pipeline [glossary-entries], provider [glossary-entries], cache-efficiency [glossary-entries], reasoning-effort [glossary-entries], tool-failures [glossary-entries], friction [glossary-entries], verification-and-guardrails [glossary-entries], recurring-failures [glossary-entries], requests-and-corrections [glossary-entries], findings-and-fixes [glossary-entries], not-shown [glossary-entries]
Views: default
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against reference: Match: a reference page of entries grouped by view; no search or filter control (the archetype's lookup zone has filters).

| Source element | Target page | Filters set | count_source | count_target |
| -------------- | ----------- | ----------- | ------------ | ------------ |

## session (current, adjust mode)

```text
+----------------------------------------------------------------------------------------------------------------------+
| head: title, question, timings badge (orphan)        [Refresh]                                                       |
+-- tasks -------------------------------------------------------------------------------------------------------------+
| [tasks                                                                                                         ]     |
+-- model-usage-and-calls ---------------------------------------------------------------------------------------------+
| [model-usage                                                   ] [model-usage                                ]       |
+-- tool-calls --------------------------------------------------------------------------------------------------------+
| [tool-calls                                                                                                    ]     |
+-- user-and-policy-signals -------------------------------------------------------------------------------------------+
| [signals                                                                                                       ]     |
+----------------------------------------------------------------------------------------------------------------------+
```

Tagged: derived from build. Zones in rendered order: head, tasks [session-tasks], model-usage-and-calls [session-model-usage], tool-calls [session-tool-calls], user-and-policy-signals [session-signals]
Views: default
View notes: the filter view sets the harness to Claude Code through the URL. The `<details>` inline expands (View as table, Show all, Registry lists) are captured as the undeclared state open only to show their L2 content renders; the contract reserves open for a drawer and these pages have none.
Drawer: no
Audit against reference: Match: a reference page for one session, in four sections of tables; no record head figure strip.

| Source element           | Target page | Filters set                                     | count_source | count_target |
| ------------------------ | ----------- | ----------------------------------------------- | ------------ | ------------ |
| session-tool-calls-links | tools       | none (keeps the current time range and harness) | -            | -            |
