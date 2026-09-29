# Dashboard Measure v3

Source of truth: [`signal_support.toml`](../src/agent_introspection/facts_sql/signal_support.toml),
loaded by `agent-introspection facts install` into `introspection.signals`,
`signal_routes`, `signal_support`, and `signal_strays`. The fact views are in
[`003_views.sql`](../src/agent_introspection/facts_sql/003_views.sql). Sections 3
and 4 are generated from the registry by `scripts/render_measures.py`; a test fails
when they drift. The design and phase history are in the
[Dashboard v3 Plan](dashboard-v3-plan.md). This document supersedes
[Measure v2](retired/dashboard-measure-v2.md).

This document records the producer routes and calculations behind every dashboard
signal. Use the exact routes and fields below when building or changing a view;
similarly named fields from different producers are not interchangeable.

## 1. Scope and principles

The dashboard surfaces **the same signal from every harness that has it**. It is not
a benchmark: harnesses are never ranked or compared against each other.

- **One definition per signal.** Each signal has one question, unit, and formula.
- **One route per producer.** Each harness reaches the signal by its own route and is
  `aligned`, `differs` (the note says how), or `not emitted` (the note says why).
  A harness that does not emit a signal is shown as not emitted, never as zero.
- **All is the union of each.** "All harnesses" aggregates the per-harness rows of the
  same signal. Counts are summed; ratios sum numerators and denominators before
  dividing (token-weighted, never an average of percentages). The Pipeline view checks
  that per-harness values recombine to the All value.
- **Composites keep the shared definition.** Clean completion is "no observed
  friction"; each task row records which friction components its producer could
  observe (`friction_observed`), and the info note states them.
- **NULL means no signal.** A missing field stays NULL and is excluded from
  denominators; it is never counted as zero.
- **Privacy (D4).** Raw prompt, command, argument, output, and tool-intent text and
  identity keys are never stored. Only normalized `x.*` fields, digit-normalized
  failure signatures, and home-redacted paths are kept.
- **Time.** Trends use UTC days; actionable repeats use a 7-day Europe/London window.

Harnesses: omp (`oh-my-pi`), Codex app-server (`codex-app-server`), Codex CLI
(`codex_cli_rs`), Codex exec (`codex_exec`), and Claude Code (`claude-code`).

## 2. Normalized fact rows

Every view reads snapshot tables (`<view>_snapshot`) of these fact views, refreshed
right after each minute load.

### 2.1 `usage_events`: one row per accepted model usage record

| Field | Meaning |
| --- | --- |
| `ts`, `harness`, `session_id` | Event time, producer, harness-scoped native session. |
| `provider`, `model`, `response_model` | Normalized provider, requested model, served model (omp only). |
| `effort` | Requested reasoning effort; `unset` means the provider default. |
| `input_tokens` | Total input including cache reads (and, for Claude Code, cache creation). |
| `cached_input` | Cache-read input only. |
| `cache_creation_input` | Input written to cache; always 0 for Codex, rare for omp. |
| `output_tokens`, `reasoning_tokens` | Output including reasoning; reasoning is NULL for Claude Code. |

Routes: Codex token-bearing `codex.sse_event`; Claude Code `api_request`; omp `chat`
spans with non-zero usage. The Codex and Claude Code branches, and the omp chat
boundary, match agent-observability's validated `usage_events` exactly.

### 2.2 `task_outcomes`: one row per user-initiated task

| Field | Meaning |
| --- | --- |
| `start_ts`, `end_ts`, `duration_seconds` | Task span bounds. |
| `harness`, `session_id`, `task_id` | Harness-scoped identity: Codex turn ID, omp root run span, Claude Code interaction span. |
| `model`, `effort` | Task model and requested effort (`mixed` when a task used more than one). |
| `output_tokens`, `reasoning_tokens`, `model_steps`, `delegations` | Task totals; delegations only for omp. |
| `interrupted`, `steered`, `errored` | 1 when the user interrupted or steered, or the task ended on an error; NULL when not emitted. |
| `quick_follow_up` | 1 when the next task in the same session starts within 10 minutes of this end; NULL for `codex_exec`. |
| `clean_completion` | 1 when no observed friction; NULL for `codex_exec`. |
| `friction_observed` | The components (`interrupt`, `error`, `follow_up`) this row could observe. |

`codex-auto-review` turns (the automated approval reviewer) and Claude Code
interactions without model calls (slash commands) are not tasks.

### 2.3 `tool_calls`: one row per tool invocation

| Field | Meaning |
| --- | --- |
| `ts`, `harness`, `session_id`, `call_id`, `tool` | Identity. |
| `outcome` | `succeeded`, `failed`, `aborted` (omp), or `unknown`. Codex reports `success=true` on non-zero exits, so the exit code wins. |
| `exit_code`, `duration_ms` | Codex exit code; duration. |
| `command_head`, `command_sub` | Codex normalized command (also from JavaScript `exec` code); Claude Code `bash_argv0` and command class. |
| `gate_bypass` | Codex: `--no-verify`, `HUSKY=0`, `SKIP=`, or `--no-gpg-sign` anywhere in the arguments. |
| `targets` | Codex files: patch headers, `path` arguments, path-like command arguments (inferred); home as `~`. |
| `failure_signature` | Normalized first error line (Codex, omp) or error class (Claude Code). |
| `arguments_hash` | Codex argument identity for repeats and loops. |
| `task_id` | Codex: the user turn in the session whose span contains the call; omp: root run of the trace; Claude Code: interaction of the trace. Empty when unattributed. |
| `workdir` | Codex working directory, home as `~`. |

### 2.4 `user_signals`: interrupts, steers, approval decisions, sandbox outcomes, prompts

Codex `turn/interrupt` and `turn/steer` spans, Codex and Claude Code tool decisions,
Codex sandbox outcomes, prompt lengths (never text), and omp aborted tool calls.

### 2.5 `model_calls`: one row per terminal model call

Codex `response.completed` streams (failed streams carry `error.message`), omp chat
spans, and Claude Code LLM request spans. `outcome` is `succeeded`, `failed`,
`cancelled` (a user-aborted omp chat; not a provider failure), or `unknown`; plus
`error_class`, `duration_seconds`, `ttft_seconds`, `output_tokens`, `attempt`, and
`stream_disconnect`.

