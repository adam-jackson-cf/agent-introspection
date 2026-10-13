# Health workflow

Establish whether the facts store, the sync job, and each harness's routes are healthy,
without bypassing a failed check.

## Checks

1. `agent-introspection doctor` (read-only) exits 0 (no check is `fail`), and each
   `warn`, `off`, or `info` has a known reason. It covers the config, SigNoz (every `facts preflight`
   check folded into `signoz`; run `agent-introspection facts preflight` for each
   check's detail), the collector and hooks endpoint, each enabled harness's
   configuration, hooks, session store, and telemetry, the spool, the schedule, the
   prompt-label key, and the dashboard's ClickHouse URL. Add `--drift` to compare each
   enabled harness's telemetry with its saved fingerprint; a `drifted` service or an
   `unregistered` one goes to the onboarding skill's producer onboarding workflow
   (Watch). The onboarding validation workflow explains each status.
2. `agent-introspection facts status`: every loader and snapshot refresher is
   `Scheduled` with no exception, and each harness's freshness is current. Judge an idle
   harness by its prompts and tasks, not log volume (Codex polls `/models` constantly).
3. `agent-introspection facts schedule status`: the job is `installed` and `loaded`.
   On macOS, `legacy` names a job an earlier release installed: reinstall the CLI, then
   `facts schedule install` retires it. On Linux, the timer is `enabled`, and `linger`
   is true, or the timer stops when the user logs out
   (`loginctl enable-linger "$USER"`). Spool backlog (the `*.json` in `hook-inbox/`,
   counted by `agent-introspection spool status`) is a stall only if it stays non-zero
   across syncs;
   `hook-inbox/rejected/` files are batches the collector partly or wholly rejected.
4. `~/.local/share/agent-introspection/sync.log` (rotated to `sync.log.1` and
   `sync.log.2`): the latest `facts sync` result has no error and its `labels` part is
   not `skipped` (the reason says why, for example no OpenRouter key for the job).
   Output the scheduler itself captured is in `sync-launchd.log` beside it on macOS,
   and in the user journal on Linux. `hooks.log` shows no steady stream of hook
   failures.
5. Dashboard Pipeline view, for the window in question:
   - source parity (last 7 days) and All = Σ harnesses recombination;
   - sanitization: no forbidden keys (including tool-intent keys), no stored diagnostic
     text (status message, failure signature) over 160 characters (new rows are capped
     at 80), no home paths in rows loaded after the documented cutoff (`HOME_PATH_CUTOFF`, see
     [Sanitization](../../../../docs/references/facts-store.md#sanitization)), and no raw text
     in `hook_events`;
   - coverage grid: a boundary route with no rows is a possible break; an `events`
     route with no rows is valid; rows under a `not applicable` cell or unclaimed rows
     need a registered stray or a fix;
   - project attribution, and prompt-label coverage (Intent view): prompts arriving as
     `REDACTED` mean that harness's prompt export is off; failed decisions are
     `status = 'failed'` rows in `introspection.prompt_labels`;
   - spool panel, per host: pending, rejected, oldest pending age, last error class,
     CLI version. `endpoint_misconfigured` means the host's `[hooks]` endpoint is
     wrong (HTTP logs URL on the collector's OTLP/HTTP port, not the gRPC port). A host
     whose latest status is older than 5 minutes is **unknown**, never backlog 0: check
     its timer (`systemctl --user list-timers`) or machine before trusting its data;
   - hooks freshness (family-filtered raw hook logs against `hook_events`) and
     attribution freshness (session-project logs against `session_projects`) are
     separate rows; per host, cumulative sent counts should match rows loaded (a gap
     is records lost after the collector acknowledged them).
6. A boundary route going quiet while a similarly named attribute appears is a
   producer rename (omp moved `pi.gen_ai.*` to `omp.gen_ai.*` on 2026-09-29), not
   idleness: fix it with the change workflow.

## Done when

- Every check has evidence, and each failure names the view, harness, and signal it
  affects. Stop at the first failure that makes later checks meaningless.
