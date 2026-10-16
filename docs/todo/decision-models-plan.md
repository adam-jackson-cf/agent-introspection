# Decision Models Plan

Status: **proposed** 2026-10-12. Not scheduled for implementation.

## Objective

Add a **Decision models** page to the Behaviour group that shows how agent
policy enforcement uses Jev, the TypeSafe decision model reached through
OpenRouter (`POST https://openrouter.ai/api/alpha/decisions`, requested and
checked as the pinned `typesafe/jev-1.13-20260917`). Policy
enforcement is a decision, not a tool failure: omp `blocked` tool results are
recorded as the `policy_blocked` outcome and excluded from tool failure rates.

Capture is by **OpenTelemetry emitted from jev-guard**, so decisions reach
SigNoz through the same pipeline as every other producer.

## Current state (2026-10-12)

| Source                                                               | Holds                                                                                                                                                                                                                                                                                                                                                                                                                                          | In SigNoz   |
| -------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| Decision audit, `~/.omp/agent/jev-audit/<policy>/<YYYY-MM-DD>.jsonl` | One record per recorded decision: policy, policyVersion, mode, sessionId, agentKind, agentName, stage, rule, verdict, enforced, requestId, providerResponseId, costUsd, latencyMs, error, httpStatus, a redacted `subject` (≤300 chars) and `state` (≤4,000 chars of command text). From 2026-09-29: guard.bash 4,238 (1,969 enforce, 2,269 shadow), guard.write 1,198, guard.integrity 1,290, guard.result 462, guard.write.flags 247, ask 1. | No          |
| Review labels, `jev-audit/<policy>/labels.jsonl`                     | `{timestamp, requestId, policy, label, reviewer: human or agent, note?}`, written by the jev-lab workbench (`~/.omp/jev-lab/lib/policy.ts`), not the extension. A human label overrides an agent label. 100 labels across guard.bash, guard.write, guard.result.                                                                                                                                                                               | No          |
| omp `execute_tool` spans with `omp.gen_ai.tool.status = 'blocked'`   | 126 in 14 days. Policy and rule only in the status text (`Jev guard blocked … (<rule>)`).                                                                                                                                                                                                                                                                                                                                                      | Partly      |
| omp `judgment ~typesafe/jev-latest` spans                            | omp's built-in judgment API, used by its `find` tool (`purpose = find`) and auto-thinking; not jev-guard.                                                                                                                                                                                                                                                                                                                                      | Yes         |
| Prompt labels (`classify.py`)                                        | Cost, status, reason code, attempts per prompt in `introspection.prompt_labels`. No latency.                                                                                                                                                                                                                                                                                                                                                   | Facts store |

Live policies: `guard.bash`, `guard.write`, `guard.result`, `guard.integrity`
(including the `guard.integrity.suspect` mode), `guard.write.flags`
(informational, kept apart from decision metrics), and `ask`. `subagent.effort`
and `subagent.review-triage` were removed on 2026-10-02; only their audit
history remains.

All audit records are written by one function, `appendDecision`
(`~/.omp/agent/extensions/lib/jev.ts`), called from jev-guard's `record()` and
jev-ask's `writeRecord()`. Jev itself is called with a raw `fetch` in
`decide()`, so no span exists for these decisions, and shadow decisions leave
no telemetry at all.

## Approach

### 1. One span per recorded decision, emitted in `appendDecision`

Emit the span inside `appendDecision`, so the audit line and the span come from
the same record and every policy (guard and ask) is covered in one place.
Decide whether spans are still emitted when `JEV_AUDIT=0` switches the file
audit off (recommended: yes, the two are independent switches).

Span name `jev.decision` (distinct from omp's `judgment` spans, so `find`
volumes and policy decisions are never mixed), kind internal, timed from the
start of the decision to the record.

