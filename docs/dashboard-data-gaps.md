# Dashboard Data Gaps

What the dashboard cannot show, or shows only partly, and why. The sections from
"Harness support matrix" through "Signals no producer supports" are generated from
[`signal_support.toml`](../src/agent_introspection/facts_sql/signal_support.toml) by
`scripts/render_measures.py`, and a test fails when it drifts. The sections after it
record gaps in the data itself, measured on 2026-09-29. Definitions and routes are in
[Dashboard Measure v3](dashboard-measure-v3.md); the evidence for each finding is in
the [Dashboard v3 Plan](dashboard-v3-plan.md#findings-log).

Legend: ✓ the harness reaches the shared definition directly; ≈ it reaches it by a
different route (see its note); ✗ it does not emit the signal, so the dashboard shows
"not emitted", never zero.

<!-- BEGIN GENERATED FROM signal_support.toml -->

## Harness support matrix

50 per-harness signals across 5 harnesses: 250 cells, 145 aligned (✓), 66 reached by a different route (≈), 39 not emitted (✗).

| View | Signal | omp | Codex app-server | Codex CLI | Codex exec | Claude Code |
| --- | --- | --- | --- | --- | --- | --- |
| Pipeline | Fact freshness | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pipeline | Source parity | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pipeline | Project attribution | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Input tokens | ✓ | ✓ | ✓ | ✓ | ≈ |
| Cache efficiency | Cached input | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Uncached input | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Cache creation | ✓ | ≈ | ≈ | ≈ | ✓ |
| Cache efficiency | Output tokens | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Input cache utilization | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Completed operations | ≈ | ≈ | ≈ | ≈ | ≈ |
| Cache efficiency | Usage-active sessions | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reasoning effort | Reasoning tokens | ✓ | ✓ | ✓ | ✓ | ✗ |
| Reasoning effort | Reasoning share of output | ✓ | ✓ | ✓ | ✓ | ✗ |
| Reasoning effort | Requested effort | ✓ | ✓ | ✓ | ✓ | ≈ |
| Reasoning effort | Tasks | ≈ | ≈ | ≈ | ≈ | ≈ |
| Reasoning effort | Heavy-effort task share | ✓ | ✓ | ✓ | ✓ | ≈ |
| Reasoning effort | Task duration | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reasoning effort | Model steps per task | ≈ | ≈ | ≈ | ≈ | ≈ |
| Reasoning effort | Reasoning tokens per task | ✓ | ≈ | ≈ | ≈ | ✗ |
| Reasoning effort | Delegations per task | ✓ | ✗ | ✗ | ✗ | ✗ |
| Tool failures | Tool calls | ✓ | ✓ | ✓ | ✓ | ✓ |
| Tool failures | Tool failure rate | ✓ | ≈ | ≈ | ≈ | ✓ |
| Tool failures | Failure signature | ✓ | ✓ | ✓ | ✓ | ≈ |
| Tool failures | Tasks affected by failures | ✓ | ≈ | ≈ | ≈ | ✓ |
| Tool failures | Repeated attempts | ✗ | ✓ | ✓ | ✓ | ✗ |
| Tool failures | Tool loops | ✗ | ✓ | ✓ | ✓ | ✗ |
| Friction | Interrupted tasks | ≈ | ✓ | ✓ | ≈ | ✗ |
| Friction | Steered tasks | ✗ | ✓ | ✓ | ≈ | ✗ |
| Friction | Errored tasks | ✓ | ✗ | ✗ | ✗ | ≈ |
| Friction | Quick follow-up | ✓ | ✓ | ✓ | ✗ | ✓ |
| Friction | Clean completion | ✓ | ≈ | ≈ | ✗ | ≈ |
| Friction | Recovery after failure | ≈ | ≈ | ≈ | ≈ | ≈ |
| Guardrails | Tool approval decisions | ✗ | ✓ | ✓ | ✓ | ✓ |
| Guardrails | Rejected tool calls | ✗ | ✓ | ✓ | ✓ | ✓ |
| Guardrails | Sandbox outcomes | ✗ | ✓ | ✓ | ✓ | ✗ |
| Guardrails | Quality-gate bypass | ✗ | ✓ | ✓ | ✓ | ✗ |
| Guardrails | Command churn | ✗ | ≈ | ≈ | ≈ | ✗ |
| Provider | Model calls | ✓ | ≈ | ≈ | ≈ | ✓ |
| Provider | Call error rate | ✓ | ≈ | ≈ | ≈ | ✓ |
| Provider | Stream disconnects | ✗ | ✓ | ✓ | ✓ | ✗ |
| Provider | Model call latency | ✓ | ≈ | ≈ | ≈ | ✓ |
| Provider | Time to first token | ✓ | ✓ | ✓ | ✓ | ✓ |
| Provider | Output throughput | ✓ | ✗ | ✗ | ✗ | ✓ |
| Provider | Retries | ✗ | ≈ | ≈ | ≈ | ✓ |
| Provider | Unknown outcomes | ✓ | ≈ | ≈ | ≈ | ✓ |
| Provider | Model conformance | ✓ | ✗ | ✗ | ✗ | ✗ |
| Recurrence | Recurring targets | ✗ | ≈ | ≈ | ≈ | ✗ |
| Recurrence | Recurring failure signatures | ✓ | ✓ | ✓ | ✓ | ≈ |
| Recurrence | Actionable repeats | ✓ | ✓ | ✓ | ✓ | ≈ |
| Recurrence | Project concentration | ✓ | ✓ | ✓ | ✓ | ✓ |

## Signals a harness does not emit

### omp (11)

- **Repeated attempts**: Tool spans carry no argument identity.
- **Tool loops**: Tool spans carry no argument identity.
- **Steered tasks**: omp emits no steer event.
- **Tool approval decisions**: omp emits no approval decisions.
- **Rejected tool calls**: omp emits no approval decisions.
- **Sandbox outcomes**: omp has no sandbox.
- **Quality-gate bypass**: Command text is not emitted in a normalized form.
- **Command churn**: Tool spans carry no target.
- **Stream disconnects**: Chat spans record errors but not stream state.
- **Retries**: No attempt or retry identity.
- **Recurring targets**: Tool spans carry no target.

### Codex app-server (4)

- **Delegations per task**: Codex subagents run as separate threads and appear as separate tasks.
- **Errored tasks**: Turn spans carry no terminal error status.
- **Output throughput**: Completed responses carry TTFT but no call duration.
- **Model conformance**: No response model.

### Codex CLI (4)

- **Delegations per task**: Codex subagents run as separate threads and appear as separate tasks.
- **Errored tasks**: Turn spans carry no terminal error status.
- **Output throughput**: Completed responses carry TTFT but no call duration.
- **Model conformance**: No response model.

### Codex exec (6)

- **Delegations per task**: Codex subagents run as separate threads and appear as separate tasks.
- **Errored tasks**: Turn spans carry no terminal error status.
- **Quick follow-up**: Headless one-shot runs: nobody can follow up.
- **Clean completion**: Headless: follow-up is unobservable, so clean completion is NULL.
- **Output throughput**: Completed responses carry TTFT but no call duration.
- **Model conformance**: No response model.

### Claude Code (14)

- **Reasoning tokens**: Claude Code emits no separate thinking-token count; thinking is inside output_tokens.
- **Reasoning share of output**: No reasoning-token field.
- **Reasoning tokens per task**: No reasoning-token field.
- **Delegations per task**: Subagent completions are logged without a task link.
- **Repeated attempts**: Tool results carry sizes, not argument identity.
- **Tool loops**: Tool results carry no argument identity.
- **Interrupted tasks**: Claude Code emits no interrupt event.
- **Steered tasks**: Claude Code emits no steer event; queued prompts are separate interactions.
- **Sandbox outcomes**: Claude Code emits no sandbox outcome.
- **Quality-gate bypass**: Only `bash_argv0` is emitted, not flags.
- **Command churn**: Tool results carry no target.
- **Stream disconnects**: No stream-state field.
- **Model conformance**: No response model.
- **Recurring targets**: Tool results carry no target.

## Signals reached by a different route

These are emitted, but the route differs from the shared definition; the note says how.

| Signal | Harness | How it differs |
| --- | --- | --- |
| Input tokens | Claude Code | `input_tokens + cache_creation_tokens + cache_read_tokens`; the direct field excludes cache, so the three are summed. |
| Cache creation | Codex app-server | `cache_write_token_count` is reported but has been 0 on every event since 2026-08-24. |
| Cache creation | Codex CLI | `cache_write_token_count` is reported but has been 0 on every event since 2026-08-24. |
| Cache creation | Codex exec | `cache_write_token_count` is reported but has been 0 on every event since 2026-08-24. |
| Completed operations | omp | One per token-bearing chat span, including subagents and advisors. |
| Completed operations | Codex app-server | One per token-bearing `response.completed` event. |
| Completed operations | Codex CLI | One per token-bearing `response.completed` event. |
| Completed operations | Codex exec | One per token-bearing `response.completed` event. |
| Completed operations | Claude Code | One per `api_request` log. |
| Requested effort | Claude Code | `effort` on LLM requests: set on nearly every request from 2026-09-28, occasionally before, so older tasks are mostly `unset`. |
| Tasks | omp | A root agent run, including its subagents and advisors. |
| Tasks | Codex app-server | A turn. `codex-auto-review` turns are excluded; subagent threads appear as separate tasks. |
| Tasks | Codex CLI | A turn. `codex-auto-review` turns are excluded; subagent threads appear as separate tasks. |
| Tasks | Codex exec | A turn. `codex-auto-review` turns are excluded; subagent threads appear as separate tasks. |
| Tasks | Claude Code | An interaction with model calls; interactions without model calls are slash commands and are excluded. |
| Heavy-effort task share | Claude Code | Effort is mostly unset before 2026-09-28; unset tasks count as not heavy. |
| Model steps per task | omp | Every chat span in the run's trace, including subagents and advisors. |
| Model steps per task | Codex app-server | `run_sampling_request` spans whose `turn_id` is the task. |
| Model steps per task | Codex CLI | `run_sampling_request` spans whose `turn_id` is the task. |
| Model steps per task | Codex exec | `run_sampling_request` spans whose `turn_id` is the task. |
| Model steps per task | Claude Code | `claude_code.llm_request` spans in the interaction's trace. |
| Reasoning tokens per task | Codex app-server | `codex.turn.token_usage.reasoning_output_tokens`; Codex CLI TUI turns often omit token usage. |
| Reasoning tokens per task | Codex CLI | `codex.turn.token_usage.reasoning_output_tokens`; Codex CLI TUI turns often omit token usage. |
| Reasoning tokens per task | Codex exec | `codex.turn.token_usage.reasoning_output_tokens`; Codex CLI TUI turns often omit token usage. |
| Tool failure rate | Codex app-server | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Tool failure rate | Codex CLI | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Tool failure rate | Codex exec | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Failure signature | Claude Code | `error_type` only: an error class, not the message. |
| Tasks affected by failures | Codex app-server | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Tasks affected by failures | Codex CLI | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Tasks affected by failures | Codex exec | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Interrupted tasks | omp | The run has an aborted chat stop reason (`stop_reason.aborted.count > 0`). |
| Interrupted tasks | Codex exec | Headless: no user can interrupt, so the value is always 0. |
| Steered tasks | Codex exec | Headless: no user can steer, so the value is always 0. |
| Errored tasks | Claude Code | Any LLM request in the interaction with an error or status ≥ 400. |
| Clean completion | Codex app-server | Observes interrupt and follow-up; task errors are not emitted. |
| Clean completion | Codex CLI | Observes interrupt and follow-up; task errors are not emitted. |
| Clean completion | Claude Code | Observes error and follow-up; interrupts are not emitted. |
| Recovery after failure | omp | Operation = tool name only (no argument identity). |
| Recovery after failure | Codex app-server | Operation = tool + command head + subcommand. |
| Recovery after failure | Codex CLI | Operation = tool + command head + subcommand. |
| Recovery after failure | Codex exec | Operation = tool + command head + subcommand. |
| Recovery after failure | Claude Code | Operation = tool name + bash argv0. |
| Command churn | Codex app-server | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Command churn | Codex CLI | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Command churn | Codex exec | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Model calls | Codex app-server | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Model calls | Codex CLI | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Model calls | Codex exec | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Call error rate | Codex app-server | Stream level: a non-empty `error.message` on `response.completed`. |
| Call error rate | Codex CLI | Stream level: a non-empty `error.message` on `response.completed`. |
| Call error rate | Codex exec | Stream level: a non-empty `error.message` on `response.completed`. |
| Model call latency | Codex app-server | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Model call latency | Codex CLI | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Model call latency | Codex exec | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Retries | Codex app-server | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Retries | Codex CLI | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Retries | Codex exec | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Unknown outcomes | Codex app-server | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Unknown outcomes | Codex CLI | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Unknown outcomes | Codex exec | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Recurring targets | Codex app-server | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Recurring targets | Codex CLI | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Recurring targets | Codex exec | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Recurring failure signatures | Claude Code | Signature is the `error_type` class only. |
| Actionable repeats | Claude Code | Signature is the `error_type` class only. |

## Signals no producer supports

- **Rule adherence** (Interventions): Unsupported: needs a versioned rule registry with deterministic triggers and observable required actions. No producer emits rule triggers.
- **Uncodified practice recurrence** (Interventions): Unsupported: needs ordered operations with an explicit successful task outcome. No producer emits explicit task success, and clean completion is a friction proxy, not success.

<!-- END GENERATED FROM signal_support.toml -->

## Gaps in coverage

These signals are emitted, but not for every row.

| Gap | Harnesses | Extent | Effect |
| --- | --- | --- | --- |
| Tool call → task attribution | Codex | 93% of app-server, 99.7% of exec, and 54% of CLI tool calls fall inside an exported turn span | Task-level tool signals (tasks affected, repeats, loops, recovery, churn, recurrence) undercount Codex CLI. Some CLI turns are never exported as `session_task.turn` (F13). |
| Task token usage | Codex CLI | TUI turns often omit `codex.turn.token_usage.*` | Per-task reasoning and output are below usage totals (55% lower over 30 days). |
| Project attribution | All | 98% Codex exec, 97% Codex CLI, 82% Codex app-server, 72% Claude Code, 54% omp of 90-day tasks have a hook-recorded project | Project concentration and project breakdowns cover only attributed sessions. Sessions in non-Git workspaces are rejected by the hooks. |
| Reasoning effort | Claude Code | `effort` set on nearly every request from 2026-09-28, rarely before | Older Claude Code tasks show `unset`. |
| Cache writes | Codex | `cache_write_token_count` is reported but has been 0 on every event | Cache creation shows 0 for Codex rather than a measured value. |
| Response model | Codex, Claude Code | No served-model field | Model conformance covers omp only. |
| Rare user events | Codex CLI, Codex exec | No `turn/interrupt` in 90 days; exec is headless and cannot be interrupted or steered | Interrupt and steer rates are 0 or "no events" there, which is a valid observation, not a break. |

## Gaps in history

| Gap | Extent | Recoverable |
| --- | --- | --- |
| Before SigNoz's retained data | Raw traces and logs start on 2026-08-24 | No. |
| 2026-09-06 to 2026-09-12 | Missing from SigNoz (expired under the earlier 15-day trace TTL and not in the recovery snapshot); 2026-09-10 is partial | No. |
| Workflow findings before 2026-09-29 | The retired scan detectors produced 6 findings (2026-08-06); promotion from the facts began on 2026-09-29 | The retired ledger is archived at `/Volumes/UGreen-External/archive/agent-introspection-2026-09-28/`. |

## Gaps in the source telemetry

- **No explicit task success.** No producer says whether a task achieved its goal.
  Clean completion is a friction proxy, and rule adherence and practice recurrence are
  unsupported.
- **No logical request identity.** omp has no attempt or retry identity; Codex
  exposes attempts only through `try_run_sampling_request` spans; Claude Code has an
  `attempt` number. Provider measures are per call, not per logical request.
- **No comparable cost.** omp's estimated cost is always 0 and Codex reports none, so
  spend is measured in tokens.
- **Mislabelled sessions.** Claude Code sessions exported as `oh-my-pi` on 2026-08-24,
  08-28, 09-01, 09-02, and 09-13, and Docker build spans under `oh-my-pi`, count for
  neither harness (F5).
- **Codex Desktop hook boundary.** The Codex global session hooks also run in Codex
  CLI and cannot tell the two apart, so the Desktop hook adapter is not installed
  in a configuration root shared with Codex CLI.
