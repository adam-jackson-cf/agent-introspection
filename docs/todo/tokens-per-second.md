# Tokens per second

How to measure tokens per second from the telemetry the harnesses already send, and how
to show it on the Provider page as an indicator of provider performance. It is an
addition: latency and time to first token stay.

## Short answer

- **It exists, but is hidden.** The registry already has `provider.throughput`
  ("Output throughput", P50 of output tokens ÷ (duration − time to first token)). The
  Provider page shows it as a column of "Where does provider time go?", but only when
  every enabled harness reaches it. Codex's cell is `not emitted`, because its
  `response.completed` log has no per-call duration. This machine enables Codex, so
  the parity rule hides the column.
- **Codex can reach it.** Codex's spans hold everything needed (checked read-only on
  2026-10-13, below). With a Codex route, the measure shows for every harness here.
- **Page:** Provider. It answers "is the provider performing?", and tokens per second
  belongs beside latency and time to first token. Make it the lead speed figure.

## Definition

There are two rates. Show the first as the headline and keep the second as a diagnostic.

| Rate                             | Formula                                              | What it shows                                                           |
| -------------------------------- | ---------------------------------------------------- | ----------------------------------------------------------------------- |
| **Output rate** (headline)       | output tokens ÷ call seconds                         | Tokens delivered per second of the call, including queueing and prefill |
| **Generation rate** (diagnostic) | output tokens ÷ (call seconds − time to first token) | Speed once tokens flow; closer to raw decode speed                      |

- **Output tokens** include reasoning tokens for every producer, because the provider
  generated them whether or not they were shown (`usage.output_tokens` is defined the
  same way).
- **Call seconds** run from the request being sent to the response completing, for
  one model call.

### Why the generation rate is only a diagnostic

Models that do not stream their reasoning (OpenAI reasoning models: Codex, and omp on
OpenAI models) report a time to first token that includes the reasoning time.
Subtracting it removes the time during which the reasoning tokens were generated, but
the token count still includes them. The generation rate then overstates speed.

On this machine's Codex calls (3 days, 2,121 calls), output tokens were 40% reasoning:

| Rate                                        | Tokens per second |
| ------------------------------------------- | ----------------- |
| Output rate                                 | 47                |
| Generation rate, all output tokens          | 70                |
| Generation rate, visible output tokens only | 42                |

The output rate does not depend on how a provider streams, so it is the same signal for
every harness and model.

## Averaging

- **Average by summing first, never by averaging per-call rates.** Short calls (a few
  tokens in about a second) are numerous and noisy, and would dominate a mean of rates.
  Use the token-weighted rate: Σ output tokens ÷ Σ call seconds over the calls in the
  window or bucket. This follows the repository rule that ratios aggregate before
  dividing.
- **Show the typical call beside it.** P50 of the per-call rate, over calls with at
  least 50 output tokens, plus P10 for the slow tail. On this machine's Codex calls
  the P50 was 44 tokens per second, against 47 token-weighted.
- **Suppress thin buckets.** A bucket with fewer than 5 calls shows "—", not a noisy
  number.

## What the panel shows

| Element              | Value                                                                                                                       |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| KPI: last hour       | Token-weighted output rate over the last complete hour of loaded data, labelled with its as-of time                         |
| KPI: selected window | Token-weighted output rate over the whole window                                                                            |
| Trend                | One point per bucket: day buckets, as the page's other trends ("Daily trend"); hour buckets for windows of two days or less |
| Breakdown            | By model, with calls and output tokens per row, and P50 and P10 per call                                                    |

- **"Last hour" is the last complete hour that has loaded.** The dashboard trails
  SigNoz by up to about 10 minutes (the snapshot rebuild), so a literal last 60 minutes
  would be partly empty.
- **Break down by model, not by harness.** Speed is a property of the model and its
  provider. The harness selector filters as on every page; harnesses are never ranked.

## Data per harness

| Harness     | Output tokens                                                             | Call seconds                                                                                      | Time to first token                                                             |
| ----------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| Claude Code | `claude_code.llm_request` span                                            | that span's duration                                                                              | `ttft_ms` on the span                                                           |
| omp         | `gen_ai.usage.output_tokens` on `chat` spans                              | the `chat` span's duration                                                                        | `gen_ai.response.time_to_first_chunk`                                           |
| Codex       | `gen_ai.usage.output_tokens` on the usage-bearing `handle_responses` span | from the `try_run_sampling_request` start to the end of the usage-bearing `handle_responses` span | `ttft_ms` on the `codex.sse_event` `response.completed` log, joined (see below) |

Claude Code and omp already feed `model_calls` with duration and time to first token
(`dashboard/server/views.ts`, Provider query, `tps_p50`).

### The Codex route (checked read-only, 2026-10-13, codex-app-server, 3 days)

- **Span structure.** Each attempt is a `try_run_sampling_request` span with one child
  `receiving_stream` span (2,126 of 2,126). That has one child `handle_responses` span
  carrying `gen_ai.usage.output_tokens` and `codex.usage.reasoning_output_tokens`.
  2,121 attempts have exactly one usage span; 5 failed attempts have none.
- **End the call at the usage span.** Neither the attempt span nor the stream span is
  the call duration: both run on after the response completes, by 0.2 s at P50 and
  9.9 s at P90, most likely while tools finish. The call runs from the attempt's start
  to the end of the usage span (P50 4.5 s, P90 12.7 s).
- **Time to first token comes only from the log.** The `response.completed` log has no
  trace or span ID, and its `conversation.id` is a different ID space from the span's
  `thread.id`. It joins by an equal output token count and the nearest time within
  3 s. That matched 2,119 of 2,121 calls, a median of 9 ms apart.
- **Codex cells:**
  - Output rate: `aligned`, from spans alone.
  - Generation rate: `differs`, because of the joined time to first token and its
    reasoning time.
- **Other Codex surfaces.** Check `codex_exec` and `codex_cli_rs` carry the same three
  span names before marking them.

## Implementation

1. **Projection.** `select_spans.sql` keeps only some Codex span names, and
   `receiving_stream` is not among them. Add it; it is about one span per model call.
   Alternatively, join the usage span to its attempt by trace and time containment.
   Run `facts install`, then `facts backfill --days 90`.
2. **`model_calls`** (`003_views.sql`). Give the Codex branch a duration: build calls
   from the attempt, stream and usage spans, and join the `response.completed` log for
   time to first token and the error message.
   - This also lets `provider.latency` use the real call duration for Codex. It
     currently uses `run_sampling_request`, which includes retries and tool draining
     (`differs`).
3. **Registry** (`signal_support.toml`):
   - Redefine `provider.throughput` as the token-weighted output rate, aligned for all
     three harnesses, and give it `requires = ["model_usage", "model_outcome"]`.
   - Add `provider.generation_rate` for the diagnostic rate, with the
     hidden-reasoning caveat in its formula and Codex `differs`.
   - Run `scripts/render-measures.py`.
4. **Dashboard:**
   - `views.ts`: per-bucket and per-model Σ tokens ÷ Σ seconds, P50 and P10, call
     counts, and the last-hour figure.
   - `Provider.tsx`: two KPIs in "At a glance", a trend panel, and the by-model
     columns. Info notes come from the registry.
5. **Verify.**
   - Per harness, Σ output tokens in `model_calls` equals the usage records over the
     same window.
   - Call seconds are never negative.
   - The coverage grid shows the new Codex route healthy.