| Attribute                                        | Type   | Source                        | Notes                                                               |
| ------------------------------------------------ | ------ | ----------------------------- | ------------------------------------------------------------------- |
| `jev.policy`                                     | string | policy                        | 7 values                                                            |
| `jev.policy_version`                             | string | policyVersion                 | dated string                                                        |
| `jev.mode`                                       | string | mode                          | `shadow` or `enforce`                                               |
| `jev.stage`                                      | string | stage                         | `deterministic`, `jev`, `jev_error`, `skipped`                      |
| `jev.rule`                                       | string | rule                          | fixed ids, `precheck:*`, integrity rule ids                         |
| `jev.verdict`                                    | string | verdict                       | allow, flag, confirm, block, light, standard, deep, answered, error |
| `jev.enforced`                                   | bool   | enforced                      | lands in `attrs_bool`                                               |
| `jev.user_decision`                              | string | confirm outcome               | enforce-mode confirm prompts                                        |
| `jev.unanswered`                                 | bool   | confirm outcome               |                                                                     |
| `jev.result_windows`                             | int    | guard.result                  | windowed judging                                                    |
| `jev.error`                                      | string | error                         | short code only                                                     |
| `jev.request_id`                                 | string | requestId                     | join key for review labels                                          |
| `jev.provider_response_id`                       | string | providerResponseId            | OpenRouter id                                                       |
| `jev.cost_usd`                                   | double | costUsd                       | Jev stage                                                           |
| `jev.latency_ms`                                 | double | latencyMs                     | client-side                                                         |
| `http.response.status_code`                      | int    | httpStatus                    | Jev stage                                                           |
| `gen_ai.provider.name`                           | string | `openrouter`                  | reuses omp's name                                                   |
| `gen_ai.request.model` / `gen_ai.response.model` | string | requested, served             |                                                                     |
| `gen_ai.usage.input_tokens` / `output_tokens`    | int    | response usage                |                                                                     |
| `gen_ai.conversation.id`                         | string | sessionId                     | joins to omp sessions                                               |
| `gen_ai.agent.kind` / `gen_ai.agent.name`        | string | agentKind, agentName          |                                                                     |
| `gen_ai.tool.call.id`                            | string | `event.toolCallId`            | joins to `execute_tool` and `policy_blocked`                        |
| `jev.subject_hash`                               | string | keyed hash of subject, 16 hex | optional; grouping repeats only                                     |

Never emit `subject`, `state`, question text, or the answers JSON (decision
D4). If per-question scores are wanted later, emit only the bounded scalar keys
(`effect`, `destructive`, `secret_exposure`, `contains_secret`,
`prompt_injection`, `touches_system`, integrity families). A plain SHA-256 of a
short redacted command can be reversed by dictionary, so `jev.subject_hash`
must be keyed (same convention as `x.status_hash`) or left out of v1.

Numeric attributes must be emitted as numbers so they land in `attrs_number`.
High-cardinality ids (`jev.request_id`, `jev.provider_response_id`,
`gen_ai.tool.call.id`, `gen_ai.conversation.id`) are fine on spans and must
never become metric labels.

### 2. Export through omp's tracer

The omp extension API (`ExtensionAPI`, `ExtensionContext`) exposes no tracer,
and omp's judgment API (`resolveJudge`) is internal and not exported, so
jev-guard keeps its own Jev client and emits its own span.

omp registers its tracer provider on the global `@opentelemetry/api` (1.9.1,
installed at `~/.omp/node_modules/@opentelemetry/api`, where extensions resolve
bare imports). In `lib/jev.ts`:

```ts
import { context, trace } from "@opentelemetry/api";
const tracer = trace.getTracer("jev-guard");
```

returns omp's provider, so spans share its resource
(`service.name = oh-my-pi`), its `BatchSpanProcessor`, and its OTLP exporter
(configured from `~/.omp/.env`; `telemetry.otlpExportEnabled` must stay on). If
a future omp ships a different API major, the global is unset and the calls
become no-ops, which is safe.

Fallback, only if the global provider proves unavailable: post OTLP/JSON to
`OTEL_EXPORTER_OTLP_ENDPOINT + /v1/traces` from the same environment, as the
activity adapter already does for logs
(`.agents/skills/introspection-onboarding/scripts/adapters/omp/activity.ts`).

**Parenting.** Spans join the omp trace through the active context at hook
time:

- Pre-call guards (`guard.bash`, `guard.write`, `guard.integrity`) run in the
  `tool_call` hook, before omp starts `execute_tool`; their parent is
  `invoke_agent`, and the decision is a sibling of the tool's span.
- `guard.result` (the `tool_result` hook) and the `ask` tool run inside
  `execute_tool`; that span is their parent.
- Shadow Jev calls run fire-and-forget; capture `context.active()` before
  backgrounding and start the span in that context, or it will have no parent.

Every decision joins its tool call by `gen_ai.tool.call.id`; blocked calls
still get an `execute_tool` span, so `policy_blocked` rows gain policy and rule
through that join. The extension never holds the `execute_tool` span, so it
cannot add attributes to it.

**Isolation and latency.** Enforce-mode Jev calls have an 8 s timeout inside
omp's 30 s `tool_call` budget. Span start and end are synchronous and cheap;
export is batched by omp and never awaited on the tool path. Span code is
wrapped like the existing audit write (errors swallowed), so a missing or
failing exporter cannot change a verdict or add latency.

### 3. Facts store

- **Projection:** no change. Every `oh-my-pi` span is already admitted (the
  span-name allowlist in `select_spans.sql` applies only to Codex), the
  `omp.gen_ai.` rename leaves `jev.*`, `gen_ai.*`, and `http.*` keys untouched,
  and the drop list removes text keys. Any future text attribute must be added
  to that drop list.
