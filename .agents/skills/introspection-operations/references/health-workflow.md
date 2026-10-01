# Health workflow

Establish whether the facts store, the sync job, and each harness's routes are healthy,
without bypassing a failed check.

## Checks

1. `agent-introspection facts preflight` passes (the local SigNoz is reachable and
   compatible).
2. `agent-introspection facts status`: every loader and snapshot refresher is
   `Scheduled` with no exception, and each harness's freshness is current. Judge an idle
   harness by its prompts and tasks, not log volume (Codex polls `/models` constantly).
3. `agent-introspection facts schedule status`: the job is loaded. A non-zero inbox
   backlog is a stall only if its files are valid (`invalid` counts unparseable files,
   which are never deleted).
4. `~/.local/share/agent-introspection/projects-sync.log`: the latest `facts sync`
   result has no error and its `labels` part is not `skipped` (the reason says why,
   for example no OpenRouter key for the job). `hooks.log` shows no steady stream of
   hook failures.
5. Dashboard Pipeline view, for the window in question:
   - source parity (last 7 days) and All = Σ harnesses recombination;
   - sanitization: no forbidden keys (including tool-intent keys), no status message
     over 160 characters, no home paths, and no raw text in `hook_events`;
   - coverage grid: a boundary route with no rows is a possible break; an `events`
     route with no rows is valid; rows under a `not applicable` cell or unclaimed rows
     need a registered stray or a fix;
   - project attribution, and prompt-label coverage (Intent view): prompts arriving as
     `REDACTED` mean that harness's prompt export is off; failed decisions are
     `status = 'failed'` rows in `introspection.prompt_labels`.
6. A boundary route going quiet while a similarly named attribute appears is a
   producer rename (omp moved `pi.gen_ai.*` to `omp.gen_ai.*` on 2026-09-29), not
   idleness: fix it with the change workflow.

## Done when

- Every check has evidence, and each failure names the view, harness, and signal it
  affects. Stop at the first failure that makes later checks meaningless.
