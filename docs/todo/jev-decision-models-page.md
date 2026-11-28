# Jev decision models page

Track: priority: CLI data access / problem and friction surfacing

## Problem

Policy enforcement by jev-guard (the omp extension that asks Jev, the TypeSafe decision model, whether to
allow a tool call) is invisible. omp `blocked` tool results become the `policy_blocked` outcome, but
policy, rule, verdict, cost, latency and shadow-mode decisions never reach SigNoz. Jev is called with a
raw `fetch`, so no span exists, and shadow decisions leave no telemetry.

## Evidence

- Audit records are written by `appendDecision` in `~/.omp/agent/extensions/lib/jev.ts` (outside this
  repository) to `~/.omp/agent/jev-audit/<policy>/<YYYY-MM-DD>.jsonl`. Review labels are in
  `jev-audit/<policy>/labels.jsonl`, written by the jev-lab workbench.
- No `jev.decision`, `decision_labels` or `decision.*` exists in `src` or `dashboard`.
  `src/agent_introspection/facts.py:242-248` `SNAPSHOTS` has no `decisions` entry.

## Proposed change

1. **Span.** Emit one `jev.decision` span per recorded decision inside `appendDecision`, so guard and
   ask policies are covered in one place. Attributes: `jev.policy`, `jev.policy_version`, `jev.mode`,
   `jev.stage`, `jev.rule`, `jev.verdict`, `jev.enforced`, `jev.user_decision`, `jev.unanswered`,
   `jev.result_windows`, `jev.error` (short code), `jev.request_id`, `jev.provider_response_id`,
   `jev.cost_usd`, `jev.latency_ms`, `http.response.status_code`, `gen_ai.provider.name`, requested
   and served model, token usage, `gen_ai.conversation.id`, agent kind and name, `gen_ai.tool.call.id`.
   Emit numbers as numbers.
2. **Privacy.** Never emit subject, state, question text or answers. A `jev.subject_hash` must be keyed,
   as `x.status_hash` is, or left out.
3. **Export.** Use `trace.getTracer("jev-guard")` from omp's global `@opentelemetry/api`, so spans share
   omp's resource and exporter. Capture `context.active()` before backgrounding shadow calls. Swallow
   span errors so a failing exporter cannot change a verdict or add latency.
4. **Facts store.** No projection change. Add a `decisions` view in `003_views.sql` and
   `Snapshot("decisions", "(harness, ts)")`. Add a `facts sync` loader from `labels.jsonl` into
   `decision_labels` (`request_id`, `policy`, `label`, `reviewer`, `labelled_at`); the latest human
   label wins.
5. **Registry.** Route `omp.jev_decision` (`source = "spans"`, `match = "name = 'jev.decision'"`).
   `decision.*` signals use `scope = "harness"`, omp aligned, Codex and Claude Code `not applicable`
   (`not emitted` would hide the page). Signals: `decision.volume`, `decision.cost`,
   `decision.latency`, `decision.verdicts`, `decision.shadow_would_block` (shadow mode records a block
   as `flag`), `decision.errors`, `decision.review_agreement`. Add `decisions = "Decision models"` to
   `[views]` after `guardrails`.
6. **Page.** In the Behaviour group after Guardrails: activity, cost and latency; verdicts and shadow mode;
   review agreement. Prompt labelling has its own todo,
   [prompt-label latency and usage](prompt-label-latency-and-usage.md).

Open questions: backfill from the audit files since 2026-09-29; emit spans when `JEV_AUDIT=0`; keep
`jev.subject_hash` in v1.

## Acceptance check

- Every audit record written after deploy has a matching span (same request id, policy, verdict, mode),
  shadow included.
- No span carries subject, state, command, prompt, question or answer text.
- Each decision shares a trace with omp and joins its tool call by `gen_ai.tool.call.id`.
- Enforce-mode tool-call latency is unchanged with the exporter on, off or failing.
- The page renders from snapshots and stays visible with Codex and Claude Code enabled.
