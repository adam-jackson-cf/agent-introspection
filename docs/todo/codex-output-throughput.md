# Codex output throughput

Track: priority: CLI data access / problem and friction surfacing

## Problem

`provider.throughput` ("Output throughput") is hidden on any machine that enables Codex, because Codex's
cell is `not emitted` and the parity rule hides the column. Codex spans hold everything needed. The
current formula is also a per-call P50 of output tokens / (duration − time to first token), which
overstates speed for models that do not stream their reasoning.

## Evidence

- `src/agent_introspection/facts_sql/signal_support.toml` `provider.throughput` (about line 1019): Codex
  `alignment = "not emitted"`.
- `src/agent_introspection/facts_sql/select_spans.sql:102-105` keeps `run_sampling_request`,
  `try_run_sampling_request` and usage-bearing `handle_responses`, but not `receiving_stream`.
- `dashboard/server/views.ts:486-487` computes the per-call P50 rate (`tps_p50`).
- A read-only check on 2026-10-13 (codex-app-server, 3 days, 2,121 calls) found:
  1. Each `try_run_sampling_request` attempt has one `receiving_stream` child, which has one
     `handle_responses` child carrying the usage.
  2. The attempt and stream spans run on after the response completes (P50 0.2 s, P90 9.9 s), so the call
     ends at the usage span.
  3. Time to first token comes only from the `response.completed` log, joined by equal output tokens
     and the nearest time within 3 s (2,119 of 2,121 matched).
  4. Output tokens were 40% reasoning. Output rate 47 tokens/s; generation rate 70 with all output
     tokens, 42 with visible tokens only.

## Proposed change

1. Define two rates. Output rate (headline): output tokens / call seconds. Generation rate (diagnostic):
   output tokens / (call seconds − time to first token), with the hidden-reasoning caveat.
2. Aggregate as Σ output tokens / Σ call seconds. Show P50 and P10 per call over calls with at least 50
   output tokens, and show "—" for buckets with fewer than 5 calls.
3. Admit `receiving_stream` in `select_spans.sql`, or join the usage span to its attempt by trace and
   time. Give the Codex branch of `model_calls` (`003_views.sql`) a call duration from the attempt
   start to the usage span end, and join the log for time to first token. This also gives
   `provider.latency` a real Codex call duration.
4. Redefine `provider.throughput` as the output rate, aligned for all three harnesses, and add
   `provider.generation_rate` with Codex `differs`. Run `scripts/render-measures.py`.
5. On the Provider page, add last-complete-hour and window KPIs, a trend and by-model columns. Break down
   by model, not by harness.
6. Check that `codex_exec` and `codex_cli_rs` carry the same span names before marking them.

## Acceptance check

- Per harness, Σ output tokens in `model_calls` equals the usage records over the same window.
- No call has negative seconds.
- The coverage grid shows the new Codex route healthy, and the throughput column shows with Codex enabled.