### 2.6 `session_project`: one project per session

The latest Git project the session-context hooks recorded for a session, from
`introspection.session_projects`. Session IDs are producer-native UUIDs and join the
facts without the harness.

<!-- BEGIN GENERATED FROM signal_support.toml -->

## 3. Producer routes

Each route names the rows of one or more harnesses in `introspection.spans` or
`introspection.logs` that carry a signal. For an `events` route, no rows while
the harness is otherwise active is a valid observation.

| Route | Harnesses | Source | Expect | Predicate | Description |
| --- | --- | --- | --- | --- | --- |
| `codex.usage` | Codex app-server, Codex CLI, Codex exec | logs | rows | `event_name = 'codex.sse_event' AND (mapContains(attrs_number, 'input_token_count') OR mapContains(attrs_string, 'input_token_count'))` | Token-bearing `codex.sse_event` log (`response.completed`), one per completed response. |
| `claude.usage` | Claude Code | logs | rows | `event_name = 'api_request'` | `api_request` log, one per completed API request. |
| `omp.chat` | omp | spans | rows | `attrs_string['gen_ai.operation.name'] = 'chat'` | `chat` span (`gen_ai.operation.name = 'chat'`), one per model call, including subagents and advisors. |
| `codex.turn` | Codex app-server, Codex CLI, Codex exec | spans | rows | `name = 'session_task.turn'` | `session_task.turn` span, one per turn; `codex-auto-review` turns are the automated approval reviewer. |
| `omp.run` | omp | spans | rows | `name = 'invoke_agent' AND parent_span_id = ''` | Root `invoke_agent` span, one per user-initiated agent run. |
| `claude.interaction` | Claude Code | spans | rows | `name = 'claude_code.interaction'` | `claude_code.interaction` span; a task only when its trace has `claude_code.llm_request` spans (otherwise a slash command). |
| `claude.llm_request` | Claude Code | spans | rows | `name = 'claude_code.llm_request'` | `claude_code.llm_request` span, one per terminal LLM request. |
| `codex.interrupt` | Codex app-server, Codex CLI, Codex exec | spans | events | `name = 'turn/interrupt'` | `turn/interrupt` RPC span carrying the interrupted `turn.id`. |
| `codex.steer` | Codex app-server, Codex CLI, Codex exec | spans | events | `name = 'turn/steer'` | `turn/steer` RPC span carrying the steered `turn.id`. |
| `omp.subagent` | omp | spans | events | `name LIKE 'invoke_agent %' AND name NOT LIKE 'invoke_agent Advisor:%'` | `invoke_agent <agent>` span for a delegated subagent run (advisors excluded). |
| `codex.tool_result` | Codex app-server, Codex CLI, Codex exec | logs | rows | `event_name = 'codex.tool_result'` | `codex.tool_result` log with normalized `x.*` command fields; raw arguments and output are not stored. |
| `omp.execute_tool` | omp | spans | rows | `name LIKE 'execute_tool %'` | `execute_tool <tool>` span with `pi.gen_ai.tool.status` ok/error/aborted. |
| `claude.tool_result` | Claude Code | logs | rows | `event_name = 'tool_result'` | `tool_result` log with `success` and `error_type`. |
| `codex.tool_decision` | Codex app-server, Codex CLI, Codex exec | logs | rows | `event_name = 'codex.tool_decision'` | `codex.tool_decision` log with `decision` and `source` (config or user). |
| `claude.tool_decision` | Claude Code | logs | rows | `event_name = 'tool_decision'` | `tool_decision` log with `decision` and `source`. |
| `codex.sandbox_outcome` | Codex app-server, Codex CLI, Codex exec | logs | events | `event_name = 'codex.sandbox_outcome'` | `codex.sandbox_outcome` log with `outcome` for a sandboxed command. |
| `codex.sampling` | Codex app-server, Codex CLI, Codex exec | spans | rows | `name = 'run_sampling_request'` | `run_sampling_request` span: one sampling step including retries, response processing, and in-flight tool draining. |
| `codex.try_sampling` | Codex app-server, Codex CLI, Codex exec | spans | rows | `name = 'try_run_sampling_request'` | `try_run_sampling_request` span: one attempt inside a sampling step. |
| `codex.response_completed` | Codex app-server, Codex CLI, Codex exec | logs | rows | `event_name = 'codex.sse_event' AND attrs_string['event.kind'] = 'response.completed'` | `codex.sse_event` with `event.kind = 'response.completed'`: one per response stream, carrying `ttft_ms`, output tokens, and `error.message` when the stream fails. |
| `claude.api_error` | Claude Code | logs | events | `event_name IN ('api_error', 'api_retries_exhausted')` | `api_error` (one per failed attempt) and `api_retries_exhausted` logs. |

### Registered strays

| Stray | Harness | Source | Predicate | Explanation |
| --- | --- | --- | --- | --- |
| `omp.claude_mislabel_logs` | omp | logs | `event_name IN ('api_request', 'tool_result', 'tool_decision', 'user_prompt', 'assistant_response', 'hook_registered', 'hook_execution_start', 'hook_execution_complete', 'mcp_server_connection', 'retention_sweep')` | Claude Code sessions exported with service.name oh-my-pi (seen 2026-08-24, 08-28, 09-01, 09-02, 09-13). Inferred: Claude Code launched from an omp shell inherits OTEL_SERVICE_NAME. The rows are Claude-style events, not an omp route, and are not counted as Claude Code either. |
| `omp.claude_mislabel_spans` | omp | spans | `startsWith(name, 'claude_code.')` | Spans from the same mislabelled Claude Code sessions. |
| `omp.docker_build` | omp | spans | `name LIKE 'moby.%' OR name LIKE 'build%' OR name LIKE 'load %' OR name LIKE 'remotes.docker.%' OR name IN ('HEAD /_ping', 'GET /v1.54/version', 'HTTP POST')` | Inferred: Docker buildx spans inherit OTEL_SERVICE_NAME=oh-my-pi when omp runs docker builds. They are not agent telemetry. |

## 4. Signal catalog

### Pipeline

#### Loader refresh (`pipeline.loader_refresh`)

