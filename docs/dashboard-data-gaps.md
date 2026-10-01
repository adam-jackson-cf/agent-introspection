# Dashboard Data Gaps

What the dashboard cannot show, or shows only partly, and why. The sections from
"Harness support matrix" through "Excluded signals" are generated from
[`signal_support.toml`](../src/agent_introspection/facts_sql/signal_support.toml) by
`scripts/render_measures.py`, and a test fails when it drifts. The sections after it
record gaps in the data itself. Definitions and routes are in
[Dashboard Measure v3](dashboard-measure-v3.md); how hooks close native gaps is in
[Hook Events](hook-events.md); the evidence for each finding is in the
[Dashboard v3 Plan](dashboard-v3-plan.md#findings-log).

**Parity rule.** Every dashboard signal is produced by every harness it can exist for,
natively or through an activity hook. A signal that some harness cannot produce is
excluded, never shown as "not emitted", and `facts install` rejects a registry that
breaks the rule.

Legend: ✓ the harness reaches the shared definition directly; ≈ it reaches it by a
different route (see its note); — the signal cannot exist for that harness by
construction (headless Codex exec has no user to interrupt, steer, or follow up).

<!-- BEGIN GENERATED FROM signal_support.toml -->

## Harness support matrix

51 per-harness signals across 5 harnesses: 255 cells, 157 aligned (✓), 91 reached by a different route (≈), 7 not applicable by construction (—). Every signal is reached by every harness it can exist for; a signal some harness cannot produce is excluded (below).

| View | Signal | omp | Codex app-server | Codex CLI | Codex exec | Claude Code |
| --- | --- | --- | --- | --- | --- | --- |
| Pipeline | Fact freshness | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pipeline | Source parity | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pipeline | Project attribution | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Input tokens | ✓ | ✓ | ✓ | ✓ | ≈ |
| Cache efficiency | Cached input | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Uncached input | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Output tokens | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Input cache utilization | ✓ | ✓ | ✓ | ✓ | ✓ |
| Cache efficiency | Completed operations | ≈ | ≈ | ≈ | ≈ | ≈ |
| Cache efficiency | Usage-active sessions | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reasoning effort | Reasoning tokens | ✓ | ✓ | ✓ | ✓ | ≈ |
| Reasoning effort | Reasoning share of output | ✓ | ✓ | ✓ | ✓ | ≈ |
| Reasoning effort | Requested effort | ✓ | ✓ | ✓ | ✓ | ≈ |
| Reasoning effort | Tasks | ≈ | ≈ | ≈ | ≈ | ≈ |
| Reasoning effort | Heavy-effort task share | ✓ | ✓ | ✓ | ✓ | ≈ |
| Reasoning effort | Task duration | ✓ | ✓ | ✓ | ✓ | ✓ |
| Reasoning effort | Model steps per task | ≈ | ≈ | ≈ | ≈ | ≈ |
| Reasoning effort | Reasoning tokens per task | ✓ | ≈ | ≈ | ≈ | ≈ |
| Reasoning effort | Delegations per task | ✓ | ≈ | ≈ | ≈ | ✓ |
| Tool failures | Tool calls | ✓ | ✓ | ✓ | ✓ | ✓ |
| Tool failures | Tool failure rate | ✓ | ≈ | ≈ | ≈ | ✓ |
| Tool failures | Failure signature | ✓ | ✓ | ✓ | ✓ | ≈ |
| Tool failures | Tasks affected by failures | ✓ | ≈ | ≈ | ≈ | ✓ |
| Tool failures | Repeated attempts | ≈ | ✓ | ✓ | ✓ | ≈ |
| Tool failures | Tool loops | ≈ | ✓ | ✓ | ✓ | ≈ |
| Friction | Interrupted tasks | ≈ | ✓ | ✓ | — | ≈ |
| Friction | Steered tasks | ≈ | ✓ | ✓ | — | ≈ |
| Friction | Errored tasks | ✓ | ≈ | ≈ | ≈ | ≈ |
| Friction | Quick follow-up | ✓ | ✓ | ✓ | — | ✓ |
| Friction | Clean completion | ✓ | ≈ | ≈ | — | ≈ |
| Friction | Recovery after failure | ≈ | ≈ | ≈ | ≈ | ≈ |
| Guardrails | Tool approval decisions | ≈ | ✓ | ✓ | ✓ | ✓ |
| Guardrails | Rejected tool calls | ≈ | ✓ | ✓ | ✓ | ✓ |
| Guardrails | Quality-gate bypass | ✓ | ✓ | ✓ | ✓ | ✓ |
| Guardrails | Command churn | ≈ | ≈ | ≈ | ≈ | ≈ |
| Provider | Model calls | ✓ | ≈ | ≈ | ≈ | ✓ |
| Provider | Call error rate | ✓ | ≈ | ≈ | ≈ | ✓ |
| Provider | Model call latency | ✓ | ≈ | ≈ | ≈ | ✓ |
| Provider | Time to first token | ✓ | ✓ | ✓ | ✓ | ✓ |
| Provider | Retries | ≈ | ≈ | ≈ | ≈ | ✓ |
| Provider | Unknown outcomes | ✓ | ≈ | ≈ | ≈ | ✓ |
| Recurrence | Recurring targets | ≈ | ≈ | ≈ | ≈ | ≈ |
| Recurrence | Recurring failure signatures | ✓ | ✓ | ✓ | ✓ | ≈ |
| Recurrence | Actionable repeats | ✓ | ✓ | ✓ | ✓ | ≈ |
| Recurrence | Project concentration | ✓ | ✓ | ✓ | ✓ | ✓ |
| Intent and corrections | Labelled tasks | ≈ | ✓ | ✓ | ✓ | ✓ |
| Intent and corrections | Task type | ≈ | ✓ | ✓ | ✓ | ✓ |
| Intent and corrections | Corrected by the next prompt | ≈ | ✓ | ✓ | — | ✓ |
| Intent and corrections | Correction kinds | ≈ | ✓ | ✓ | — | ✓ |
| Intent and corrections | Frustrated follow-ups | ≈ | ✓ | ✓ | — | ✓ |
| Intent and corrections | Effort payoff by task type | ≈ | ✓ | ✓ | ≈ | ✓ |

## Signals not applicable to a harness

| Signal | Harness | Why |
| --- | --- | --- |
| Interrupted tasks | Codex exec | Headless: no user can interrupt. |
| Steered tasks | Codex exec | Headless: no user can steer. |
| Quick follow-up | Codex exec | Headless one-shot runs: nobody can follow up. |
| Clean completion | Codex exec | Headless: follow-up is unobservable, so clean completion is NULL. |
| Corrected by the next prompt | Codex exec | Headless one-shot runs have no next prompt. |
| Correction kinds | Codex exec | Headless one-shot runs have no next prompt. |
| Frustrated follow-ups | Codex exec | Headless one-shot runs have no next prompt. |

## Signals reached by a different route

These are emitted, but the route differs from the shared definition; the note says how.

| Signal | Harness | How it differs |
| --- | --- | --- |
| Input tokens | Claude Code | `input_tokens + cache_creation_tokens + cache_read_tokens`; the direct field excludes cache, so the three are summed. |
| Completed operations | omp | One per token-bearing chat span, including subagents and advisors. |
| Completed operations | Codex app-server | One per token-bearing `response.completed` event. |
| Completed operations | Codex CLI | One per token-bearing `response.completed` event. |
| Completed operations | Codex exec | One per token-bearing `response.completed` event. |
| Completed operations | Claude Code | One per `api_request` log. |
| Reasoning tokens | Claude Code | Per turn, not per call: the `Stop` hook sums `thinking_tokens` over the turn's assistant messages in the transcript. From the activity hooks' install date; earlier rows have no value. |
| Reasoning share of output | Claude Code | Per turn: thinking tokens from the `Stop` hook over the turn's output tokens. From the activity hooks' install date; earlier rows have no value. |
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
| Reasoning tokens per task | Claude Code | Thinking tokens summed over the turn's assistant messages by the `Stop` hook. From the activity hooks' install date; earlier rows have no value. |
| Delegations per task | Codex app-server | Subagent spawns (`codex.agent_communication`) sent from the turn's thread during the turn; the subagent threads still also appear as separate tasks. |
| Delegations per task | Codex CLI | Subagent spawns (`codex.agent_communication`) sent from the turn's thread during the turn; the subagent threads still also appear as separate tasks. |
| Delegations per task | Codex exec | Subagent spawns (`codex.agent_communication`) sent from the turn's thread during the turn; the subagent threads still also appear as separate tasks. |
| Tool failure rate | Codex app-server | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Tool failure rate | Codex CLI | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Tool failure rate | Codex exec | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Failure signature | Claude Code | Most specific diagnostic line of the `PostToolUseFailure` error; before the hooks, and when the hook has no error text, the `error_type` class. |
| Tasks affected by failures | Codex app-server | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Tasks affected by failures | Codex CLI | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Tasks affected by failures | Codex exec | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Repeated attempts | omp | Hash of the canonical tool arguments from the `tool_call` hook, joined by tool call ID. From the activity hooks' install date; earlier rows have no value. |
| Repeated attempts | Claude Code | Hash of the canonical `tool_input` from the `PreToolUse` hook, joined by `tool_use_id`. From the activity hooks' install date; earlier rows have no value. |
| Tool loops | omp | Argument hash from the `tool_call` hook. From the activity hooks' install date; earlier rows have no value. |
| Tool loops | Claude Code | Argument hash from the `PreToolUse` hook. From the activity hooks' install date; earlier rows have no value. |
| Interrupted tasks | omp | The run has an aborted chat stop reason (`stop_reason.aborted.count > 0`). |
| Interrupted tasks | Claude Code | Inferred: the task's prompt reached the `UserPromptSubmit` hook but no `Stop` or `StopFailure` followed (Claude Code runs no Stop hook on a user interrupt). From the activity hooks' install date; earlier rows have no value. |
| Steered tasks | omp | The `input` hook fired while the run was active. From the activity hooks' install date; earlier rows have no value. |
| Steered tasks | Claude Code | Inferred: another prompt reached the `UserPromptSubmit` hook while the interaction was running. From the activity hooks' install date; earlier rows have no value. |
| Errored tasks | Codex app-server | Inferred: the last response stream of the turn failed (`error.message` on `response.completed`); turn spans carry no terminal status. |
| Errored tasks | Codex CLI | Inferred: the last response stream of the turn failed (`error.message` on `response.completed`); turn spans carry no terminal status. |
| Errored tasks | Codex exec | Inferred: the last response stream of the turn failed (`error.message` on `response.completed`); turn spans carry no terminal status. |
| Errored tasks | Claude Code | Any LLM request in the interaction with an error or status ≥ 400. |
| Clean completion | Codex app-server | Observes interrupt, follow-up, and the inferred error (last response stream failed). |
| Clean completion | Codex CLI | Observes interrupt, follow-up, and the inferred error (last response stream failed). |
| Clean completion | Claude Code | Observes error and follow-up, and the inferred interrupt once the activity hooks cover the session. |
| Recovery after failure | omp | Operation = tool + command head + subcommand from the `tool_call` hook; tool name only before the hooks. |
| Recovery after failure | Codex app-server | Operation = tool + command head + subcommand. |
| Recovery after failure | Codex CLI | Operation = tool + command head + subcommand. |
| Recovery after failure | Codex exec | Operation = tool + command head + subcommand. |
| Recovery after failure | Claude Code | Operation = tool + command head + subcommand from the `PreToolUse` hook; tool + bash argv0 before the hooks. |
| Tool approval decisions | omp | `tool_approval_resolved` hook (approved or rejected, source user); omp's default mode approves without asking, so decisions are rare. |
| Rejected tool calls | omp | `tool_approval_resolved` with approved = false. |
| Command churn | omp | Targets from the `tool_call` hook (edit paths, `path` arguments, path-like command arguments). From the activity hooks' install date; earlier rows have no value. |
| Command churn | Codex app-server | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Command churn | Codex CLI | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Command churn | Codex exec | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Command churn | Claude Code | Targets from the `PreToolUse` hook (`file_path`, edit paths, path-like command arguments). From the activity hooks' install date; earlier rows have no value. |
| Model calls | Codex app-server | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Model calls | Codex CLI | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Model calls | Codex exec | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Call error rate | Codex app-server | Stream level: a non-empty `error.message` on `response.completed`. |
| Call error rate | Codex CLI | Stream level: a non-empty `error.message` on `response.completed`. |
| Call error rate | Codex exec | Stream level: a non-empty `error.message` on `response.completed`. |
| Model call latency | Codex app-server | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Model call latency | Codex CLI | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Model call latency | Codex exec | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Retries | omp | `auto_retry_start` hook events: one per retry of a failed chat. From the activity hooks' install date; earlier rows have no value. |
| Retries | Codex app-server | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Retries | Codex CLI | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Retries | Codex exec | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Unknown outcomes | Codex app-server | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Unknown outcomes | Codex CLI | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Unknown outcomes | Codex exec | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Recurring targets | omp | Targets from the `tool_call` hook, home as ~. From the activity hooks' install date; earlier rows have no value. |
| Recurring targets | Codex app-server | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Recurring targets | Codex CLI | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Recurring targets | Codex exec | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Recurring targets | Claude Code | Targets from the `PreToolUse` hook, home as ~. From the activity hooks' install date; earlier rows have no value. |
| Recurring failure signatures | Claude Code | Most specific diagnostic line from the `PostToolUseFailure` hook; the `error_type` class before the hooks. |
| Actionable repeats | Claude Code | Most specific diagnostic line from the `PostToolUseFailure` hook; the `error_type` class before the hooks. |
| Labelled tasks | omp | omp exports no prompt event; the activity extension sends `omp.user_prompt` on `before_agent_start`. Labelled from the prompt export's start (2026-09-30); earlier prompts were exported redacted. |
| Task type | omp | Prompt event sent by the omp activity extension. |
| Corrected by the next prompt | omp | Prompt event sent by the omp activity extension. |
| Correction kinds | omp | Prompt event sent by the omp activity extension. |
| Frustrated follow-ups | omp | Prompt event sent by the omp activity extension. |
| Effort payoff by task type | omp | Prompt event sent by the omp activity extension. |
| Effort payoff by task type | Codex exec | Task type only; corrections and clean completion are not applicable to headless runs. |

## Excluded signals

At least one harness can produce these neither natively nor through an activity
hook, so they are left off the dashboard.

| Signal | View | Missing for | Reason |
| --- | --- | --- | --- |
| Cache creation (`usage.cache_creation`) | Cache efficiency | Codex app-server, Codex CLI, Codex exec | Codex reports `cache_write_token_count`, but it has been 0 on every event since 2026-08-24, and no hook sees cache writes. |
| Sandbox outcomes (`guard.sandbox`) | Guardrails | omp | omp has no sandbox, so there is no outcome to observe. |
| Stream disconnects (`provider.stream_disconnects`) | Provider | omp, Claude Code | omp chat spans and Claude Code requests record errors but not stream state; guessing from error text would not be the same signal. |
| Output throughput (`provider.throughput`) | Provider | Codex app-server, Codex CLI, Codex exec | Codex completed responses carry time to first token but no per-call duration; a per-turn rate would be a different signal. |
| Model conformance (`provider.model_conformance`) | Provider | Codex app-server, Codex CLI, Codex exec | Codex reports only the requested model: neither its telemetry, its hooks, nor its transcript name the served model. |
| Rule adherence (`intervene.rule_adherence`) | Interventions | omp, Codex app-server, Codex CLI, Codex exec, Claude Code | No producer emits rule triggers. Each applied intervention is instead evaluated against its own structured success metric (Post-intervention recurrence). |

<!-- END GENERATED FROM signal_support.toml -->

## Gaps in coverage

These signals are emitted, but not for every row.

| Gap | Harnesses | Extent | Effect |
| --- | --- | --- | --- |
| Tool call → task attribution | Codex | 93% of app-server, 99.7% of exec, and 54% of CLI tool calls fall inside an exported turn span | Task-level tool signals (tasks affected, repeats, loops, recovery, churn, recurrence) undercount Codex CLI. Some CLI turns are never exported as `session_task.turn` (F13). |
| Task token usage | Codex CLI | TUI turns often omit `codex.turn.token_usage.*` | Per-task reasoning and output are below usage totals (55% lower over 30 days). |
| Project attribution | All | 98% Codex exec, 97% Codex CLI, 82% Codex app-server, 72% Claude Code, 54% omp of 90-day tasks have a hook-recorded project | Project concentration and project breakdowns cover only attributed sessions. Sessions in non-Git workspaces are rejected by the hooks. |
| Reasoning effort | Claude Code | `effort` set on nearly every request from 2026-09-28, rarely before | Older Claude Code tasks show `unset`. |
| Rare user events | Codex CLI, Codex exec | No `turn/interrupt` in 90 days; exec is headless and cannot be interrupted or steered | Interrupt and steer rates are 0 or "no events" there, which is a valid observation, not a break. |

## Gaps from the activity hooks

| Gap | Harnesses | Extent | Effect |
| --- | --- | --- | --- |
| Hook history starts at install | omp, Claude Code | Hook events exist from 2026-09-30 13:40 UTC | Hook-routed signals (argument identity, targets, gate bypass, Claude interrupts, steers, reasoning, delegations) are NULL before then, never 0. |
| Prompt labels start at export | All | Producers exported prompt text from 2026-09-30 15:20 UTC (Claude Code and Codex sessions started after their setting changed; omp sessions started after the extension was registered); earlier prompts are a `REDACTED` placeholder | Intent signals are empty before then; a long-running session keeps exporting placeholders until it restarts. |
| Claude Code interrupts and steers | Claude Code | Inferred from hook timing: no `Stop` after the prompt (interrupt); a prompt submitted while the interaction runs (steer) | Not live-tested yet; a queued prompt may start a new interaction instead of counting as a steer. |
| omp sessions started before registration | omp | Long-running omp sessions load extensions at start | Their calls carry no hook fields until the session restarts. |

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
- **No comparable cost.** Claude Code reports `cost_usd` on every request and omp
  reports an estimated cost on judgment spans since 2026-09-29, but Codex reports
  none, so under the parity rule spend is measured in tokens.
- **Mislabelled sessions.** Claude Code sessions exported as `oh-my-pi` on 2026-08-24,
  08-28, 09-01, 09-02, and 09-13, and Docker build spans under `oh-my-pi`, count for
  neither harness (F5).
- **Codex Desktop hook boundary.** The Codex global session hooks also run in Codex
  CLI and cannot tell the two apart, so the Desktop hook adapter is not installed
  in a configuration root shared with Codex CLI.
