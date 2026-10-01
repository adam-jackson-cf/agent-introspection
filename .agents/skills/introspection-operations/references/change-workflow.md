# Change workflow

Change the facts store (projection, views, registry) or a dashboard signal, including
closing a harness's gap with a new hook event or excluding a signal, without losing
history or breaking harness parity.

## Parity rule

A harness-scoped signal is defined once and reached by every harness it can exist
for: `aligned`, `differs` (another route; the note says how), or `not applicable`
(impossible by construction, such as headless Codex exec having no user to steer).
`facts install` rejects `not emitted`. Never rank or compare harnesses.

## Steps

1. **Define** the change in `src/agent_introspection/facts_sql/`: the projections
   (`select_spans.sql`, `select_logs.sql`), the fact views (`003_views.sql`; NULL means
   no signal, never 0), and the registry (`signal_support.toml`: the signal with its
   question, unit, formula, and a support entry per harness; `codex` covers the three
   Codex surfaces).
2. **Find a route for every harness**, in this order:
   1. native telemetry already in `introspection.spans` / `logs` (inspect key names and
      counts in SigNoz read-only; never print prompt or command text);
   2. a native field the projection drops or does not derive yet (store only
      normalized forms, never raw text);
   3. a new activity-hook event: add it to `docs/hook-events.md`, implement it in
      `hooks.py` with tests (normalize; never keep raw text), register it in the
      harness's installer or extension, and route it with `source = "hooks"`; mark the
      cell `differs` when inferred or when history starts at the hook's install;
   4. none: record the missing surface and the upstream change that would close it,
      and move the signal to `[[excluded]]` (`id`, `view`, `title`, `missing`,
      `reason`), removing its panels.
3. **Name each route** as a `[[route]]` whose predicate names the producer's event or
   span (`expect = "events"` for rare events), so the coverage grid can tell a real
   route from stray rows.
4. **Dashboard**: update `dashboard/server/views.ts` and the view component. All is the
   union of per-harness rows; ratios aggregate before dividing; info notes come only
   from the registry.
5. **Apply**:
   - `uv run python scripts/render-measures.py` (regenerates Measure v3 and the
     data-gaps doc; a test fails when they drift);
   - `bash scripts/run-ci-quality-gates.sh`;
   - `uv tool install --force --reinstall .` after any Python change;
   - `agent-introspection facts install`; `facts backfill --days 90` when a projection
     changed; `OPTIMIZE TABLE introspection.spans FINAL` (and `logs`) when a change
     removes stored text;
   - for a new hook event, reinstall the harness's hooks and run the onboarding
     validation workflow.
6. **Verify** with the health workflow over 90 days, and log anomalies in the plan's
   findings log (finding, evidence, fix, validated result); never patch silently.

## Add a harness

Start from its harness profile (onboarding harness configuration workflow, "Unknown
harness"). A harness is added in one change, after every signal has a route or the
user has decided which signals to exclude (exclusion removes a signal for every
harness). The places a harness is named:

- `signal_support.toml`: `[harnesses]`, its routes, and a support cell per signal;
- `select_spans.sql` / `select_logs.sql`: the service-name allowlists;
- `003_views.sql`: a branch per fact view (usage, tasks, tool calls, user signals,
  model calls);
- `classify.py` (prompt source), `preflight.py` (service list);
- `dashboard/src/contracts.ts` (`HARNESSES`), `dashboard/src/format.ts` (label),
  `dashboard/server/pipeline.ts` (services, parity recount);
- project attribution: an adapter under the onboarding skill's `scripts/adapters/` and
  its producer name in `session-context-runtime.sh`; activity hooks in `hooks.py` when
  the profile needs them.

Then apply and verify as in steps 5 and 6.

Never drop `introspection.spans`, `logs`, `session_projects`, `hook_events`, or
`prompt_labels`: history older than SigNoz's retention and all synced hook and label
history exist only there.

## Done when

- The registry validates, every harness cell is aligned, differs with a note, or not
  applicable (or the signal is excluded), generated docs are current, the gates pass,
  and the coverage grid is healthy.