- **Question:** Are the refreshable loaders and snapshots running without errors?
- **Unit:** status
- **Formula:** system.view_refreshes status, last success time, and exception for every view in the introspection database.

Scope: system (not per harness).

#### Fact freshness (`pipeline.freshness`)

- **Question:** How far behind SigNoz is each harness's fact data?
- **Unit:** seconds
- **Formula:** Per harness and source: max(ts) in SigNoz minus max(ts) in introspection over the last day. Rows for idle harnesses report their last activity.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | — |
| Codex app-server | aligned | `codex.turn` | — |
| Codex CLI | aligned | `codex.turn` | — |
| Codex exec | aligned | `codex.turn` | — |
| Claude Code | aligned | `claude.llm_request` | — |

#### Source parity (`pipeline.parity`)

- **Question:** Does usage_events equal a direct recount of the raw SigNoz source for the window?
- **Unit:** tokens
- **Formula:** Per harness: Σ input, cached, output tokens and operations from usage_events versus the same fields summed directly from signoz_logs / signoz_traces with the producer's usage route.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | — |
| Codex app-server | aligned | `codex.usage` | — |
| Codex CLI | aligned | `codex.usage` | — |
| Codex exec | aligned | `codex.usage` | — |
| Claude Code | aligned | `claude.usage` | — |

#### Project attribution (`pipeline.project_attribution`)

- **Question:** Which share of each harness's tasks has a project from the session-context hooks?
- **Unit:** %
- **Formula:** Per harness: 100 × tasks whose session the hooks recorded with a git project / tasks. The session takes its latest recorded project. Hook rejections (e.g. non-git workspace) and the unsynced inbox backlog are shown alongside.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.run` | — |
| Codex app-server | aligned | `codex.turn` | Codex CLI and exec sessions come from the `codex-cli` hook; app-server sessions from the app-server adapter. |
| Codex CLI | aligned | `codex.turn` | Codex CLI and exec sessions come from the `codex-cli` hook; app-server sessions from the app-server adapter. |
| Codex exec | aligned | `codex.turn` | Codex CLI and exec sessions come from the `codex-cli` hook; app-server sessions from the app-server adapter. |
| Claude Code | aligned | `claude.interaction` | — |

#### All = Σ harnesses (`pipeline.recombination`)

- **Question:** Do per-harness values recombine to the All value?
- **Unit:** check
- **Formula:** For counts: Σ per-harness = All. For ratios: Σ per-harness numerators and denominators = All numerator and denominator, then divide.

Scope: system (not per harness).

#### Sanitization (`pipeline.sanitization`)

- **Question:** Is any raw prompt, command, argument, output, or identity text stored?
- **Unit:** rows
- **Formula:** Count of fact rows holding a dropped key (prompt, user_prompt, arguments, output, content, tool intent, identity keys), a status message over 160 characters, or a home-directory path. Expected 0.

Scope: system (not per harness).

#### Coverage grid (`pipeline.coverage`)

- **Question:** Does the data match what the registry says each harness emits?
- **Unit:** state
- **Formula:** Per signal × harness: registry alignment versus rows on the harness's route and rows matching another harness's route. Healthy, idle, possible break, stray (explained or not), or not emitted.

Scope: system (not per harness).

### Cache efficiency

#### Input tokens (`usage.input_tokens`)

- **Question:** How many input tokens were processed?
- **Unit:** tokens
- **Formula:** Σ input_tokens. Input includes cache reads for every producer.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.usage.input_tokens`, including cache read and cache creation. |
| Codex app-server | aligned | `codex.usage` | `input_token_count`, including cached input. |
| Codex CLI | aligned | `codex.usage` | `input_token_count`, including cached input. |
| Codex exec | aligned | `codex.usage` | `input_token_count`, including cached input. |
| Claude Code | differs | `claude.usage` | `input_tokens + cache_creation_tokens + cache_read_tokens`; the direct field excludes cache, so the three are summed. |

#### Cached input (`usage.cached_input`)

- **Question:** How many input tokens were served from cache?
- **Unit:** tokens
- **Formula:** Σ cached_input (cache reads only; cache creation is not reuse).

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.usage.cache_read.input_tokens`, 0 when absent. |
| Codex app-server | aligned | `codex.usage` | `cached_token_count`; a missing string value counts as 0. |
| Codex CLI | aligned | `codex.usage` | `cached_token_count`; a missing string value counts as 0. |
| Codex exec | aligned | `codex.usage` | `cached_token_count`; a missing string value counts as 0. |
| Claude Code | aligned | `claude.usage` | `cache_read_tokens`. |

#### Uncached input (`usage.uncached_input`)

- **Question:** How much input had to be processed without cache?
- **Unit:** tokens
- **Formula:** Σ input_tokens − Σ cached_input.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | Includes cache-creation input. |
| Codex app-server | aligned | `codex.usage` | — |
| Codex CLI | aligned | `codex.usage` | — |
| Codex exec | aligned | `codex.usage` | — |
| Claude Code | aligned | `claude.usage` | Includes cache-creation input. |

#### Cache creation (`usage.cache_creation`)

- **Question:** How much input was written to cache?
- **Unit:** tokens
- **Formula:** Σ cache_creation_input. Part of input and uncached input, never of cached input.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.usage.cache_creation.input_tokens`; non-zero only on Anthropic chats. |
| Codex app-server | differs | `codex.usage` | `cache_write_token_count` is reported but has been 0 on every event since 2026-08-24. |
| Codex CLI | differs | `codex.usage` | `cache_write_token_count` is reported but has been 0 on every event since 2026-08-24. |
| Codex exec | differs | `codex.usage` | `cache_write_token_count` is reported but has been 0 on every event since 2026-08-24. |
| Claude Code | aligned | `claude.usage` | `cache_creation_tokens`. |

#### Output tokens (`usage.output_tokens`)

