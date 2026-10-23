# Feature inventory: Agent Introspection dashboard baseline, 2026-10-22

One row per control, panel, column, link and action on each scoped page, from the Step 0 `default` snapshot (recaptured with the audit overlay). Repeated row links (a session id per table row) are one row with their count. Every row survives a change or records its new home; `removed: <reason>` only for a deletion the user asked for.

| Id                   | Page              | Kind    | Element (data-q or selector)               | Label                                         | What it is for                          | New home |
| -------------------- | ----------------- | ------- | ------------------------------------------ | --------------------------------------------- | --------------------------------------- | -------- |
| PIPELINE-01          | pipeline          | link    | skip-link                                  | Skip to content                               | opens a related page or detail          |          |
| PIPELINE-02          | pipeline          | link    | nav                                        | (x11)                                         | app navigation (every page)             |          |
| PIPELINE-03          | pipeline          | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| PIPELINE-04          | pipeline          | control | pipeline-filters                           | select (x2)                                   | filters every panel                     |          |
| PIPELINE-05          | pipeline          | action  | pipeline-filters                           | What the harness choice covers                | page action                             |          |
| PIPELINE-06          | pipeline          | panel   | pipeline-kpi-loaders                       | Loaders and snapshots                         | answers Q1.1                            |          |
| PIPELINE-07          | pipeline          | action  | pipeline-kpi-loaders                       | Definition of Loaders and snapshots           | explains the panel (definition popover) |          |
| PIPELINE-08          | pipeline          | link    | pipeline-kpi-loaders-links                 | Show loader refresh                           | opens a related page or detail          |          |
| PIPELINE-09          | pipeline          | panel   | pipeline-kpi-max-lag                       | Max fact lag                                  | answers Q1.1                            |          |
| PIPELINE-10          | pipeline          | action  | pipeline-kpi-max-lag                       | Definition of Max fact lag                    | explains the panel (definition popover) |          |
| PIPELINE-11          | pipeline          | panel   | pipeline-kpi-coverage                      | Coverage                                      | answers Q1.1                            |          |
| PIPELINE-12          | pipeline          | action  | pipeline-kpi-coverage                      | Definition of Coverage                        | explains the panel (definition popover) |          |
| PIPELINE-13          | pipeline          | link    | pipeline-kpi-coverage-links                | Open coverage grid                            | opens a related page or detail          |          |
| PIPELINE-14          | pipeline          | panel   | pipeline-kpi-parity                        | Source parity                                 | answers Q1.1                            |          |
| PIPELINE-15          | pipeline          | action  | pipeline-kpi-parity                        | Definition of Source parity                   | explains the panel (definition popover) |          |
| PIPELINE-16          | pipeline          | link    | pipeline-kpi-parity-links                  | Show source parity                            | opens a related page or detail          |          |
| PIPELINE-17          | pipeline          | panel   | pipeline-kpi-sanitization                  | Sanitization                                  | answers Q1.1                            |          |
| PIPELINE-18          | pipeline          | action  | pipeline-kpi-sanitization                  | Definition of Sanitization                    | explains the panel (definition popover) |          |
| PIPELINE-19          | pipeline          | link    | pipeline-kpi-sanitization-links            | Show sanitization                             | opens a related page or detail          |          |
| PIPELINE-20          | pipeline          | panel   | pipeline-loader-refresh                    | Loader and snapshot refresh                   | answers Q1.2                            |          |
| PIPELINE-21          | pipeline          | action  | pipeline-loader-refresh                    | Definition of Loader and snapshot refres      | explains the panel (definition popover) |          |
| PIPELINE-22          | pipeline          | panel   | pipeline-hook-spool                        | Hook spool by host                            | answers Q1.2                            |          |
| PIPELINE-23          | pipeline          | action  | pipeline-hook-spool                        | Definition of Hook spool by host              | explains the panel (definition popover) |          |
| PIPELINE-24          | pipeline          | panel   | pipeline-freshness-by-harness              | Freshness by harness                          | answers Q1.2                            |          |
| PIPELINE-25          | pipeline          | action  | pipeline-freshness-by-harness              | Definition of Freshness by harness            | explains the panel (definition popover) |          |
| PIPELINE-26          | pipeline          | panel   | pipeline-fact-rows-per-day                 | Fact rows per day                             | answers Q1.6                            |          |
| PIPELINE-27          | pipeline          | action  | pipeline-fact-rows-per-day                 | Definition of Fact rows per day               | explains the panel (definition popover) |          |
| PIPELINE-28          | pipeline          | panel   | pipeline-coverage-by-view                  | Coverage by view                              | answers Q1.4                            |          |
| PIPELINE-29          | pipeline          | action  | pipeline-coverage-by-view                  | Definition of Coverage by view                | explains the panel (definition popover) |          |
| PIPELINE-30          | pipeline          | action  | pipeline-coverage-by-view-columns          | View                                          | page action                             |          |
| PIPELINE-31          | pipeline          | action  | pipeline-coverage-by-view-columns          | Healthy                                       | page action                             |          |
| PIPELINE-32          | pipeline          | action  | pipeline-coverage-by-view-columns          | Unexplained                                   | page action                             |          |
| PIPELINE-33          | pipeline          | link    | pipeline-coverage-by-view-links            | Pipeline                                      | opens a related page or detail          |          |
| PIPELINE-34          | pipeline          | link    | pipeline-coverage-by-view-links            | Cache efficiency                              | opens a related page or detail          |          |
| PIPELINE-35          | pipeline          | link    | pipeline-coverage-by-view-links            | Reasoning effort                              | opens a related page or detail          |          |
| PIPELINE-36          | pipeline          | link    | pipeline-coverage-by-view-links            | Tool failures                                 | opens a related page or detail          |          |
| PIPELINE-37          | pipeline          | link    | pipeline-coverage-by-view-links            | Friction                                      | opens a related page or detail          |          |
| PIPELINE-38          | pipeline          | link    | pipeline-coverage-by-view-links            | Verification and guardrails                   | opens a related page or detail          |          |
| PIPELINE-39          | pipeline          | link    | pipeline-coverage-by-view-links            | Provider                                      | opens a related page or detail          |          |
| PIPELINE-40          | pipeline          | link    | pipeline-coverage-by-view-links            | Recurring failures                            | opens a related page or detail          |          |
| PIPELINE-41          | pipeline          | link    | pipeline-coverage-by-view-links            | Requests and corrections                      | opens a related page or detail          |          |
| PIPELINE-42          | pipeline          | panel   | pipeline-source-parity                     | Source parity                                 | answers Q1.3                            |          |
| PIPELINE-43          | pipeline          | action  | pipeline-source-parity                     | Definition of Source parity                   | explains the panel (definition popover) |          |
| PIPELINE-44          | pipeline          | action  | pipeline-source-parity-columns             | Harness                                       | page action                             |          |
| PIPELINE-45          | pipeline          | action  | pipeline-source-parity-columns             | Raw ops                                       | page action                             |          |
| PIPELINE-46          | pipeline          | action  | pipeline-source-parity-columns             | Fact ops                                      | page action                             |          |
| PIPELINE-47          | pipeline          | action  | pipeline-source-parity-columns             | Raw input                                     | page action                             |          |
| PIPELINE-48          | pipeline          | action  | pipeline-source-parity-columns             | Fact input                                    | page action                             |          |
| PIPELINE-49          | pipeline          | action  | pipeline-source-parity-columns             | Match                                         | page action                             |          |
| PIPELINE-50          | pipeline          | panel   | pipeline-all-equals-sum                    | All = Σ harnesses                             | answers Q1.3                            |          |
| PIPELINE-51          | pipeline          | action  | pipeline-all-equals-sum                    | Definition of All = Σ harnesses               | explains the panel (definition popover) |          |
| PIPELINE-52          | pipeline          | action  | pipeline-all-equals-sum-columns            | Fact                                          | page action                             |          |
| PIPELINE-53          | pipeline          | action  | pipeline-all-equals-sum-columns            | Measure                                       | page action                             |          |
| PIPELINE-54          | pipeline          | action  | pipeline-all-equals-sum-columns            | All                                           | page action                             |          |
| PIPELINE-55          | pipeline          | action  | pipeline-all-equals-sum-columns            | Σ harnesses                                   | page action                             |          |
| PIPELINE-56          | pipeline          | action  | pipeline-all-equals-sum-columns            | Match                                         | page action                             |          |
| PIPELINE-57          | pipeline          | panel   | pipeline-sanitization                      | Sanitization                                  | answers Q1.5                            |          |
| PIPELINE-58          | pipeline          | action  | pipeline-sanitization                      | Definition of Sanitization                    | explains the panel (definition popover) |          |
| PIPELINE-59          | pipeline          | action  | pipeline-sanitization-columns              | Table                                         | page action                             |          |
| PIPELINE-60          | pipeline          | action  | pipeline-sanitization-columns              | Rows                                          | page action                             |          |
| PIPELINE-61          | pipeline          | action  | pipeline-sanitization-columns              | Dropped keys                                  | page action                             |          |
| PIPELINE-62          | pipeline          | action  | pipeline-sanitization-columns              | Text > 160                                    | page action                             |          |
| PIPELINE-63          | pipeline          | action  | pipeline-sanitization-columns              | Home paths                                    | page action                             |          |
| PIPELINE-COVERAGE-01 | pipeline-coverage | link    | topbar                                     | Pipeline                                      | opens a related page or detail          |          |
| PIPELINE-COVERAGE-02 | pipeline-coverage | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| PIPELINE-COVERAGE-03 | pipeline-coverage | control | pipeline-coverage-filters                  | select (x2)                                   | filters every panel                     |          |
| PIPELINE-COVERAGE-04 | pipeline-coverage | action  | pipeline-coverage-filters                  | What the harness choice covers                | page action                             |          |
| PIPELINE-COVERAGE-05 | pipeline-coverage | panel   | pipeline-coverage-coverage-by-view         | Coverage by view                              | answers Q1.4                            |          |
| PIPELINE-COVERAGE-06 | pipeline-coverage | action  | pipeline-coverage-coverage-by-view         | Definition of Coverage by view                | explains the panel (definition popover) |          |
| PIPELINE-COVERAGE-07 | pipeline-coverage | action  | pipeline-coverage-coverage-by-view-columns | View                                          | page action                             |          |
| PIPELINE-COVERAGE-08 | pipeline-coverage | action  | pipeline-coverage-coverage-by-view-columns | Healthy                                       | page action                             |          |
| PIPELINE-COVERAGE-09 | pipeline-coverage | action  | pipeline-coverage-coverage-by-view-columns | Unexplained                                   | page action                             |          |
| PIPELINE-COVERAGE-10 | pipeline-coverage | panel   | pipeline-coverage-coverage-grid            | Coverage grid                                 | answers Q1.4                            |          |
| PIPELINE-COVERAGE-11 | pipeline-coverage | action  | pipeline-coverage-coverage-grid            | Definition of Coverage grid                   | explains the panel (definition popover) |          |
| PIPELINE-COVERAGE-12 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Signal                                        | table column                            |          |
| PIPELINE-COVERAGE-13 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | pipeline                                      | table column                            |          |
| PIPELINE-COVERAGE-14 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Fact freshness                                | table column                            |          |
| PIPELINE-COVERAGE-15 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Source parity                                 | table column                            |          |
| PIPELINE-COVERAGE-16 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Project attribution                           | table column                            |          |
| PIPELINE-COVERAGE-17 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | cache                                         | table column                            |          |
| PIPELINE-COVERAGE-18 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Input tokens                                  | table column                            |          |
| PIPELINE-COVERAGE-19 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Cached input                                  | table column                            |          |
| PIPELINE-COVERAGE-20 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Uncached input                                | table column                            |          |
| PIPELINE-COVERAGE-21 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Output tokens                                 | table column                            |          |
| PIPELINE-COVERAGE-22 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Input cache utilization                       | table column                            |          |
| PIPELINE-COVERAGE-23 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Completed operations                          | table column                            |          |
| PIPELINE-COVERAGE-24 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Usage-active sessions                         | table column                            |          |
| PIPELINE-COVERAGE-25 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | effort                                        | table column                            |          |
| PIPELINE-COVERAGE-26 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Reasoning tokens                              | table column                            |          |
| PIPELINE-COVERAGE-27 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Reasoning share of output                     | table column                            |          |
| PIPELINE-COVERAGE-28 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Requested effort                              | table column                            |          |
| PIPELINE-COVERAGE-29 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Tasks                                         | table column                            |          |
| PIPELINE-COVERAGE-30 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Heavy-effort task share                       | table column                            |          |
| PIPELINE-COVERAGE-31 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Task duration                                 | table column                            |          |
| PIPELINE-COVERAGE-32 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Model steps per task                          | table column                            |          |
| PIPELINE-COVERAGE-33 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Reasoning tokens per task                     | table column                            |          |
| PIPELINE-COVERAGE-34 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Delegations per task                          | table column                            |          |
| PIPELINE-COVERAGE-35 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | tools                                         | table column                            |          |
| PIPELINE-COVERAGE-36 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Tool calls                                    | table column                            |          |
| PIPELINE-COVERAGE-37 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Tool failure rate                             | table column                            |          |
| PIPELINE-COVERAGE-38 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Failure signature                             | table column                            |          |
| PIPELINE-COVERAGE-39 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Tasks affected by failures                    | table column                            |          |
| PIPELINE-COVERAGE-40 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Repeated attempts                             | table column                            |          |
| PIPELINE-COVERAGE-41 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Tool loops                                    | table column                            |          |
| PIPELINE-COVERAGE-42 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | friction                                      | table column                            |          |
| PIPELINE-COVERAGE-43 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Interrupted tasks                             | table column                            |          |
| PIPELINE-COVERAGE-44 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Steered tasks                                 | table column                            |          |
| PIPELINE-COVERAGE-45 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Errored tasks                                 | table column                            |          |
| PIPELINE-COVERAGE-46 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Quick follow-up                               | table column                            |          |
| PIPELINE-COVERAGE-47 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Clean completion                              | table column                            |          |
| PIPELINE-COVERAGE-48 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Recovery after failure                        | table column                            |          |
| PIPELINE-COVERAGE-49 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | guardrails                                    | table column                            |          |
| PIPELINE-COVERAGE-50 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Tool approval decisions                       | table column                            |          |
| PIPELINE-COVERAGE-51 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Rejected tool calls                           | table column                            |          |
| PIPELINE-COVERAGE-52 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Quality-gate bypass                           | table column                            |          |
| PIPELINE-COVERAGE-53 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Command churn                                 | table column                            |          |
| PIPELINE-COVERAGE-54 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Verification runs                             | table column                            |          |
| PIPELINE-COVERAGE-55 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Failures caught in the task                   | table column                            |          |
| PIPELINE-COVERAGE-56 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | provider                                      | table column                            |          |
| PIPELINE-COVERAGE-57 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Model calls                                   | table column                            |          |
| PIPELINE-COVERAGE-58 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Call error rate                               | table column                            |          |
| PIPELINE-COVERAGE-59 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Model call latency                            | table column                            |          |
| PIPELINE-COVERAGE-60 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Time to first token                           | table column                            |          |
| PIPELINE-COVERAGE-61 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Retries                                       | table column                            |          |
| PIPELINE-COVERAGE-62 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Unknown outcomes                              | table column                            |          |
| PIPELINE-COVERAGE-63 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | recurrence                                    | table column                            |          |
| PIPELINE-COVERAGE-64 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Recurring targets                             | table column                            |          |
| PIPELINE-COVERAGE-65 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Recurring failure signatures                  | table column                            |          |
| PIPELINE-COVERAGE-66 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Actionable repeats                            | table column                            |          |
| PIPELINE-COVERAGE-67 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Project concentration                         | table column                            |          |
| PIPELINE-COVERAGE-68 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | intent                                        | table column                            |          |
| PIPELINE-COVERAGE-69 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Labelled tasks                                | table column                            |          |
| PIPELINE-COVERAGE-70 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Task type                                     | table column                            |          |
| PIPELINE-COVERAGE-71 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Corrected by the next prompt                  | table column                            |          |
| PIPELINE-COVERAGE-72 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Correction kinds                              | table column                            |          |
| PIPELINE-COVERAGE-73 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Frustrated follow-ups                         | table column                            |          |
| PIPELINE-COVERAGE-74 | pipeline-coverage | column  | pipeline-coverage-coverage-grid-columns    | Effort payoff by task type                    | table column                            |          |
| PIPELINE-COVERAGE-75 | pipeline-coverage | link    | pipeline-coverage-coverage-grid-links      | Open Pipeline                                 | opens a related page or detail          |          |
| PROVIDER-01          | provider          | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| PROVIDER-02          | provider          | control | provider-filters                           | select (x2)                                   | filters every panel                     |          |
| PROVIDER-03          | provider          | action  | provider-filters                           | What the harness choice covers                | page action                             |          |
| PROVIDER-04          | provider          | panel   | provider-kpi-model-calls                   | Model calls                                   | answers Q2.11                           |          |
| PROVIDER-05          | provider          | action  | provider-kpi-model-calls                   | Definition of Model calls                     | explains the panel (definition popover) |          |
| PROVIDER-06          | provider          | panel   | provider-kpi-call-error-rate               | Call error rate                               | answers Q2.11                           |          |
| PROVIDER-07          | provider          | action  | provider-kpi-call-error-rate               | Definition of Call error rate                 | explains the panel (definition popover) |          |
| PROVIDER-08          | provider          | panel   | provider-kpi-retries                       | Retries                                       | answers Q2.11                           |          |
| PROVIDER-09          | provider          | action  | provider-kpi-retries                       | Definition of Retries                         | explains the panel (definition popover) |          |
| PROVIDER-10          | provider          | panel   | provider-kpi-unknown-outcomes              | Unknown outcomes                              | answers Q2.11                           |          |
| PROVIDER-11          | provider          | action  | provider-kpi-unknown-outcomes              | Definition of Unknown outcomes                | explains the panel (definition popover) |          |
| PROVIDER-12          | provider          | panel   | provider-daily-error-rate                  | Daily call error rate                         | answers Q2.12                           |          |
| PROVIDER-13          | provider          | action  | provider-daily-error-rate                  | Definition of Daily call error rate           | explains the panel (definition popover) |          |
| PROVIDER-14          | provider          | panel   | provider-daily-ttft                        | Daily median time to first token              | answers Q2.12                           |          |
| PROVIDER-15          | provider          | action  | provider-daily-ttft                        | Definition of Daily median time to first      | explains the panel (definition popover) |          |
| PROVIDER-16          | provider          | panel   | provider-latency-by-model                  | Latency and time to first token by model      | answers Q2.13                           |          |
| PROVIDER-17          | provider          | action  | provider-latency-by-model                  | Definition of Latency and time to first       | explains the panel (definition popover) |          |
| PROVIDER-18          | provider          | action  | provider-latency-by-model-columns          | Harness                                       | page action                             |          |
| PROVIDER-19          | provider          | action  | provider-latency-by-model-columns          | Model                                         | page action                             |          |
| PROVIDER-20          | provider          | action  | provider-latency-by-model-columns          | Calls                                         | page action                             |          |
| PROVIDER-21          | provider          | action  | provider-latency-by-model-columns          | P50 latency                                   | page action                             |          |
| PROVIDER-22          | provider          | action  | provider-latency-by-model-columns          | P95 latency                                   | page action                             |          |
| PROVIDER-23          | provider          | action  | provider-latency-by-model-columns          | P50 TTFT                                      | page action                             |          |
| PROVIDER-24          | provider          | action  | provider-latency-by-model-columns          | P95 TTFT                                      | page action                             |          |
| PROVIDER-25          | provider          | panel   | provider-errors-by-class                   | Call errors by class                          | answers Q2.14                           |          |
| PROVIDER-26          | provider          | action  | provider-errors-by-class                   | Definition of Call errors by class            | explains the panel (definition popover) |          |
| PROVIDER-27          | provider          | action  | provider-errors-by-class-columns           | Harness                                       | page action                             |          |
| PROVIDER-28          | provider          | action  | provider-errors-by-class-columns           | Model                                         | page action                             |          |
| PROVIDER-29          | provider          | action  | provider-errors-by-class-columns           | Class                                         | page action                             |          |
| PROVIDER-30          | provider          | action  | provider-errors-by-class-columns           | Calls                                         | page action                             |          |
| PROVIDER-31          | provider          | action  | provider-errors-by-class-columns           | Last seen (UTC)                               | page action                             |          |
| PROVIDER-32          | provider          | link    | provider-errors-by-class-links             | Open Friction                                 | opens a related page or detail          |          |
| PROVIDER-33          | provider          | panel   | provider-retries                           | Retries                                       | answers Q2.14                           |          |
| PROVIDER-34          | provider          | action  | provider-retries                           | Definition of Retries                         | explains the panel (definition popover) |          |
| PROVIDER-35          | provider          | action  | provider-retries-columns                   | Harness                                       | page action                             |          |
| PROVIDER-36          | provider          | action  | provider-retries-columns                   | Extra attempts                                | page action                             |          |
| PROVIDER-37          | provider          | action  | provider-retries-columns                   | Measured as                                   | page action                             |          |
| CACHE-01             | cache             | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| CACHE-02             | cache             | control | cache-filters                              | select (x2)                                   | filters every panel                     |          |
| CACHE-03             | cache             | action  | cache-filters                              | What the harness choice covers                | page action                             |          |
| CACHE-04             | cache             | panel   | cache-kpi-input-tokens                     | Input tokens                                  | answers Q2.1                            |          |
| CACHE-05             | cache             | action  | cache-kpi-input-tokens                     | Definition of Input tokens                    | explains the panel (definition popover) |          |
| CACHE-06             | cache             | panel   | cache-kpi-cache-utilization                | Input cache utilization                       | answers Q2.1                            |          |
| CACHE-07             | cache             | action  | cache-kpi-cache-utilization                | Definition of Input cache utilization         | explains the panel (definition popover) |          |
| CACHE-08             | cache             | panel   | cache-kpi-uncached-input                   | Uncached input                                | answers Q2.1                            |          |
| CACHE-09             | cache             | action  | cache-kpi-uncached-input                   | Definition of Uncached input                  | explains the panel (definition popover) |          |
| CACHE-10             | cache             | panel   | cache-kpi-output-tokens                    | Output tokens                                 | answers Q2.1                            |          |
| CACHE-11             | cache             | action  | cache-kpi-output-tokens                    | Definition of Output tokens                   | explains the panel (definition popover) |          |
| CACHE-12             | cache             | panel   | cache-daily-tokens                         | Daily token consumption                       | answers Q2.2                            |          |
| CACHE-13             | cache             | action  | cache-daily-tokens                         | Definition of Daily token consumption         | explains the panel (definition popover) |          |
| CACHE-14             | cache             | panel   | cache-daily-cache-utilization              | Daily input cache utilization                 | answers Q2.2                            |          |
| CACHE-15             | cache             | action  | cache-daily-cache-utilization              | Definition of Daily input cache utilizat      | explains the panel (definition popover) |          |
| CACHE-16             | cache             | panel   | cache-by-harness                           | By harness                                    | answers Q2.3                            |          |
| CACHE-17             | cache             | action  | cache-by-harness                           | Definition of By harness                      | explains the panel (definition popover) |          |
| CACHE-18             | cache             | action  | cache-by-harness-columns                   | Harness                                       | page action                             |          |
| CACHE-19             | cache             | action  | cache-by-harness-columns                   | Input                                         | page action                             |          |
| CACHE-20             | cache             | action  | cache-by-harness-columns                   | Cached                                        | page action                             |          |
| CACHE-21             | cache             | column  | cache-by-harness-columns                   | Utilization                                   | table column                            |          |
| CACHE-22             | cache             | action  | cache-by-harness-columns                   | Uncached                                      | page action                             |          |
| CACHE-23             | cache             | action  | cache-by-harness-columns                   | Output                                        | page action                             |          |
| CACHE-24             | cache             | action  | cache-by-harness-columns                   | Operations                                    | page action                             |          |
| CACHE-25             | cache             | action  | cache-by-harness-columns                   | Sessions                                      | page action                             |          |
| CACHE-26             | cache             | panel   | cache-by-model                             | By model                                      | answers Q2.3                            |          |
| CACHE-27             | cache             | action  | cache-by-model                             | Definition of By model                        | explains the panel (definition popover) |          |
| CACHE-28             | cache             | action  | cache-by-model-columns                     | Harness                                       | page action                             |          |
| CACHE-29             | cache             | action  | cache-by-model-columns                     | Provider                                      | page action                             |          |
| CACHE-30             | cache             | action  | cache-by-model-columns                     | Model                                         | page action                             |          |
| CACHE-31             | cache             | action  | cache-by-model-columns                     | Operations                                    | page action                             |          |
| CACHE-32             | cache             | action  | cache-by-model-columns                     | Input                                         | page action                             |          |
| CACHE-33             | cache             | column  | cache-by-model-columns                     | Utilization                                   | table column                            |          |
| CACHE-34             | cache             | action  | cache-by-model-columns                     | Uncached                                      | page action                             |          |
| CACHE-35             | cache             | action  | cache-by-model-columns                     | Output                                        | page action                             |          |
| CACHE-36             | cache             | panel   | cache-top-uncached-sessions                | Sessions with the most uncached input         | answers Q2.4                            |          |
| CACHE-37             | cache             | action  | cache-top-uncached-sessions                | Definition of Sessions with the most unc      | explains the panel (definition popover) |          |
| CACHE-38             | cache             | action  | cache-top-uncached-sessions-columns        | Harness                                       | page action                             |          |
| CACHE-39             | cache             | action  | cache-top-uncached-sessions-columns        | Session                                       | page action                             |          |
| CACHE-40             | cache             | action  | cache-top-uncached-sessions-columns        | Models                                        | page action                             |          |
| CACHE-41             | cache             | action  | cache-top-uncached-sessions-columns        | Uncached                                      | page action                             |          |
| CACHE-42             | cache             | column  | cache-top-uncached-sessions-columns        | Utilization                                   | table column                            |          |
| CACHE-43             | cache             | action  | cache-top-uncached-sessions-columns        | Output                                        | page action                             |          |
| CACHE-44             | cache             | action  | cache-top-uncached-sessions-columns        | Operations                                    | page action                             |          |
| CACHE-45             | cache             | action  | cache-top-uncached-sessions-columns        | Last seen (UTC)                               | page action                             |          |
| CACHE-46             | cache             | link    | cache-top-uncached-sessions-links          | `<session id>` (x20)                          | opens a related page or detail          |          |
| EFFORT-01            | effort            | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| EFFORT-02            | effort            | control | effort-filters                             | select (x2)                                   | filters every panel                     |          |
| EFFORT-03            | effort            | action  | effort-filters                             | What the harness choice covers                | page action                             |          |
| EFFORT-04            | effort            | panel   | effort-kpi-reasoning-tokens                | Reasoning tokens                              | answers Q2.6                            |          |
| EFFORT-05            | effort            | action  | effort-kpi-reasoning-tokens                | Definition of Reasoning tokens                | explains the panel (definition popover) |          |
| EFFORT-06            | effort            | panel   | effort-kpi-reasoning-share                 | Reasoning share of output                     | answers Q2.6                            |          |
| EFFORT-07            | effort            | action  | effort-kpi-reasoning-share                 | Definition of Reasoning share of output       | explains the panel (definition popover) |          |
| EFFORT-08            | effort            | panel   | effort-kpi-tasks                           | Tasks                                         | answers Q2.6                            |          |
| EFFORT-09            | effort            | action  | effort-kpi-tasks                           | Definition of Tasks                           | explains the panel (definition popover) |          |
| EFFORT-10            | effort            | panel   | effort-kpi-heavy-share                     | Heavy-effort task share                       | answers Q2.6                            |          |
| EFFORT-11            | effort            | action  | effort-kpi-heavy-share                     | Definition of Heavy-effort task share         | explains the panel (definition popover) |          |
| EFFORT-12            | effort            | panel   | effort-daily-reasoning                     | Daily reasoning tokens by effort              | answers Q2.7                            |          |
| EFFORT-13            | effort            | action  | effort-daily-reasoning                     | Definition of Daily reasoning tokens by       | explains the panel (definition popover) |          |
| EFFORT-14            | effort            | panel   | effort-daily-effort-mix                    | Daily task mix by effort                      | answers Q2.7                            |          |
| EFFORT-15            | effort            | action  | effort-daily-effort-mix                    | Definition of Daily task mix by effort        | explains the panel (definition popover) |          |
| EFFORT-16            | effort            | panel   | effort-clean-by-effort                     | Clean completion by effort                    | answers Q2.8                            |          |
| EFFORT-17            | effort            | action  | effort-clean-by-effort                     | Definition of Clean completion by effort      | explains the panel (definition popover) |          |
| EFFORT-18            | effort            | panel   | effort-duration-by-effort                  | Task duration by effort                       | answers Q2.8                            |          |
| EFFORT-19            | effort            | action  | effort-duration-by-effort                  | Definition of Task duration by effort         | explains the panel (definition popover) |          |
| EFFORT-20            | effort            | panel   | effort-outcomes-by-effort                  | Task outcomes by reasoning effort             | answers Q2.5                            |          |
| EFFORT-21            | effort            | action  | effort-outcomes-by-effort                  | Definition of Task outcomes by reasoning      | explains the panel (definition popover) |          |
| EFFORT-22            | effort            | action  | effort-outcomes-by-effort-columns          | Harness                                       | page action                             |          |
| EFFORT-23            | effort            | action  | effort-outcomes-by-effort-columns          | Effort                                        | page action                             |          |
| EFFORT-24            | effort            | action  | effort-outcomes-by-effort-columns          | Tasks                                         | page action                             |          |
| EFFORT-25            | effort            | column  | effort-outcomes-by-effort-columns          | Clean                                         | table column                            |          |
| EFFORT-26            | effort            | column  | effort-outcomes-by-effort-columns          | Interrupted                                   | table column                            |          |
| EFFORT-27            | effort            | column  | effort-outcomes-by-effort-columns          | Follow-up                                     | table column                            |          |
| EFFORT-28            | effort            | column  | effort-outcomes-by-effort-columns          | Errored                                       | table column                            |          |
| EFFORT-29            | effort            | action  | effort-outcomes-by-effort-columns          | P50 time                                      | page action                             |          |
| EFFORT-30            | effort            | action  | effort-outcomes-by-effort-columns          | P90 time                                      | page action                             |          |
| EFFORT-31            | effort            | action  | effort-outcomes-by-effort-columns          | Steps / task                                  | page action                             |          |
| EFFORT-32            | effort            | action  | effort-outcomes-by-effort-columns          | Delegations / task                            | page action                             |          |
| EFFORT-33            | effort            | action  | effort-outcomes-by-effort-columns          | Reasoning / task                              | page action                             |          |
| EFFORT-34            | effort            | column  | effort-outcomes-by-effort-columns          | Reasoning share                               | table column                            |          |
| EFFORT-35            | effort            | panel   | effort-spend-by-effort                     | Reasoning spend by effort                     | answers Q2.5                            |          |
| EFFORT-36            | effort            | action  | effort-spend-by-effort                     | Definition of Reasoning spend by effort       | explains the panel (definition popover) |          |
| EFFORT-37            | effort            | action  | effort-spend-by-effort-columns             | Harness                                       | page action                             |          |
| EFFORT-38            | effort            | action  | effort-spend-by-effort-columns             | Effort                                        | page action                             |          |
| EFFORT-39            | effort            | action  | effort-spend-by-effort-columns             | Calls                                         | page action                             |          |
| EFFORT-40            | effort            | action  | effort-spend-by-effort-columns             | Reasoning                                     | page action                             |          |
| EFFORT-41            | effort            | column  | effort-spend-by-effort-columns             | Of all reasoning                              | table column                            |          |
| EFFORT-42            | effort            | action  | effort-spend-by-effort-columns             | Per call                                      | page action                             |          |
| EFFORT-43            | effort            | column  | effort-spend-by-effort-columns             | Share of output                               | table column                            |          |
| EFFORT-44            | effort            | panel   | effort-model-by-effort                     | Model × effort                                | answers Q2.9                            |          |
| EFFORT-45            | effort            | action  | effort-model-by-effort                     | Definition of Model × effort                  | explains the panel (definition popover) |          |
| EFFORT-46            | effort            | action  | effort-model-by-effort-columns             | Harness                                       | page action                             |          |
| EFFORT-47            | effort            | action  | effort-model-by-effort-columns             | Model                                         | page action                             |          |
| EFFORT-48            | effort            | action  | effort-model-by-effort-columns             | Effort                                        | page action                             |          |
| EFFORT-49            | effort            | action  | effort-model-by-effort-columns             | Tasks                                         | page action                             |          |
| EFFORT-50            | effort            | column  | effort-model-by-effort-columns             | Clean                                         | table column                            |          |
| EFFORT-51            | effort            | action  | effort-model-by-effort-columns             | P50 time                                      | page action                             |          |
| EFFORT-52            | effort            | action  | effort-model-by-effort-columns             | Steps                                         | page action                             |          |
| EFFORT-53            | effort            | action  | effort-model-by-effort-columns             | Reasoning / step                              | page action                             |          |
| EFFORT-54            | effort            | panel   | effort-top-reasoning-tasks                 | Top 25 tasks by reasoning tokens              | answers Q2.10                           |          |
| EFFORT-55            | effort            | action  | effort-top-reasoning-tasks                 | Definition of Top 25 tasks by reasoning       | explains the panel (definition popover) |          |
| EFFORT-56            | effort            | action  | effort-top-reasoning-tasks-columns         | Harness                                       | page action                             |          |
| EFFORT-57            | effort            | action  | effort-top-reasoning-tasks-columns         | Session                                       | page action                             |          |
| EFFORT-58            | effort            | action  | effort-top-reasoning-tasks-columns         | Model                                         | page action                             |          |
| EFFORT-59            | effort            | action  | effort-top-reasoning-tasks-columns         | Effort                                        | page action                             |          |
| EFFORT-60            | effort            | action  | effort-top-reasoning-tasks-columns         | Started (UTC)                                 | page action                             |          |
| EFFORT-61            | effort            | action  | effort-top-reasoning-tasks-columns         | Duration                                      | page action                             |          |
| EFFORT-62            | effort            | action  | effort-top-reasoning-tasks-columns         | Steps                                         | page action                             |          |
| EFFORT-63            | effort            | action  | effort-top-reasoning-tasks-columns         | Reasoning                                     | page action                             |          |
| EFFORT-64            | effort            | action  | effort-top-reasoning-tasks-columns         | Output                                        | page action                             |          |
| EFFORT-65            | effort            | action  | effort-top-reasoning-tasks-columns         | Clean                                         | page action                             |          |
| EFFORT-66            | effort            | link    | effort-top-reasoning-tasks-links           | `<session id>` (x25)                          | opens a related page or detail          |          |
| TOOLS-01             | tools             | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| TOOLS-02             | tools             | control | tools-filters                              | select (x2)                                   | filters every panel                     |          |
| TOOLS-03             | tools             | action  | tools-filters                              | What the harness choice covers                | page action                             |          |
| TOOLS-04             | tools             | panel   | tools-kpi-tool-calls                       | Tool calls                                    | answers Q3.2                            |          |
| TOOLS-05             | tools             | action  | tools-kpi-tool-calls                       | Definition of Tool calls                      | explains the panel (definition popover) |          |
| TOOLS-06             | tools             | panel   | tools-kpi-failure-rate                     | Tool failure rate                             | answers Q3.2                            |          |
| TOOLS-07             | tools             | action  | tools-kpi-failure-rate                     | Definition of Tool failure rate               | explains the panel (definition popover) |          |
| TOOLS-08             | tools             | panel   | tools-kpi-tasks-affected                   | Tasks affected by failures                    | answers Q3.2                            |          |
| TOOLS-09             | tools             | action  | tools-kpi-tasks-affected                   | Definition of Tasks affected by failures      | explains the panel (definition popover) |          |
| TOOLS-10             | tools             | panel   | tools-kpi-repeats                          | Tasks with repeated attempts                  | answers Q3.2                            |          |
| TOOLS-11             | tools             | action  | tools-kpi-repeats                          | Definition of Tasks with repeated attemp      | explains the panel (definition popover) |          |
| TOOLS-12             | tools             | panel   | tools-daily-failure-rate                   | Daily tool failure rate                       | answers Q3.3                            |          |
| TOOLS-13             | tools             | action  | tools-daily-failure-rate                   | Definition of Daily tool failure rate         | explains the panel (definition popover) |          |
| TOOLS-14             | tools             | panel   | tools-daily-tool-calls                     | Daily tool calls                              | answers Q3.3                            |          |
| TOOLS-15             | tools             | action  | tools-daily-tool-calls                     | Definition of Daily tool calls                | explains the panel (definition popover) |          |
| TOOLS-16             | tools             | panel   | tools-failures-by-tool                     | Failures by tool                              | answers Q3.1                            |          |
| TOOLS-17             | tools             | action  | tools-failures-by-tool                     | Definition of Failures by tool                | explains the panel (definition popover) |          |
| TOOLS-18             | tools             | action  | tools-failures-by-tool-columns             | Harness                                       | page action                             |          |
| TOOLS-19             | tools             | action  | tools-failures-by-tool-columns             | Tool                                          | page action                             |          |
| TOOLS-20             | tools             | action  | tools-failures-by-tool-columns             | Calls                                         | page action                             |          |
| TOOLS-21             | tools             | action  | tools-failures-by-tool-columns             | Failed                                        | page action                             |          |
| TOOLS-22             | tools             | column  | tools-failures-by-tool-columns             | Rate                                          | table column                            |          |
| TOOLS-23             | tools             | action  | tools-failures-by-tool-columns             | Failed tasks                                  | page action                             |          |
| TOOLS-24             | tools             | panel   | tools-failure-signatures                   | Failure signatures                            | answers Q3.1                            |          |
| TOOLS-25             | tools             | action  | tools-failure-signatures                   | Definition of Failure signatures              | explains the panel (definition popover) |          |
| TOOLS-26             | tools             | action  | tools-failure-signatures-columns           | Harness                                       | page action                             |          |
| TOOLS-27             | tools             | action  | tools-failure-signatures-columns           | Tool                                          | page action                             |          |
| TOOLS-28             | tools             | action  | tools-failure-signatures-columns           | Signature                                     | page action                             |          |
| TOOLS-29             | tools             | action  | tools-failure-signatures-columns           | Count                                         | page action                             |          |
| TOOLS-30             | tools             | action  | tools-failure-signatures-columns           | Tasks                                         | page action                             |          |
| TOOLS-31             | tools             | action  | tools-failure-signatures-columns           | Example                                       | page action                             |          |
| TOOLS-32             | tools             | link    | tools-failure-signatures-links             | `<session id>` (x30)                          | opens a related page or detail          |          |
| TOOLS-33             | tools             | link    | tools-failure-signatures-links             | Open Friction                                 | opens a related page or detail          |          |
| TOOLS-34             | tools             | link    | tools-failure-signatures-links             | Open Recurring failures                       | opens a related page or detail          |          |
| TOOLS-35             | tools             | panel   | tools-repeated-calls                       | Repeated calls                                | answers Q3.4                            |          |
| TOOLS-36             | tools             | action  | tools-repeated-calls                       | Definition of Repeated calls                  | explains the panel (definition popover) |          |
| TOOLS-37             | tools             | action  | tools-repeated-calls-columns               | Harness                                       | page action                             |          |
| TOOLS-38             | tools             | action  | tools-repeated-calls-columns               | Session                                       | page action                             |          |
| TOOLS-39             | tools             | action  | tools-repeated-calls-columns               | Tool                                          | page action                             |          |
| TOOLS-40             | tools             | action  | tools-repeated-calls-columns               | Command                                       | page action                             |          |
| TOOLS-41             | tools             | action  | tools-repeated-calls-columns               | Attempts                                      | page action                             |          |
| TOOLS-42             | tools             | action  | tools-repeated-calls-columns               | Longest run                                   | page action                             |          |
| TOOLS-43             | tools             | action  | tools-repeated-calls-columns               | Failed                                        | page action                             |          |
| TOOLS-44             | tools             | action  | tools-repeated-calls-columns               | Started (UTC)                                 | page action                             |          |
| TOOLS-45             | tools             | link    | tools-repeated-calls-links                 | `<session id>` (x25)                          | opens a related page or detail          |          |
| TOOLS-46             | tools             | link    | tools-repeated-calls-links                 | Open Friction                                 | opens a related page or detail          |          |
| TOOLS-47             | tools             | link    | tools-repeated-calls-links                 | Open Recurring failures                       | opens a related page or detail          |          |
| FRICTION-01          | friction          | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| FRICTION-02          | friction          | control | friction-filters                           | select (x2)                                   | filters every panel                     |          |
| FRICTION-03          | friction          | action  | friction-filters                           | What the harness choice covers                | page action                             |          |
| FRICTION-04          | friction          | panel   | friction-kpi-clean-completion              | Clean completion                              | answers Q3.5                            |          |
| FRICTION-05          | friction          | action  | friction-kpi-clean-completion              | Definition of Clean completion                | explains the panel (definition popover) |          |
| FRICTION-06          | friction          | panel   | friction-kpi-interrupted                   | Interrupted tasks                             | answers Q3.5                            |          |
| FRICTION-07          | friction          | action  | friction-kpi-interrupted                   | Definition of Interrupted tasks               | explains the panel (definition popover) |          |
| FRICTION-08          | friction          | panel   | friction-kpi-quick-follow-up               | Quick follow-up                               | answers Q3.5                            |          |
| FRICTION-09          | friction          | action  | friction-kpi-quick-follow-up               | Definition of Quick follow-up                 | explains the panel (definition popover) |          |
| FRICTION-10          | friction          | panel   | friction-kpi-recovery                      | Recovery after failure                        | answers Q3.7                            |          |
| FRICTION-11          | friction          | action  | friction-kpi-recovery                      | Definition of Recovery after failure          | explains the panel (definition popover) |          |
| FRICTION-12          | friction          | panel   | friction-daily-clean                       | Daily clean completion                        | answers Q3.6                            |          |
| FRICTION-13          | friction          | action  | friction-daily-clean                       | Definition of Daily clean completion          | explains the panel (definition popover) |          |
| FRICTION-14          | friction          | panel   | friction-daily-interrupt-steer             | Daily interrupt and steer signals             | answers Q3.6                            |          |
| FRICTION-15          | friction          | action  | friction-daily-interrupt-steer             | Definition of Daily interrupt and steer       | explains the panel (definition popover) |          |
| FRICTION-16          | friction          | panel   | friction-components-by-harness             | Friction components by harness                | answers Q3.7                            |          |
| FRICTION-17          | friction          | action  | friction-components-by-harness             | Definition of Friction components by har      | explains the panel (definition popover) |          |
| FRICTION-18          | friction          | action  | friction-components-by-harness-columns     | Harness                                       | page action                             |          |
| FRICTION-19          | friction          | action  | friction-components-by-harness-columns     | Tasks                                         | page action                             |          |
| FRICTION-20          | friction          | action  | friction-components-by-harness-columns     | Observes                                      | page action                             |          |
| FRICTION-21          | friction          | column  | friction-components-by-harness-columns     | Clean                                         | table column                            |          |
| FRICTION-22          | friction          | column  | friction-components-by-harness-columns     | Interrupted                                   | table column                            |          |
| FRICTION-23          | friction          | column  | friction-components-by-harness-columns     | Steered                                       | table column                            |          |
| FRICTION-24          | friction          | column  | friction-components-by-harness-columns     | Errored                                       | table column                            |          |
| FRICTION-25          | friction          | column  | friction-components-by-harness-columns     | Follow-up                                     | table column                            |          |
| FRICTION-26          | friction          | panel   | friction-recovery-by-harness               | Recovery by harness                           | answers Q3.7                            |          |
| FRICTION-27          | friction          | action  | friction-recovery-by-harness               | Definition of Recovery by harness             | explains the panel (definition popover) |          |
| FRICTION-28          | friction          | action  | friction-recovery-by-harness-columns       | Harness                                       | page action                             |          |
| FRICTION-29          | friction          | action  | friction-recovery-by-harness-columns       | Failed ops                                    | page action                             |          |
| FRICTION-30          | friction          | action  | friction-recovery-by-harness-columns       | Recovered                                     | page action                             |          |
| FRICTION-31          | friction          | column  | friction-recovery-by-harness-columns       | Rate                                          | table column                            |          |
| FRICTION-32          | friction          | column  | friction-recovery-by-harness-columns       | P50 to recover                                | table column                            |          |
| FRICTION-33          | friction          | panel   | friction-failing-operations                | Operations that fail most                     | answers Q3.8                            |          |
| FRICTION-34          | friction          | action  | friction-failing-operations                | Definition of Operations that fail most       | explains the panel (definition popover) |          |
| FRICTION-35          | friction          | action  | friction-failing-operations-columns        | Harness                                       | page action                             |          |
| FRICTION-36          | friction          | action  | friction-failing-operations-columns        | Operation                                     | page action                             |          |
| FRICTION-37          | friction          | action  | friction-failing-operations-columns        | Failed                                        | page action                             |          |
| FRICTION-38          | friction          | action  | friction-failing-operations-columns        | Recovered                                     | page action                             |          |
| FRICTION-39          | friction          | panel   | friction-failure-classes                   | Failure classes in friction tasks             | answers Q3.8                            |          |
| FRICTION-40          | friction          | action  | friction-failure-classes                   | Definition of Failure classes in frictio      | explains the panel (definition popover) |          |
| FRICTION-41          | friction          | action  | friction-failure-classes-columns           | Failure class                                 | page action                             |          |
| FRICTION-42          | friction          | action  | friction-failure-classes-columns           | Tasks                                         | page action                             |          |
| FRICTION-43          | friction          | action  | friction-failure-classes-columns           | Failures                                      | page action                             |          |
| FRICTION-44          | friction          | action  | friction-failure-classes-columns           | Harnesses                                     | page action                             |          |
| FRICTION-45          | friction          | action  | friction-failure-classes-columns           | Last seen (UTC)                               | page action                             |          |
| FRICTION-46          | friction          | panel   | friction-latest-friction-tasks             | Latest interrupted, steered, or errored tasks | answers Q3.9                            |          |
| FRICTION-47          | friction          | action  | friction-latest-friction-tasks             | Definition of Latest interrupted, steere      | explains the panel (definition popover) |          |
| FRICTION-48          | friction          | action  | friction-latest-friction-tasks-columns     | Harness                                       | page action                             |          |
| FRICTION-49          | friction          | action  | friction-latest-friction-tasks-columns     | Type                                          | page action                             |          |
| FRICTION-50          | friction          | action  | friction-latest-friction-tasks-columns     | Session                                       | page action                             |          |
| FRICTION-51          | friction          | action  | friction-latest-friction-tasks-columns     | Model                                         | page action                             |          |
| FRICTION-52          | friction          | action  | friction-latest-friction-tasks-columns     | Effort                                        | page action                             |          |
| FRICTION-53          | friction          | action  | friction-latest-friction-tasks-columns     | Failing tool                                  | page action                             |          |
| FRICTION-54          | friction          | action  | friction-latest-friction-tasks-columns     | Failure class                                 | page action                             |          |
| FRICTION-55          | friction          | action  | friction-latest-friction-tasks-columns     | Started (UTC)                                 | page action                             |          |
| FRICTION-56          | friction          | action  | friction-latest-friction-tasks-columns     | Duration                                      | page action                             |          |
| FRICTION-57          | friction          | action  | friction-latest-friction-tasks-columns     | Interrupted                                   | page action                             |          |
| FRICTION-58          | friction          | action  | friction-latest-friction-tasks-columns     | Steered                                       | page action                             |          |
| FRICTION-59          | friction          | action  | friction-latest-friction-tasks-columns     | Errored                                       | page action                             |          |
| FRICTION-60          | friction          | action  | friction-latest-friction-tasks-columns     | Follow-up                                     | page action                             |          |
| FRICTION-61          | friction          | link    | friction-latest-friction-tasks-links       | `<session id>` (x25)                          | opens a related page or detail          |          |
| GUARDRAILS-01        | guardrails        | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| GUARDRAILS-02        | guardrails        | control | guardrails-filters                         | select (x2)                                   | filters every panel                     |          |
| GUARDRAILS-03        | guardrails        | action  | guardrails-filters                         | What the harness choice covers                | page action                             |          |
| GUARDRAILS-04        | guardrails        | panel   | guardrails-kpi-rejected                    | Rejected tool calls                           | answers Q3.10                           |          |
| GUARDRAILS-05        | guardrails        | action  | guardrails-kpi-rejected                    | Definition of Rejected tool calls             | explains the panel (definition popover) |          |
| GUARDRAILS-06        | guardrails        | panel   | guardrails-kpi-approvals                   | Approval decisions                            | answers Q3.10                           |          |
| GUARDRAILS-07        | guardrails        | action  | guardrails-kpi-approvals                   | Definition of Approval decisions              | explains the panel (definition popover) |          |
| GUARDRAILS-08        | guardrails        | panel   | guardrails-kpi-gate-bypass                 | Quality-gate bypass                           | answers Q3.10                           |          |
| GUARDRAILS-09        | guardrails        | action  | guardrails-kpi-gate-bypass                 | Definition of Quality-gate bypass             | explains the panel (definition popover) |          |
| GUARDRAILS-10        | guardrails        | panel   | guardrails-kpi-command-churn               | Command churn                                 | answers Q3.10                           |          |
| GUARDRAILS-11        | guardrails        | action  | guardrails-kpi-command-churn               | Definition of Command churn                   | explains the panel (definition popover) |          |
| GUARDRAILS-12        | guardrails        | panel   | guardrails-daily-rejection-rate            | Daily rejection rate                          | answers Q3.11                           |          |
| GUARDRAILS-13        | guardrails        | action  | guardrails-daily-rejection-rate            | Definition of Daily rejection rate            | explains the panel (definition popover) |          |
| GUARDRAILS-14        | guardrails        | link    | guardrails-daily-rejection-rate-links      | Open Findings and fixes                       | opens a related page or detail          |          |
| GUARDRAILS-15        | guardrails        | panel   | guardrails-verification-runs               | Verification runs                             | answers Q3.12                           |          |
| GUARDRAILS-16        | guardrails        | action  | guardrails-verification-runs               | Definition of Verification runs               | explains the panel (definition popover) |          |
| GUARDRAILS-17        | guardrails        | panel   | guardrails-verification-by-kind            | Verification runs by kind                     | answers Q3.12                           |          |
| GUARDRAILS-18        | guardrails        | action  | guardrails-verification-by-kind            | Definition of Verification runs by kind       | explains the panel (definition popover) |          |
| GUARDRAILS-19        | guardrails        | action  | guardrails-verification-by-kind-columns    | Kind                                          | page action                             |          |
| GUARDRAILS-20        | guardrails        | action  | guardrails-verification-by-kind-columns    | Harness                                       | page action                             |          |
| GUARDRAILS-21        | guardrails        | action  | guardrails-verification-by-kind-columns    | Runs                                          | page action                             |          |
| GUARDRAILS-22        | guardrails        | action  | guardrails-verification-by-kind-columns    | Failed                                        | page action                             |          |
| GUARDRAILS-23        | guardrails        | column  | guardrails-verification-by-kind-columns    | Failed rate                                   | table column                            |          |
| GUARDRAILS-24        | guardrails        | column  | guardrails-verification-by-kind-columns    | Caught                                        | table column                            |          |
| GUARDRAILS-25        | guardrails        | action  | guardrails-verification-by-kind-columns    | Tasks                                         | page action                             |          |
| GUARDRAILS-26        | guardrails        | column  | guardrails-verification-by-kind-columns    | Last seen (UTC)                               | table column                            |          |
| GUARDRAILS-27        | guardrails        | panel   | guardrails-failures-caught                 | Failures caught in the task                   | answers Q3.12                           |          |
| GUARDRAILS-28        | guardrails        | action  | guardrails-failures-caught                 | Definition of Failures caught in the tas      | explains the panel (definition popover) |          |
| GUARDRAILS-29        | guardrails        | action  | guardrails-failures-caught-columns         | Harness                                       | page action                             |          |
| GUARDRAILS-30        | guardrails        | action  | guardrails-failures-caught-columns         | Failed kinds                                  | page action                             |          |
| GUARDRAILS-31        | guardrails        | action  | guardrails-failures-caught-columns         | Caught                                        | page action                             |          |
| GUARDRAILS-32        | guardrails        | column  | guardrails-failures-caught-columns         | Rate                                          | table column                            |          |
| GUARDRAILS-33        | guardrails        | column  | guardrails-failures-caught-columns         | P50 to catch                                  | table column                            |          |
| GUARDRAILS-34        | guardrails        | panel   | guardrails-approvals-by-source             | Approval decisions by source                  | answers Q3.13                           |          |
| GUARDRAILS-35        | guardrails        | action  | guardrails-approvals-by-source             | Definition of Approval decisions by sour      | explains the panel (definition popover) |          |
| GUARDRAILS-36        | guardrails        | action  | guardrails-approvals-by-source-columns     | Harness                                       | page action                             |          |
| GUARDRAILS-37        | guardrails        | action  | guardrails-approvals-by-source-columns     | Decision                                      | page action                             |          |
| GUARDRAILS-38        | guardrails        | action  | guardrails-approvals-by-source-columns     | Source                                        | page action                             |          |
| GUARDRAILS-39        | guardrails        | action  | guardrails-approvals-by-source-columns     | Decisions                                     | page action                             |          |
| GUARDRAILS-40        | guardrails        | panel   | guardrails-bypass-commands                 | Quality-gate bypass commands                  | answers Q3.14                           |          |
| GUARDRAILS-41        | guardrails        | action  | guardrails-bypass-commands                 | Definition of Quality-gate bypass comman      | explains the panel (definition popover) |          |
| GUARDRAILS-42        | guardrails        | action  | guardrails-bypass-commands-columns         | At (UTC)                                      | page action                             |          |
| GUARDRAILS-43        | guardrails        | action  | guardrails-bypass-commands-columns         | Harness                                       | page action                             |          |
| GUARDRAILS-44        | guardrails        | action  | guardrails-bypass-commands-columns         | Session                                       | page action                             |          |
| GUARDRAILS-45        | guardrails        | action  | guardrails-bypass-commands-columns         | Command                                       | page action                             |          |
| GUARDRAILS-46        | guardrails        | action  | guardrails-bypass-commands-columns         | Outcome                                       | page action                             |          |
| GUARDRAILS-47        | guardrails        | link    | guardrails-bypass-commands-links           | `<session id>` (x17)                          | opens a related page or detail          |          |
| GUARDRAILS-48        | guardrails        | link    | guardrails-bypass-commands-links           | Open Tool failures                            | opens a related page or detail          |          |
| GUARDRAILS-49        | guardrails        | panel   | guardrails-command-churn                   | Command churn                                 | answers Q3.14                           |          |
| GUARDRAILS-50        | guardrails        | action  | guardrails-command-churn                   | Definition of Command churn                   | explains the panel (definition popover) |          |
| GUARDRAILS-51        | guardrails        | action  | guardrails-command-churn-columns           | Harness                                       | page action                             |          |
| GUARDRAILS-52        | guardrails        | action  | guardrails-command-churn-columns           | Session                                       | page action                             |          |
| GUARDRAILS-53        | guardrails        | action  | guardrails-command-churn-columns           | File                                          | page action                             |          |
| GUARDRAILS-54        | guardrails        | action  | guardrails-command-churn-columns           | Commands                                      | page action                             |          |
| GUARDRAILS-55        | guardrails        | action  | guardrails-command-churn-columns           | Calls                                         | page action                             |          |
| GUARDRAILS-56        | guardrails        | action  | guardrails-command-churn-columns           | Failed                                        | page action                             |          |
| GUARDRAILS-57        | guardrails        | link    | guardrails-command-churn-links             | `<session id>` (x25)                          | opens a related page or detail          |          |
| GUARDRAILS-58        | guardrails        | link    | guardrails-command-churn-links             | Open Tool failures                            | opens a related page or detail          |          |
| RECURRENCE-01        | recurrence        | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| RECURRENCE-02        | recurrence        | control | recurrence-filters                         | select (x2)                                   | filters every panel                     |          |
| RECURRENCE-03        | recurrence        | action  | recurrence-filters                         | What the harness choice covers                | page action                             |          |
| RECURRENCE-04        | recurrence        | panel   | recurrence-kpi-actionable                  | Actionable repeats                            | answers Q4.1                            |          |
| RECURRENCE-05        | recurrence        | action  | recurrence-kpi-actionable                  | Definition of Actionable repeats              | explains the panel (definition popover) |          |
| RECURRENCE-06        | recurrence        | panel   | recurrence-kpi-clusters                    | Recurring failure clusters                    | answers Q4.2                            |          |
| RECURRENCE-07        | recurrence        | action  | recurrence-kpi-clusters                    | Definition of Recurring failure clusters      | explains the panel (definition popover) |          |
| RECURRENCE-08        | recurrence        | panel   | recurrence-kpi-targets                     | Recurring targets                             | answers Q4.2                            |          |
| RECURRENCE-09        | recurrence        | action  | recurrence-kpi-targets                     | Definition of Recurring targets               | explains the panel (definition popover) |          |
| RECURRENCE-10        | recurrence        | panel   | recurrence-daily-classed-failures          | Daily tool failures with a failure class      | answers Q4.3                            |          |
| RECURRENCE-11        | recurrence        | action  | recurrence-daily-classed-failures          | Definition of Daily tool failures with a      | explains the panel (definition popover) |          |
| RECURRENCE-12        | recurrence        | panel   | recurrence-actionable-repeats              | Actionable repeats                            | answers Q4.1                            |          |
| RECURRENCE-13        | recurrence        | action  | recurrence-actionable-repeats              | Definition of Actionable repeats              | explains the panel (definition popover) |          |
| RECURRENCE-14        | recurrence        | action  | recurrence-actionable-repeats-columns      | Tool family                                   | page action                             |          |
| RECURRENCE-15        | recurrence        | action  | recurrence-actionable-repeats-columns      | Failure class                                 | page action                             |          |
| RECURRENCE-16        | recurrence        | action  | recurrence-actionable-repeats-columns      | Harnesses (count)                             | page action                             |          |
| RECURRENCE-17        | recurrence        | action  | recurrence-actionable-repeats-columns      | Tools                                         | page action                             |          |
| RECURRENCE-18        | recurrence        | action  | recurrence-actionable-repeats-columns      | Count                                         | page action                             |          |
| RECURRENCE-19        | recurrence        | action  | recurrence-actionable-repeats-columns      | Tasks                                         | page action                             |          |
| RECURRENCE-20        | recurrence        | action  | recurrence-actionable-repeats-columns      | Days                                          | page action                             |          |
| RECURRENCE-21        | recurrence        | action  | recurrence-actionable-repeats-columns      | Last seen (UTC)                               | page action                             |          |
| RECURRENCE-22        | recurrence        | panel   | recurrence-failure-clusters                | Recurring failure clusters                    | answers Q4.2                            |          |
| RECURRENCE-23        | recurrence        | action  | recurrence-failure-clusters                | Definition of Recurring failure clusters      | explains the panel (definition popover) |          |
| RECURRENCE-24        | recurrence        | action  | recurrence-failure-clusters-columns        | Tool family                                   | page action                             |          |
| RECURRENCE-25        | recurrence        | action  | recurrence-failure-clusters-columns        | Failure class                                 | page action                             |          |
| RECURRENCE-26        | recurrence        | action  | recurrence-failure-clusters-columns        | Harnesses (count)                             | page action                             |          |
| RECURRENCE-27        | recurrence        | action  | recurrence-failure-clusters-columns        | Tools                                         | page action                             |          |
| RECURRENCE-28        | recurrence        | action  | recurrence-failure-clusters-columns        | Count                                         | page action                             |          |
| RECURRENCE-29        | recurrence        | action  | recurrence-failure-clusters-columns        | Tasks                                         | page action                             |          |
| RECURRENCE-30        | recurrence        | action  | recurrence-failure-clusters-columns        | Days                                          | page action                             |          |
| RECURRENCE-31        | recurrence        | action  | recurrence-failure-clusters-columns        | Last seen (UTC)                               | page action                             |          |
| RECURRENCE-32        | recurrence        | link    | recurrence-failure-clusters-links          | Open Tool failures                            | opens a related page or detail          |          |
| RECURRENCE-33        | recurrence        | panel   | recurrence-recurring-targets               | Recurring targets                             | answers Q4.2                            |          |
| RECURRENCE-34        | recurrence        | action  | recurrence-recurring-targets               | Definition of Recurring targets               | explains the panel (definition popover) |          |
| RECURRENCE-35        | recurrence        | action  | recurrence-recurring-targets-columns       | File                                          | page action                             |          |
| RECURRENCE-36        | recurrence        | action  | recurrence-recurring-targets-columns       | Harnesses                                     | page action                             |          |
| RECURRENCE-37        | recurrence        | action  | recurrence-recurring-targets-columns       | Tasks                                         | page action                             |          |
| RECURRENCE-38        | recurrence        | action  | recurrence-recurring-targets-columns       | Days                                          | page action                             |          |
| RECURRENCE-39        | recurrence        | action  | recurrence-recurring-targets-columns       | Calls                                         | page action                             |          |
| RECURRENCE-40        | recurrence        | action  | recurrence-recurring-targets-columns       | Failed                                        | page action                             |          |
| RECURRENCE-41        | recurrence        | link    | recurrence-recurring-targets-links         | Open Verification and guardrails              | opens a related page or detail          |          |
| INTENT-01            | intent            | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| INTENT-02            | intent            | control | intent-filters                             | select (x2)                                   | filters every panel                     |          |
| INTENT-03            | intent            | action  | intent-filters                             | What the harness choice covers                | page action                             |          |
| INTENT-04            | intent            | panel   | intent-kpi-labelled                        | Labelled tasks                                | answers Q4.4                            |          |
| INTENT-05            | intent            | action  | intent-kpi-labelled                        | Definition of Labelled tasks                  | explains the panel (definition popover) |          |
| INTENT-06            | intent            | panel   | intent-kpi-corrected                       | Corrected by the next prompt                  | answers Q4.4                            |          |
| INTENT-07            | intent            | action  | intent-kpi-corrected                       | Definition of Corrected by the next prom      | explains the panel (definition popover) |          |
| INTENT-08            | intent            | panel   | intent-kpi-frustrated                      | Frustrated follow-ups                         | answers Q4.6                            |          |
| INTENT-09            | intent            | action  | intent-kpi-frustrated                      | Definition of Frustrated follow-ups           | explains the panel (definition popover) |          |
| INTENT-10            | intent            | panel   | intent-daily-corrected                     | Daily corrected-by-next-prompt rate           | answers Q4.5                            |          |
| INTENT-11            | intent            | action  | intent-daily-corrected                     | Definition of Daily corrected-by-next-pr      | explains the panel (definition popover) |          |
| INTENT-12            | intent            | panel   | intent-task-type-mix                       | Task type mix                                 | answers Q4.4                            |          |
| INTENT-13            | intent            | action  | intent-task-type-mix                       | Definition of Task type mix                   | explains the panel (definition popover) |          |
| INTENT-14            | intent            | panel   | intent-correction-kinds                    | Correction kinds                              | answers Q4.6                            |          |
| INTENT-15            | intent            | action  | intent-correction-kinds                    | Definition of Correction kinds                | explains the panel (definition popover) |          |
| INTENT-16            | intent            | panel   | intent-by-harness                          | By harness                                    | answers Q4.6                            |          |
| INTENT-17            | intent            | action  | intent-by-harness                          | Definition of By harness                      | explains the panel (definition popover) |          |
| INTENT-18            | intent            | action  | intent-by-harness-columns                  | Harness                                       | page action                             |          |
| INTENT-19            | intent            | action  | intent-by-harness-columns                  | Tasks                                         | page action                             |          |
| INTENT-20            | intent            | action  | intent-by-harness-columns                  | Labelled                                      | page action                             |          |
| INTENT-21            | intent            | column  | intent-by-harness-columns                  | Labelled share                                | table column                            |          |
| INTENT-22            | intent            | column  | intent-by-harness-columns                  | Corrected next                                | table column                            |          |
| INTENT-23            | intent            | column  | intent-by-harness-columns                  | Frustrated next                               | table column                            |          |
| INTENT-24            | intent            | panel   | intent-effort-payoff                       | Effort payoff by task type                    | answers Q4.7                            |          |
| INTENT-25            | intent            | action  | intent-effort-payoff                       | Definition of Effort payoff by task type      | explains the panel (definition popover) |          |
| INTENT-26            | intent            | action  | intent-effort-payoff-columns               | Task type                                     | page action                             |          |
| INTENT-27            | intent            | action  | intent-effort-payoff-columns               | Effort                                        | page action                             |          |
| INTENT-28            | intent            | action  | intent-effort-payoff-columns               | Tasks                                         | page action                             |          |
| INTENT-29            | intent            | column  | intent-effort-payoff-columns               | Corrected next                                | table column                            |          |
| INTENT-30            | intent            | column  | intent-effort-payoff-columns               | Clean completion                              | table column                            |          |
| INTENT-31            | intent            | panel   | intent-recent-corrected                    | Recent corrected tasks                        | answers Q4.8                            |          |
| INTENT-32            | intent            | action  | intent-recent-corrected                    | Definition of Recent corrected tasks          | explains the panel (definition popover) |          |
| INTENT-33            | intent            | action  | intent-recent-corrected-columns            | Harness                                       | page action                             |          |
| INTENT-34            | intent            | action  | intent-recent-corrected-columns            | Session                                       | page action                             |          |
| INTENT-35            | intent            | action  | intent-recent-corrected-columns            | Started (UTC)                                 | page action                             |          |
| INTENT-36            | intent            | action  | intent-recent-corrected-columns            | Task type                                     | page action                             |          |
| INTENT-37            | intent            | action  | intent-recent-corrected-columns            | Effort                                        | page action                             |          |
| INTENT-38            | intent            | action  | intent-recent-corrected-columns            | Correction                                    | page action                             |          |
| INTENT-39            | intent            | action  | intent-recent-corrected-columns            | Next sentiment                                | page action                             |          |
| INTENT-40            | intent            | link    | intent-recent-corrected-links              | `<session id>` (x25)                          | opens a related page or detail          |          |
| GLOSSARY-01          | glossary          | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| GLOSSARY-02          | glossary          | panel   | glossary-entries                           | one entry per signal (x67)                    | answers Q6.1                            |          |
| GLOSSARY-03          | glossary          | column  | glossary-entries-columns                   | (entry table columns) (x522)                  | table column                            |          |
| SESSION-01           | session           | action  | refresh                                    | ↻ Refresh                                     | reloads the latest facts (Q1.9)         |          |
| SESSION-02           | session           | panel   | session-tasks                              | Tasks                                         | answers Q6.2                            |          |
| SESSION-03           | session           | action  | session-tasks-columns                      | Started (UTC)                                 | page action                             |          |
| SESSION-04           | session           | action  | session-tasks-columns                      | Model                                         | page action                             |          |
| SESSION-05           | session           | action  | session-tasks-columns                      | Effort                                        | page action                             |          |
| SESSION-06           | session           | action  | session-tasks-columns                      | Task type                                     | page action                             |          |
| SESSION-07           | session           | action  | session-tasks-columns                      | Duration                                      | page action                             |          |
| SESSION-08           | session           | action  | session-tasks-columns                      | Steps                                         | page action                             |          |
| SESSION-09           | session           | action  | session-tasks-columns                      | Reasoning                                     | page action                             |          |
| SESSION-10           | session           | action  | session-tasks-columns                      | Interrupted                                   | page action                             |          |
| SESSION-11           | session           | action  | session-tasks-columns                      | Steered                                       | page action                             |          |
| SESSION-12           | session           | action  | session-tasks-columns                      | Errored                                       | page action                             |          |
| SESSION-13           | session           | action  | session-tasks-columns                      | Follow-up                                     | page action                             |          |
| SESSION-14           | session           | action  | session-tasks-columns                      | Clean                                         | page action                             |          |
| SESSION-15           | session           | action  | session-tasks-columns                      | Corrected next                                | page action                             |          |
| SESSION-16           | session           | action  | session-tasks-columns                      | Friction observed                             | page action                             |          |
| SESSION-17           | session           | panel   | session-model-usage                        | Model usage and calls (x2)                    | answers Q6.2                            |          |
| SESSION-18           | session           | action  | session-model-usage-columns                | Model                                         | page action                             |          |
| SESSION-19           | session           | action  | session-model-usage-columns                | Effort                                        | page action                             |          |
| SESSION-20           | session           | action  | session-model-usage-columns                | Operations                                    | page action                             |          |
| SESSION-21           | session           | action  | session-model-usage-columns                | Input                                         | page action                             |          |
| SESSION-22           | session           | action  | session-model-usage-columns                | Cached                                        | page action                             |          |
| SESSION-23           | session           | action  | session-model-usage-columns                | Output                                        | page action                             |          |
| SESSION-24           | session           | column  | session-model-usage-columns                | Reasoning                                     | table column                            |          |
| SESSION-25           | session           | column  | session-model-usage-columns                | Outcome                                       | table column                            |          |
| SESSION-26           | session           | column  | session-model-usage-columns                | Error                                         | table column                            |          |
| SESSION-27           | session           | column  | session-model-usage-columns                | Calls                                         | table column                            |          |
| SESSION-28           | session           | column  | session-model-usage-columns                | TTFT p50                                      | table column                            |          |
| SESSION-29           | session           | panel   | session-tool-calls                         | Tool calls                                    | answers Q6.2                            |          |
| SESSION-30           | session           | action  | session-tool-calls-columns                 | At (UTC)                                      | page action                             |          |
| SESSION-31           | session           | action  | session-tool-calls-columns                 | Tool                                          | page action                             |          |
| SESSION-32           | session           | action  | session-tool-calls-columns                 | Outcome                                       | page action                             |          |
| SESSION-33           | session           | action  | session-tool-calls-columns                 | Command                                       | page action                             |          |
| SESSION-34           | session           | column  | session-tool-calls-columns                 | Exit                                          | table column                            |          |
| SESSION-35           | session           | action  | session-tool-calls-columns                 | Failure signature                             | page action                             |          |
| SESSION-36           | session           | action  | session-tool-calls-columns                 | Targets                                       | page action                             |          |
| SESSION-37           | session           | link    | session-tool-calls-links                   | Open Tool failures                            | opens a related page or detail          |          |
| SESSION-38           | session           | panel   | session-signals                            | User and policy signals                       | answers Q6.2                            |          |
| SESSION-39           | session           | action  | session-signals-columns                    | At (UTC)                                      | page action                             |          |
| SESSION-40           | session           | action  | session-signals-columns                    | Signal                                        | page action                             |          |
| SESSION-41           | session           | action  | session-signals-columns                    | Detail                                        | page action                             |          |
| SESSION-42           | session           | action  | session-signals-columns                    | Source                                        | page action                             |          |
| SESSION-43           | session           | action  | session-signals-columns                    | Tool                                          | page action                             |          |

## Regression guard pages

None: audit-only run, no shared changes.