- **View and snapshot:** a `decisions` view in `003_views.sql` (one row per
  `jev.decision` span) and `Snapshot("decisions", "(harness, ts)")` in
  `facts.py` `SNAPSHOTS`; refreshers and `facts verify --compare` follow
  automatically. Add it to the fact-view list asserted in `tests/test_facts.py`.
- **Review labels:** a `facts sync` loader over `labels.jsonl` into a
  `decision_labels` table (`request_id`, `policy`, `label`, `reviewer`,
  `labelled_at`), dropping `note`. Labels come from jev-lab, a separate
  process, so a loader is simpler than a second span producer. Latest human
  label wins over agent labels.
- **Prompt-label latency:** add `latency_ms UInt32 DEFAULT 0` in
  `001_tables.sql` (`prompt_labels`), time `post()` in `classify.decide()`
  (including the failure path), carry it through `label()`, `UNLABELLED`, and
  `_row`, and add it to `inbox.py` `_STRUCTURES["prompt_labels"]`. Tests in
  `tests/test_classify.py`.

### 4. Registry

`decision.*` signals use `scope = "harness"`, with
`oh-my-pi = { route = "omp.jev_decision", alignment = "aligned" }` and Codex
and Claude Code as `not applicable` ("jev-guard is an omp extension; the policy
cannot exist for this harness"). Marking them `not emitted` instead would hide
the page whenever another harness is enabled (`hidden_signals`, `facts.py`).
The `not applicable` cells on the Pipeline coverage grid are informational,
not a comparison between harnesses.

New route:

```toml
[[route]]
id = "omp.jev_decision"
harnesses = ["oh-my-pi"]
source = "spans"
match = "name = 'jev.decision'"
```

Add `decisions = "Decision models"` to `[views]`, after `guardrails`.

| Signal                               | Definition notes                                                                                                                                                                                       |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `decision.volume`                    | Recorded decisions per day by policy and mode. Read-only allowlist hits are recorded 1 in 25 (rule `allowlist-sample`), so this is recorded volume, not total checks. `guard.write.flags` shown apart. |
| `decision.cost` / `decision.latency` | Spend and p50/p95 latency, Jev stage.                                                                                                                                                                  |
| `decision.verdicts`                  | Verdict mix per policy, enforce and shadow; share settled by a deterministic rule before Jev. `allowlist-sample` excluded from the allow rate.                                                         |
| `decision.shadow_would_block`        | In shadow a block is recorded as `flag` and `enforced` is always false, so "would block" is `mode = shadow AND verdict IN ('flag','confirm')`, refined by rule. `expect = "events"`.                   |
| `decision.errors`                    | `stage = jev_error` share. Zero so far is a true zero (the path exists); `expect = "events"`.                                                                                                          |
| `decision.review_agreement`          | Labels joined on `request_id`: share `correct` versus `false_positive`, `false_negative`, `incorrect`, `uncertain`, per policy and reviewer (agent first pass, human).                                 |
| `labels.jev_usage`                   | `scope = "system"` (this repo's own pipeline): prompt-labelling calls, cost, failures by reason code, latency. Buildable from `prompt_labels` today.                                                   |

### 5. Page

Behaviour group, after Guardrails. First version:

1. **Activity, cost and latency**: decisions per day by policy, enforce versus
   shadow; spend; p50/p95 latency.
2. **Verdicts and shadow mode**: verdict mix per policy; deterministic rule
   versus Jev; what shadow mode would have blocked.
3. **Review agreement**: label agreement per policy and reviewer.
4. **Prompt labelling**: this repo's Jev usage, cost, volume, failures.

Deferred: what the agent did after a block.

## Open questions

- Spans start at deploy. Backfilling from the audit files (from 2026-09-29,
  including the removed `subagent.*` policies) is cheap with a one-off loader
  into `decisions_history`, marked `source = 'audit'`, but those rows have no
  trace or tool-call join. Is that history wanted?
- Should spans be emitted when `JEV_AUDIT=0`?
- Keep or drop `jev.subject_hash` in v1?

## Acceptance

- Every audit record written after deploy has a matching `jev.decision` span
  (same `jev.request_id`, policy, verdict, mode), shadow included.
- No span carries subject, state, command, prompt, question, or answer text.
- Each decision shares a trace with omp and joins its tool call by
  `gen_ai.tool.call.id`; shadow spans are parented.
- Enforce-mode tool-call latency is unchanged with the exporter on, off, or
  failing.
- The Decision models page renders all four sections from snapshots, and stays
  visible with Codex and Claude Code enabled.