- **Question:** How many tokens were generated?
- **Unit:** tokens
- **Formula:** Σ output_tokens (includes reasoning output).

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.usage.output_tokens`. |
| Codex app-server | aligned | `codex.usage` | `output_token_count`. |
| Codex CLI | aligned | `codex.usage` | `output_token_count`. |
| Codex exec | aligned | `codex.usage` | `output_token_count`. |
| Claude Code | aligned | `claude.usage` | `output_tokens`. |

#### Input cache utilization (`usage.cache_utilization`)

- **Question:** What share of input was served from cache?
- **Unit:** %
- **Formula:** 100 × Σ cached_input / Σ input_tokens, aggregated before division (token-weighted; no input gives no value, not 0).

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | — |
| Codex app-server | aligned | `codex.usage` | — |
| Codex CLI | aligned | `codex.usage` | — |
| Codex exec | aligned | `codex.usage` | — |
| Claude Code | aligned | `claude.usage` | The denominator includes cache-creation input. |

#### Completed operations (`usage.operations`)

- **Question:** How many producer-native usage records were accepted?
- **Unit:** operations
- **Formula:** count() of accepted usage records. A producer-native boundary, not a shared request ID.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | differs | `omp.chat` | One per token-bearing chat span, including subagents and advisors. |
| Codex app-server | differs | `codex.usage` | One per token-bearing `response.completed` event. |
| Codex CLI | differs | `codex.usage` | One per token-bearing `response.completed` event. |
| Codex exec | differs | `codex.usage` | One per token-bearing `response.completed` event. |
| Claude Code | differs | `claude.usage` | One per `api_request` log. |

#### Usage-active sessions (`usage.active_sessions`)

- **Question:** How many sessions consumed tokens?
- **Unit:** sessions
- **Formula:** uniqExact(harness, session_id) over usage records. Harness-scoped identity.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.conversation.id`. |
| Codex app-server | aligned | `codex.usage` | `conversation.id`. |
| Codex CLI | aligned | `codex.usage` | `conversation.id`. |
| Codex exec | aligned | `codex.usage` | `conversation.id`. |
| Claude Code | aligned | `claude.usage` | `session.id`. |

### Reasoning effort

#### Reasoning tokens (`effort.reasoning_tokens`)

- **Question:** How many output tokens were spent reasoning?
- **Unit:** tokens
- **Formula:** Σ reasoning_tokens over usage records (a subset of output).

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.usage.reasoning.output_tokens`, 0 when absent. |
| Codex app-server | aligned | `codex.usage` | `reasoning_token_count`. |
| Codex CLI | aligned | `codex.usage` | `reasoning_token_count`. |
| Codex exec | aligned | `codex.usage` | `reasoning_token_count`. |
| Claude Code | not emitted | — | Claude Code emits no separate thinking-token count; thinking is inside output_tokens. |

#### Reasoning share of output (`effort.reasoning_share`)

- **Question:** What share of output tokens was reasoning?
- **Unit:** %
- **Formula:** 100 × Σ reasoning_tokens / Σ output_tokens over producers that emit reasoning, aggregated before division.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | — |
| Codex app-server | aligned | `codex.usage` | — |
| Codex CLI | aligned | `codex.usage` | — |
| Codex exec | aligned | `codex.usage` | — |
| Claude Code | not emitted | — | No reasoning-token field. |

#### Requested effort (`effort.level`)

- **Question:** Which reasoning effort was requested?
- **Unit:** level
- **Formula:** Producer-requested effort; `unset` means the provider default, not no reasoning. Tasks with more than one effort are `mixed`.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `pi.gen_ai.request.reasoning.effort` on the chats directly under the root run. |
| Codex app-server | aligned | `codex.turn` | Task: `codex.turn.reasoning_effort`. Usage: `model_reasoning_effort`. |
| Codex CLI | aligned | `codex.turn` | Task: `codex.turn.reasoning_effort`. Usage: `model_reasoning_effort`. |
| Codex exec | aligned | `codex.turn` | Task: `codex.turn.reasoning_effort`. Usage: `model_reasoning_effort`. |
| Claude Code | differs | `claude.llm_request` | `effort` on LLM requests: set on nearly every request from 2026-09-28, occasionally before, so older tasks are mostly `unset`. |

#### Tasks (`task.count`)

- **Question:** How many user-initiated tasks ran?
- **Unit:** tasks
- **Formula:** count() of task_outcomes rows.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | differs | `omp.run` | A root agent run, including its subagents and advisors. |
| Codex app-server | differs | `codex.turn` | A turn. `codex-auto-review` turns are excluded; subagent threads appear as separate tasks. |
| Codex CLI | differs | `codex.turn` | A turn. `codex-auto-review` turns are excluded; subagent threads appear as separate tasks. |
| Codex exec | differs | `codex.turn` | A turn. `codex-auto-review` turns are excluded; subagent threads appear as separate tasks. |
| Claude Code | differs | `claude.interaction` | An interaction with model calls; interactions without model calls are slash commands and are excluded. |

#### Heavy-effort task share (`effort.heavy_share`)

- **Question:** What share of tasks requested high or xhigh effort?
- **Unit:** %
- **Formula:** 100 × countIf(effort IN ('high', 'xhigh')) / count() over tasks; `mixed` is not heavy.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.run` | — |
| Codex app-server | aligned | `codex.turn` | — |
| Codex CLI | aligned | `codex.turn` | — |
| Codex exec | aligned | `codex.turn` | — |
| Claude Code | differs | `claude.interaction` | Effort is mostly unset before 2026-09-28; unset tasks count as not heavy. |

#### Task duration (`task.duration`)

