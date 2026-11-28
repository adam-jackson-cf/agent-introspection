# Prompt-label latency and Jev usage

Track: priority: CLI data access / problem and friction surfacing

## Problem

This repository's own prompt labelling calls Jev through OpenRouter, but nothing shows its volume, cost,
latency or failures. Labelling failures starve the corrections signal without any visible warning (see
the `mac-cf2` OpenRouter 403 in [operations](operations.md)).

## Evidence

- `src/agent_introspection/classify.py:214` `decide()` posts to Jev and records no latency.
- `src/agent_introspection/facts_sql/001_tables.sql:58` `prompt_labels` has cost, status, reason code
  and attempts, but no `latency_ms`.
- `src/agent_introspection/inbox.py:27` lists the `prompt_labels` structure.
- No `labels.jev_usage` signal exists in `signal_support.toml`.

## Proposed change

1. Add `latency_ms UInt32 DEFAULT 0` to `prompt_labels`. Time the post in `classify.decide()`,
   including the failure path, and carry it through `label()`, `UNLABELLED` and `_row`. Add it to
   `inbox.py`. Test in `tests/test_classify.py`.
2. Add a `labels.jev_usage` signal with `scope = "system"`: labelling calls, cost, failures by reason
   code, and latency. Show it on the Pipeline page, or on the decision models page once that exists.

## Acceptance check

- `latency_ms` is filled on success and on failure.
- The signal renders, and a run of failed labels shows as failures by reason code.