- **Question:** How long do tasks take?
- **Unit:** seconds
- **Formula:** P50 / P90 of the task span duration in seconds.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.run` | — |
| Codex app-server | aligned | `codex.turn` | — |
| Codex CLI | aligned | `codex.turn` | — |
| Codex exec | aligned | `codex.turn` | — |
| Claude Code | aligned | `claude.interaction` | — |

#### Model steps per task (`task.model_steps`)

- **Question:** How many model round-trips does a task take?
- **Unit:** steps
- **Formula:** Average model_steps per task.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | differs | `omp.chat` | Every chat span in the run's trace, including subagents and advisors. |
| Codex app-server | differs | `codex.sampling` | `run_sampling_request` spans whose `turn_id` is the task. |
| Codex CLI | differs | `codex.sampling` | `run_sampling_request` spans whose `turn_id` is the task. |
| Codex exec | differs | `codex.sampling` | `run_sampling_request` spans whose `turn_id` is the task. |
| Claude Code | differs | `claude.llm_request` | `claude_code.llm_request` spans in the interaction's trace. |

#### Reasoning tokens per task (`task.reasoning_tokens`)

- **Question:** How much reasoning does a task consume?
- **Unit:** tokens
- **Formula:** Σ task reasoning_tokens / tasks; task totals are below usage totals by design (calls outside a task).

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | Summed over every chat in the run's trace. |
| Codex app-server | differs | `codex.turn` | `codex.turn.token_usage.reasoning_output_tokens`; Codex CLI TUI turns often omit token usage. |
| Codex CLI | differs | `codex.turn` | `codex.turn.token_usage.reasoning_output_tokens`; Codex CLI TUI turns often omit token usage. |
| Codex exec | differs | `codex.turn` | `codex.turn.token_usage.reasoning_output_tokens`; Codex CLI TUI turns often omit token usage. |
| Claude Code | not emitted | — | No reasoning-token field. |

#### Delegations per task (`task.delegations`)

- **Question:** How many subagent runs does a task start?
- **Unit:** runs
- **Formula:** Average delegations per task.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.subagent` | `invoke_agent <agent>` spans in the trace, advisors excluded. |
| Codex app-server | not emitted | — | Codex subagents run as separate threads and appear as separate tasks. |
| Codex CLI | not emitted | — | Codex subagents run as separate threads and appear as separate tasks. |
| Codex exec | not emitted | — | Codex subagents run as separate threads and appear as separate tasks. |
| Claude Code | not emitted | — | Subagent completions are logged without a task link. |

### Tool failures

#### Tool calls (`tools.calls`)

- **Question:** How many tool calls ran?
- **Unit:** calls
- **Formula:** count() of tool_calls rows.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.execute_tool` | — |
| Codex app-server | aligned | `codex.tool_result` | — |
| Codex CLI | aligned | `codex.tool_result` | — |
| Codex exec | aligned | `codex.tool_result` | — |
| Claude Code | aligned | `claude.tool_result` | — |

#### Tool failure rate (`tools.failure_rate`)

- **Question:** What share of tool calls with an explicit outcome failed?
- **Unit:** %
- **Formula:** 100 × failed calls / calls with an explicit outcome (succeeded, failed, or aborted). Unknown outcomes stay out of the denominator.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.execute_tool` | `pi.gen_ai.tool.status` error; aborted calls count in the denominator only. |
| Codex app-server | differs | `codex.tool_result` | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Codex CLI | differs | `codex.tool_result` | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Codex exec | differs | `codex.tool_result` | `success=false` or a non-zero `x.exit_code`; Codex reports success=true for non-zero exits, so the exit code wins. |
| Claude Code | aligned | `claude.tool_result` | `success=false`. |

#### Failure signature (`tools.failure_signature`)

- **Question:** Which normalized failures repeat?
- **Unit:** signature
- **Formula:** Failed calls grouped by tool and normalized failure signature (home → ~, digit and hex runs → N, 160 characters).

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.execute_tool` | First error-like line of the span status message. |
| Codex app-server | aligned | `codex.tool_result` | `x.failure_signature`: first error-like output line. |
| Codex CLI | aligned | `codex.tool_result` | `x.failure_signature`: first error-like output line. |
| Codex exec | aligned | `codex.tool_result` | `x.failure_signature`: first error-like output line. |
| Claude Code | differs | `claude.tool_result` | `error_type` only: an error class, not the message. |

#### Tasks affected by failures (`tools.tasks_affected`)

- **Question:** What share of tool-using tasks hit a failed call?
- **Unit:** %
- **Formula:** 100 × tasks with ≥1 failed call / tasks with ≥1 attributed tool call.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.execute_tool` | Call → root run of the same trace. |
| Codex app-server | differs | `codex.tool_result` | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Codex CLI | differs | `codex.tool_result` | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Codex exec | differs | `codex.tool_result` | Call → task by session and the turn span that contains the call time; calls outside a user turn are unattributed. |
| Claude Code | aligned | `claude.tool_result` | Call → interaction of the same trace. |

#### Repeated attempts (`tools.repeats`)

- **Question:** Which operations run with identical arguments more than once in a task?
- **Unit:** tasks
- **Formula:** Tasks where the same tool and argument hash appears ≥2 times, excluding waiting and status-polling tools (wait, write_stdin, wait_agent, wait_threads, collaborationwait_agent, sleep, get_goal, get_usage_limits) whose identical calls are expected.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | Tool spans carry no argument identity. |
| Codex app-server | aligned | `codex.tool_result` | `x.arguments_hash` over the raw arguments. |
| Codex CLI | aligned | `codex.tool_result` | `x.arguments_hash` over the raw arguments. |
| Codex exec | aligned | `codex.tool_result` | `x.arguments_hash` over the raw arguments. |
| Claude Code | not emitted | — | Tool results carry sizes, not argument identity. |

#### Tool loops (`tools.loops`)

- **Question:** Which tasks repeat the same operation back-to-back?
- **Unit:** tasks
- **Formula:** Tasks with a run of ≥3 consecutive calls sharing tool and argument hash, excluding waiting and status-polling tools.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | Tool spans carry no argument identity. |
| Codex app-server | aligned | `codex.tool_result` | Ordered by call time within the task. |
| Codex CLI | aligned | `codex.tool_result` | Ordered by call time within the task. |
| Codex exec | aligned | `codex.tool_result` | Ordered by call time within the task. |
| Claude Code | not emitted | — | Tool results carry no argument identity. |

### Friction

#### Interrupted tasks (`friction.interrupt`)

- **Question:** How often did the user stop a task?
- **Unit:** %
- **Formula:** 100 × Σ interrupted / tasks with an interrupt signal.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | differs | `omp.run` | The run has an aborted chat stop reason (`stop_reason.aborted.count > 0`). |
| Codex app-server | aligned | `codex.interrupt` | The turn's `turn.id` appears on a `turn/interrupt` span. |
| Codex CLI | aligned | `codex.interrupt` | The turn's `turn.id` appears on a `turn/interrupt` span. |
| Codex exec | differs | `codex.interrupt` | Headless: no user can interrupt, so the value is always 0. |
| Claude Code | not emitted | — | Claude Code emits no interrupt event. |

#### Steered tasks (`friction.steer`)

- **Question:** How often did the user redirect a running task?
- **Unit:** %
- **Formula:** 100 × Σ steered / tasks with a steer signal.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | omp emits no steer event. |
| Codex app-server | aligned | `codex.steer` | The turn's `turn.id` appears on a `turn/steer` span. |
| Codex CLI | aligned | `codex.steer` | The turn's `turn.id` appears on a `turn/steer` span. |
| Codex exec | differs | `codex.steer` | Headless: no user can steer, so the value is always 0. |
| Claude Code | not emitted | — | Claude Code emits no steer event; queued prompts are separate interactions. |

#### Errored tasks (`friction.error`)

- **Question:** How often did a task end on an error?
- **Unit:** %
- **Formula:** 100 × Σ errored / tasks with an error signal.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.run` | The run has an error chat stop reason. |
| Codex app-server | not emitted | — | Turn spans carry no terminal error status. |
| Codex CLI | not emitted | — | Turn spans carry no terminal error status. |
| Codex exec | not emitted | — | Turn spans carry no terminal error status. |
| Claude Code | differs | `claude.llm_request` | Any LLM request in the interaction with an error or status ≥ 400. |

#### Quick follow-up (`friction.quick_follow_up`)

- **Question:** How often did the next task in the same session start within 10 minutes?
- **Unit:** %
- **Formula:** 100 × Σ quick_follow_up / tasks with a follow-up signal. A proxy: a follow-up can be the next planned step.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.run` | — |
| Codex app-server | aligned | `codex.turn` | — |
| Codex CLI | aligned | `codex.turn` | — |
| Codex exec | not emitted | — | Headless one-shot runs: nobody can follow up. |
| Claude Code | aligned | `claude.interaction` | — |

#### Clean completion (`friction.clean_completion`)

- **Question:** How often did a task finish with no observed friction?
- **Unit:** %
- **Formula:** 100 × Σ clean_completion / tasks with a value. Clean = no interrupt, no error, no quick follow-up among the components the producer observes (row field `friction_observed`).

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.run` | Observes interrupt, error, and follow-up. |
| Codex app-server | differs | `codex.turn` | Observes interrupt and follow-up; task errors are not emitted. |
| Codex CLI | differs | `codex.turn` | Observes interrupt and follow-up; task errors are not emitted. |
| Codex exec | not emitted | — | Headless: follow-up is unobservable, so clean completion is NULL. |
| Claude Code | differs | `claude.interaction` | Observes error and follow-up; interrupts are not emitted. |

#### Recovery after failure (`friction.recovery`)

- **Question:** After a failed tool call, does the same operation later succeed in the task?
- **Unit:** %
- **Formula:** Inferred. 100 × failed operations with a later success of the same operation in the same task / failed operations in attributed tasks.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | differs | `omp.execute_tool` | Operation = tool name only (no argument identity). |
| Codex app-server | differs | `codex.tool_result` | Operation = tool + command head + subcommand. |
| Codex CLI | differs | `codex.tool_result` | Operation = tool + command head + subcommand. |
| Codex exec | differs | `codex.tool_result` | Operation = tool + command head + subcommand. |
| Claude Code | differs | `claude.tool_result` | Operation = tool name + bash argv0. |

### Guardrails

#### Tool approval decisions (`guard.decisions`)

- **Question:** How are tool calls approved, and by whom?
- **Unit:** decisions
- **Formula:** count() of tool decisions by decision and source.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | omp emits no approval decisions. |
| Codex app-server | aligned | `codex.tool_decision` | `decision`, `source` (config or user). |
| Codex CLI | aligned | `codex.tool_decision` | `decision`, `source` (config or user). |
| Codex exec | aligned | `codex.tool_decision` | `decision`, `source` (config or user). |
| Claude Code | aligned | `claude.tool_decision` | `decision`, `source`. |

#### Rejected tool calls (`guard.rejections`)

- **Question:** How often was a tool call rejected?
- **Unit:** %
- **Formula:** 100 × decisions that are not an approval (`approved`, `approved_with_amendment`, `accept`) / all decisions.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | omp emits no approval decisions. |
| Codex app-server | aligned | `codex.tool_decision` | Sources are Config, User, and AutomatedReviewer (the approval reviewer model). |
| Codex CLI | aligned | `codex.tool_decision` | Sources are Config, User, and AutomatedReviewer (the approval reviewer model). |
| Codex exec | aligned | `codex.tool_decision` | Sources are Config, User, and AutomatedReviewer (the approval reviewer model). |
| Claude Code | aligned | `claude.tool_decision` | Sources are config and user_reject. |

#### Sandbox outcomes (`guard.sandbox`)

- **Question:** How often do sandboxed commands get denied?
- **Unit:** outcomes
- **Formula:** count() of sandbox outcomes by outcome.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | omp has no sandbox. |
| Codex app-server | aligned | `codex.sandbox_outcome` | — |
| Codex CLI | aligned | `codex.sandbox_outcome` | — |
| Codex exec | aligned | `codex.sandbox_outcome` | — |
| Claude Code | not emitted | — | Claude Code emits no sandbox outcome. |

#### Quality-gate bypass (`guard.gate_bypass`)

- **Question:** How often did a command bypass a quality gate?
- **Unit:** calls
- **Formula:** Tool calls whose command contains --no-verify, HUSKY=0, SKIP=, or --no-gpg-sign.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | Command text is not emitted in a normalized form. |
| Codex app-server | aligned | `codex.tool_result` | `x.gate_bypass`, matched over the whole argument text before it is discarded, so commands nested in JavaScript `exec` code are covered. |
| Codex CLI | aligned | `codex.tool_result` | `x.gate_bypass`, matched over the whole argument text before it is discarded, so commands nested in JavaScript `exec` code are covered. |
| Codex exec | aligned | `codex.tool_result` | `x.gate_bypass`, matched over the whole argument text before it is discarded, so commands nested in JavaScript `exec` code are covered. |
| Claude Code | not emitted | — | Only `bash_argv0` is emitted, not flags. |

#### Command churn (`guard.command_churn`)

- **Question:** Where do agents try ≥3 distinct commands against the same file in one task?
- **Unit:** tasks
- **Formula:** Task × target pairs with ≥3 distinct command head + subcommand values.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | Tool spans carry no target. |
| Codex app-server | differs | `codex.tool_result` | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Codex CLI | differs | `codex.tool_result` | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Codex exec | differs | `codex.tool_result` | Targets are `apply_patch` files, `path` arguments, and path-like command arguments (inferred from tokens with a slash or a known file extension). |
| Claude Code | not emitted | — | Tool results carry no target. |

### Provider

#### Model calls (`provider.calls`)

- **Question:** How many model calls reached a terminal state?
- **Unit:** calls
- **Formula:** count() of producer-native model call records.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | Chat spans. |
| Codex app-server | differs | `codex.response_completed` | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Codex CLI | differs | `codex.response_completed` | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Codex exec | differs | `codex.response_completed` | One per response stream (`response.completed`), including failed streams; not a logical request. |
| Claude Code | aligned | `claude.llm_request` | LLM request spans. |

#### Call error rate (`provider.error_rate`)

- **Question:** What share of model calls failed?
- **Unit:** %
- **Formula:** 100 × failed calls / calls not cancelled by the user. A user cancellation is not a provider failure.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | Span error status; `error.type = aborted` is a user cancellation and is excluded. |
| Codex app-server | differs | `codex.response_completed` | Stream level: a non-empty `error.message` on `response.completed`. |
| Codex CLI | differs | `codex.response_completed` | Stream level: a non-empty `error.message` on `response.completed`. |
| Codex exec | differs | `codex.response_completed` | Stream level: a non-empty `error.message` on `response.completed`. |
| Claude Code | aligned | `claude.llm_request` | Span error or `success=false`. |

#### Stream disconnects (`provider.stream_disconnects`)

- **Question:** How often does a response stream break before completion?
- **Unit:** calls
- **Formula:** Completed-response records whose error says the stream disconnected before completion.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | Chat spans record errors but not stream state. |
| Codex app-server | aligned | `codex.response_completed` | `error.message` starting 'stream disconnected before completion'. |
| Codex CLI | aligned | `codex.response_completed` | `error.message` starting 'stream disconnected before completion'. |
| Codex exec | aligned | `codex.response_completed` | `error.message` starting 'stream disconnected before completion'. |
| Claude Code | not emitted | — | No stream-state field. |

#### Model call latency (`provider.latency`)

- **Question:** How long does a model call take?
- **Unit:** seconds
- **Formula:** P50 / P95 of the model call duration.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | Chat span duration. |
| Codex app-server | differs | `codex.sampling` | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Codex CLI | differs | `codex.sampling` | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Codex exec | differs | `codex.sampling` | `run_sampling_request` span, which includes retries, response processing, and in-flight tool draining. |
| Claude Code | aligned | `claude.llm_request` | LLM request span duration. |

#### Time to first token (`provider.ttft`)

- **Question:** How long until the first token arrives?
- **Unit:** seconds
- **Formula:** P50 / P95 of time to first token.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.response.time_to_first_chunk` (seconds). |
| Codex app-server | aligned | `codex.response_completed` | `ttft_ms` on `response.completed`. |
| Codex CLI | aligned | `codex.response_completed` | `ttft_ms` on `response.completed`. |
| Codex exec | aligned | `codex.response_completed` | `ttft_ms` on `response.completed`. |
| Claude Code | aligned | `claude.llm_request` | `ttft_ms`. |

#### Output throughput (`provider.throughput`)

- **Question:** Is generation speed degraded independently of response size?
- **Unit:** tokens/s
- **Formula:** P50 of output tokens / (duration − time to first token) for calls with both timings and positive generation time.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | — |
| Codex app-server | not emitted | — | Completed responses carry TTFT but no call duration. |
| Codex CLI | not emitted | — | Completed responses carry TTFT but no call duration. |
| Codex exec | not emitted | — | Completed responses carry TTFT but no call duration. |
| Claude Code | aligned | `claude.llm_request` | — |

#### Retries (`provider.retries`)

- **Question:** How much extra traffic do retries create?
- **Unit:** attempts
- **Formula:** Attempts beyond the first per logical call.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | No attempt or retry identity. |
| Codex app-server | differs | `codex.try_sampling` | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Codex CLI | differs | `codex.try_sampling` | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Codex exec | differs | `codex.try_sampling` | `try_run_sampling_request` spans beyond one per `run_sampling_request`. |
| Claude Code | aligned | `claude.llm_request` | `attempt` > 1 on LLM requests, plus `api_retries_exhausted`. |

#### Unknown outcomes (`provider.unknown_outcomes`)

- **Question:** How many model calls lack an explicit outcome?
- **Unit:** calls
- **Formula:** Calls with no success flag, error, or finish reason.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | No `gen_ai.response.finish_reasons`. |
| Codex app-server | differs | `codex.response_completed` | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Codex CLI | differs | `codex.response_completed` | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Codex exec | differs | `codex.response_completed` | Every response stream has an outcome (error message or completion), so Codex has no unknown calls. |
| Claude Code | aligned | `claude.llm_request` | No `success` attribute. |

#### Model conformance (`provider.model_conformance`)

- **Question:** Did the provider serve the requested model?
- **Unit:** calls
- **Formula:** Calls whose response model is set and differs from the requested model.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.chat` | `gen_ai.request.model` vs `gen_ai.response.model`. |
| Codex app-server | not emitted | — | No response model. |
| Codex CLI | not emitted | — | No response model. |
| Codex exec | not emitted | — | No response model. |
| Claude Code | not emitted | — | No response model. |

### Recurrence

#### Recurring targets (`recur.targets`)

- **Question:** Which files recur across tasks?
- **Unit:** tasks
- **Formula:** Targets touched in ≥2 tasks, with task, day, and failure counts.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | not emitted | — | Tool spans carry no target. |
| Codex app-server | differs | `codex.tool_result` | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Codex CLI | differs | `codex.tool_result` | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Codex exec | differs | `codex.tool_result` | `x.targets`: `apply_patch` files, `path` arguments, and path-like command arguments (inferred), home as ~. |
| Claude Code | not emitted | — | Tool results carry no target. |

#### Recurring failure signatures (`recur.signatures`)

- **Question:** Which failure signatures recur across tasks and days?
- **Unit:** tasks
- **Formula:** Failure signatures seen in ≥2 tasks, with occurrences, tasks, and days.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.execute_tool` | — |
| Codex app-server | aligned | `codex.tool_result` | — |
| Codex CLI | aligned | `codex.tool_result` | — |
| Codex exec | aligned | `codex.tool_result` | — |
| Claude Code | differs | `claude.tool_result` | Signature is the `error_type` class only. |

#### Actionable repeats (`recur.actionable`)

- **Question:** Which repeated failures cross the evidence threshold for intervention?
- **Unit:** signatures
- **Formula:** Over the 7 Europe/London calendar days ending on the window end: ≥3 occurrences across ≥2 tasks and ≥2 days, or ≥5 occurrences across ≥3 tasks.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.execute_tool` | — |
| Codex app-server | aligned | `codex.tool_result` | — |
| Codex CLI | aligned | `codex.tool_result` | — |
| Codex exec | aligned | `codex.tool_result` | — |
| Claude Code | differs | `claude.tool_result` | Signature is the `error_type` class only. |

#### Project concentration (`recur.project_concentration`)

- **Question:** Is a repeated failure localized to one project?
- **Unit:** %
- **Formula:** Per signature: 100 × occurrences in the top project / occurrences with a project. Project is the git repository the session-context hooks recorded for the session.

| Harness | Alignment | Route | Note |
| --- | --- | --- | --- |
| omp | aligned | `omp.execute_tool` | Session → project from the omp hook. |
| Codex app-server | aligned | `codex.tool_result` | Session → project from the `codex-cli` / app-server hooks. |
| Codex CLI | aligned | `codex.tool_result` | Session → project from the `codex-cli` / app-server hooks. |
| Codex exec | aligned | `codex.tool_result` | Session → project from the `codex-cli` / app-server hooks. |
| Claude Code | aligned | `claude.tool_result` | Session → project from the Claude Code hook; interactions in a non-git workspace have none. |

### Interventions

#### Findings and proposals (`intervene.findings`)

- **Question:** Which findings and intervention proposals exist, and in what state?
- **Unit:** records
- **Formula:** Findings by state and detector; proposals by state and tier, from the local workflow store.

Scope: system (not per harness).

#### Post-intervention recurrence (`intervene.post_recurrence`)

- **Question:** Did an applied intervention reduce the failure signature it targeted?
- **Unit:** per task
- **Formula:** For each applied intervention: occurrences / attributed tasks in equal pre and post windows around its application time. Needs applied interventions.

Scope: system (not per harness).

#### Rule adherence (`intervene.rule_adherence`)

- **Question:** Are skill and workflow rules followed when their trigger is present?
- **Unit:** %
- **Formula:** Unsupported: needs a versioned rule registry with deterministic triggers and observable required actions. No producer emits rule triggers.

Scope: system (not per harness).

#### Uncodified practice recurrence (`intervene.practice_recurrence`)

- **Question:** Which successful operation sequences recur often enough to codify?
- **Unit:** sequences
- **Formula:** Unsupported: needs ordered operations with an explicit successful task outcome. No producer emits explicit task success, and clean completion is a friction proxy, not success.

Scope: system (not per harness).

#### Enforcement-tier audit (`intervene.tier_audit`)

- **Question:** Are repeats addressed at the strongest deterministic tier?
- **Unit:** proposals
- **Formula:** Proposals by selected tier (tool, script, skill, guidance) and approval state.

Scope: system (not per harness).

<!-- END GENERATED FROM signal_support.toml -->

## 5. Interpretation constraints

1. **Task boundaries differ by harness.** A Codex turn, an omp root run (including
   subagents and advisors), and a Claude Code interaction with model calls are all
   one user request's autonomous run, but differ in scope. Codex subagent threads are
   separate tasks.
2. **Usage records are not a shared request boundary.** Completed operations count
   producer-native usage records; do not compare them as requests.
3. **Spend is tokens, not money.** No producer reports cost comparably.
4. **`unset` effort is not "no reasoning".** It means the provider default. Effort is
   user-chosen, so differences between levels are correlational; compare within one
   harness and model.
5. **Clean completion is a proxy.** It infers acceptance from absent friction; a quick
   follow-up can be the next planned step.
6. **Headless `codex_exec` runs** cannot be interrupted, steered, or followed up; those
   values are always 0 or NULL.
7. **Repeats and loops exclude polling tools** (`wait`, `write_stdin`, `wait_agent`,
   `wait_threads`, `collaborationwait_agent`, `sleep`, `get_goal`,
   `get_usage_limits`), whose identical calls are expected.
8. **Codex tool calls outside a user turn are unattributed.** About 54% of Codex CLI
   calls fall inside an exported turn span; some CLI turns are never exported.
9. **Inferred values are labelled.** Recovery after failure and path-like command
   targets are inferred and say so in their notes.
10. **Mislabelled rows are strays.** Claude Code sessions exported as `oh-my-pi` and
    Docker build spans under `oh-my-pi` count for neither harness.
11. **Project attribution is partial.** 54–98% of tasks per harness have a hook
    project; sessions in non-Git workspaces are rejected by the hooks.

## 6. Verification

The Pipeline view runs these checks for the selected window:

- **Source parity:** `usage_events` equals a direct recount of the raw SigNoz source
  with the producer contracts (last 7 days of the window, ending 10 minutes ago).
- **All = Σ harnesses:** direct All aggregates equal the sum of per-harness
  aggregates for usage, tasks, tools, and model calls, including harness-scoped
  session counts.
- **Sanitization:** 0 rows hold dropped keys, status messages over 160 characters,
  or home-directory paths.
- **Coverage grid:** per signal × harness, the registry's alignment versus rows on the
  route and on other harnesses' routes: healthy, idle, no events, possible break, not
  emitted, or stray (explained or unexplained).
- **Project attribution:** attributed share of tasks per harness, hook rejections,
  and the unsynced inbox backlog.

Each view's totals were reconciled per harness and for All against direct queries on
the live fact views when it shipped; the results are recorded in the plan.
