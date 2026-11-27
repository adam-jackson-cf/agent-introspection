# Design rationale

This page holds the reasons behind non-obvious choices in the code: constraints,
invariants, upstream quirks, and the reasons for limits, timeouts, and orderings. It
does not repeat what the other references already explain; each section names the file
the reason applies to.

## Facts store and refreshers

`src/agent_introspection/facts.py`

- The SigNoz ClickHouse container runs near its 4 GiB memory cap, but the default profile lets one query use 10 GB. A heavy refresh is then killed (Code 241) instead of slowing down.
- So every refresher gets a realistic per-query ceiling, and grouping, sorting, and joining spill to disk at half of it. A refresh then finishes slower rather than failing.
- Do not cap `LOAD_SETTINGS` below 1.5 GiB: the log sweep peaks near 1.05 GiB. Loaders, sweeps, and backfill also use small blocks and one insert thread.
- Refreshers run with two threads, so dashboard queries keep the CPU.
- Every refresher is created `EMPTY` because an 18-refresher burst at creation once exhausted its retries and left history empty.
- A refresh still running after its wait budget is not retried. Another request would queue a second full run behind the first.
- A sweep retries after a full hour, so the retry follows its own next slot instead of landing on a neighbouring sweep 10 minutes later.
- History offsets are minute-only because `_REFRESH_SCHEDULE` rewrites a single-unit `OFFSET` when `facts verify` builds its scratch copy.
- Hook-event loaders key on `observed_timestamp` alone. A record's own time is the producer's clock and can trail by a whole outage.
- Spans are exported when they end, and turns can run for more than a day. So the span loaders select by end time, and the span sweep is bounded only by retention.
- A delayed collector export keeps its original observed time, so rows can land after the minute loaders' 30-minute window. The hourly sweeps exist to catch them.
- Install drops the removed project-attribution objects after the refreshers that still wait on the old loaders, and drops loaders and the view before the table.
- `registry_harnesses` reads only the `[harnesses]` table and never runs at import. A broken registry then cannot stop a command that does not read it, such as `hook`.
- `facts verify` drops the legacy fixed-name scratch database only after an hour without changes, because an older-version verify in another worktree may still be building in it.
- `facts verify --compare` leaves out the newest 15 minutes because the loaders keep adding rows there. It leaves out the oldest day because rows at the edge lack their neighbours.
- The HTTP runner sends each statement in its own POST, because ClickHouse's HTTP interface runs one statement per request.
- It sets `wait_end_of_query=1`, which buffers the result, so a failure mid-query arrives as an HTTP error rather than a truncated body.

## Facts SQL

`src/agent_introspection/facts_sql/`

- `001_tables.sql`: overlapping loader windows insert some rows more than once. The tables use `ReplacingMergeTree` to collapse them, so every read uses `FINAL`.
- `003_views.sql`: `tool_family` groups each harness's tool names by what the tool does, so one behaviour is one finding whatever harness produced it.
- `003_views.sql`: `failure_class` generalizes quoted values and paths, so the same failure on different files is one class.
- `003_views.sql`: zsh prefixes errors of `eval`ed commands with `(eval):N:`. That hides that two failures are one, so the prefix is dropped.
- `003_views.sql`: a line that is only shell punctuation before "not found" (a Markdown rule typed as a command) names no failure and gets no class.
- `003_views.sql`: Codex hook events share one producer family across app-server, CLI, and exec. `hook_rows` takes the harness from the session's Codex logs, and it is empty until they arrive.
- `004_labels.sql`: a task takes the latest label submitted within five seconds of its start, before or after. The prompt event and the task start are stamped by different producers, and 14 days of matches fit in that span.
- `004_labels.sql`: an older label belongs to an earlier prompt, so the task stays unlabelled instead.
- `004_labels.sql`: `corrected_next` needs both a correction probability of at least 0.5 and a named kind. They are separate questions, so both must agree.
- `004_labels.sql`: the view reads the `task_outcomes` snapshot rather than the live view. Labels exist only from 2026-09-30, well inside the snapshot's 91 days.
- `004_labels.sql`: ClickHouse `ASOF` joins allow one inequality, so the lower time bound is filtered after the join.
- `select_hook_events.sql`: columns follow the table's order, with added columns last, because backfills insert by position.
- `select_logs.sql` and `select_spans.sql`: with no diagnostic line, the stored text is empty rather than an arbitrary output line that could carry raw content.
- `select_spans.sql`: omp renamed its `pi.gen_ai.*` attributes to `omp.gen_ai.*` on 2026-09-29. Both are stored under the original `pi.gen_ai.*` names, so every view reads one key.

## Hooks and spool

`src/agent_introspection/hook_runtime/activity-shim.sh`, `hooks.py`, `hook_install.py`, `spool.py`, `inbox.py`

- The shim hands the envelope over a pipe, not a temporary file. It uses `printf`, a shell builtin, so the envelope never appears in a process list.
- `hooks.py` mirrors its credential redactions in the SQL projections, which use RE2. RE2 has no lookahead, so the long-token rule marks, unmarks, then redacts.
- When `hooks.py` sums a turn's transcript usage, streamed entries that share a message ID count once.
- `harness remove-hooks` unregisters one harness's hooks but leaves the shared runtime, because the other harnesses still run it.
- A hook process sends at most one batch (`max_batches`), because it must not outlast its harness's patience.
- An invalid config gives the hook path no endpoint. The records then stay spooled, and only the error class is logged.
- `inbox.py` keeps each INSERT under ClickHouse's 256 KiB query limit, and cuts larger rows by encoded size.
- `inbox.py` declares column types for some tables because JSON inference would read nested objects as tuples, not the declared `Map` columns.

## Dashboard server

`dashboard/server.ts`, `dashboard/server/`

- `server.ts` shares route counts and Pipeline's whole-history checks for 10 minutes, the cadence on which the fact snapshots rebuild.
- The shared cache drops a failed result at once, so the next load retries the query.
- The snapshot horizon check uses `SNAPSHOT_DAYS + 1`. A "Last 90 days" window whose start was computed a moment before the request then still reads the snapshots.
- When every branch of a view fails, the server reports ClickHouse as down, not one bad query.
- The ClickHouse address is resolved only when no query function is injected, so tests need no ClickHouse address.
- `clickhouse.ts` runs at most `BRANCH_CONCURRENCY` (3) branches at once. ClickHouse here runs near its memory cap, and a `UNION ALL`'s peak memory is the sum of its branches.
- The small pool also keeps connections few. Bun's HTTP client stalls about one second on some new connections to OrbStack, and a reused keep-alive connection avoids the stall.
- The registry, route counts, and a session use the all-or-nothing batch, because a caller cannot use them in part. Views use `batchSettled`, where one failed branch only empties its dataset.
- `clickhouse.ts` refuses the retired config key, as the CLI does, and never falls back past it to the default address.
- `interventions.ts` buckets by Europe/London calendar day to match the daily facts buckets.
- The application day mixes events from before and after, so neither before/after window counts it. The shorter side of the range sets both windows.
- A proposal of the retired repeated-correction detector has no cluster to match, so its counts and rates are NULL rather than a measured 0.
- Findings rows without a harness list come from older stores, so they belong to All only.
- `pipeline.ts` reads activity hooks through `hook_rows` without `FINAL`, because that view already reads `hook_events` `FINAL`.
- Route predicates read raw attributes that no snapshot keeps, so the route count is the one raw scan. Its 90-day lookback keeps it off all history.
- The parity recount covers only the last 7 days of the window, because a raw recount scans SigNoz's full trace table.
- The parity recount ends 10 minutes before now, so rows the loaders have not copied yet do not read as drift.
- `recombine` leaves out a check missing one side, because it would read as 0 = 0.
- A failed delivery query leaves the delivery counts empty and shown as unknown, never as a clean result.
- `record-route.ts` ages spool status by the collector's `observed_timestamp`, never the producer's own `timestamp`. A skewed host clock must not make a silent host look fresh.
- A stale host's backlog is NULL and its state `unknown`, so an old report never reads as a backlog of 0.
- `PRODUCER_PRUNE` (4 days) only prunes partitions. It must never be tighter than the loaders' 3-day sweep, or records the sender counted would drop out of received.
- `refreshers.ts` leaves the hourly sweeps out of a view's "as of" time. A sweep only recovers delayed rows, so counting it would understate freshness.
- A failing daily history refresh only ages older days, so it is reported apart as `historyStale`.
- `src/agent_introspection/cli_dashboard.py` waits up to 120 seconds, because a cold view with no cached route counts can take tens of seconds on a large store.
- `cli_dashboard.py`, `doctor.py`, and `signoz_detect.py` open loopback URLs with no proxy, so a proxy from the environment never sees these requests.

## Dashboard UI

`dashboard/src/`

- `app-data.ts`: a view request is abandoned after 120 seconds, a user decision of 2026-10-02.
- `app-data.ts` and `styles.css`: after a failed refresh the last good response stays on screen, dimmed, so it is not read as current.
- `App.tsx`: a link can name a harness this machine does not use, so the page falls back to All.
- `charts.tsx`: ClickHouse sends 64-bit integers as strings, so table sorting parses numeric strings.
- `charts.tsx`: daily charts fill every day between the first and last row, so gaps stay visible.
- `charts.tsx` and `components.tsx`: when a chart's series are not harnesses, the panel's chips turn neutral. Only one set of keys then carries colour.
- `components.tsx`: a panel whose dataset failed shows the failure, because an empty dataset would read as zero.
- `format.ts` and `tokens.ts`: colour follows the harness everywhere and never follows rank. Non-harness series use their own slots, so they never share a harness colour.
- `tokens.ts` and `tokens.css` mirror `docs/ux/DESIGN.md`. Change `DESIGN.md` first, then `tokens.css`, so values live in one file. Spare harness slots are kept for candidate harnesses.
- `styles.css`: the custom range opens beside the dropdown, so choosing it never adds a row or moves the content.
- `feature-audit.test.ts`: a recorded title may move to another page, but it may only disappear when its audit entry names a decided change.
- `views/Interventions.tsx`: proposals are drafted by hand, never on a schedule, so the empty state says how to draft one.
- `scripts/ux-probe/probe.ts` reads navigation links only. Per-row links are row identifiers, so no session ID reaches the evidence.
- `probe.ts` also stops waiting on "Query failed", so the failed-query check reports it instead of a 180-second timeout.
- `docs/ux/rubrics/ux-page.yaml`: its regexes are used verbatim by `scripts/ux-probe`.

## Doctor

`src/agent_introspection/doctor.py`, `doctor_harness.py`

- A check that raises becomes a failure naming only the exception class. One check never stops the report, and no error message carries a value into it.
- A closed OTLP/gRPC port is only `info`, because every bundled profile exports over OTLP/HTTP. It becomes a warning when an export setting is satisfied over gRPC.
- Doctor counts rows for every registry harness, so a disabled harness that still sends is seen.
- The installed dashboard service never sees the shell's environment. Only a dashboard run by hand reads `INTROSPECTION_CLICKHOUSE_URL`.
- A missing or different service name only warns, because producers name their own service by default. Any other export setting turns on or routes export, so it fails.
- Prompt export being off is a choice, never a failure.
- A pasted shell path keeps `~/` outside the quotes, so the shell still expands it.

## Harness configuration

`src/agent_introspection/config_writers.py`, `harness_config.py`, `profiles.py`, `harness_profiles/`

- `config_writers.py` regenerates a TOML table only when it cannot edit key lines in place: sub-tables, dotted keys, or a key across lines. The result must re-parse to the intended document.
- A `#` starts a comment only when the statement parses the same without the rest of the line. A `#` inside a multi-line string is never taken for one.
- Files are replaced through a same-directory temporary file renamed over the original, with its permissions, after a timestamped backup.
- `harness_config.py` reports prompt-export settings but never changes them unless asked, because prompt export is opt-in.
- `harness list` never runs a binary whose profile has no `version_args`, so it never starts a harness session.
- `profiles.py` skips a path whose variable is unset or empty, so a harness's config-directory variable falls through to its default path.
- `~` expands only as a path's first part, as a shell does, never inside a variable's value.
- A home matched by `home_globs` never stands in for the default home when nothing else is chosen.
- `codex.toml`: Codex reads `[otel]` from each Codex home and ignores it in a project's `.codex/config.toml`. So `$CODEX_HOME` and an existing `~/.codex` are both configured.
- `codex.toml`: Orca keeps one Codex home per account, hence the extra home globs.
- `opencode.toml`: call and turn counts use distinct span IDs, because SigNoz stored some opencode spans twice on the busiest days.
- `opencode.toml`: the payload fields came from 1.2.6 type definitions, but the spans came from a newer build. Re-check both on the build meant to run.

## Config

`src/agent_introspection/config.py`

- Defaults name `127.0.0.1`, not `localhost`. Some clients resolve `localhost` to `::1` first, while SigNoz may publish its ports on `127.0.0.1` only.
- Retired keys are still rejected, so stale settings never look active. The error names them as safe to delete, and `setup` removes them.
- `*.orb.local` counts as local because it is OrbStack's container DNS, which resolves only on this Mac. Other container runtimes publish ClickHouse on a loopback port instead.

## Schedule, services, and CLI steps

`src/agent_introspection/schedule.py`, `dashboard_service.py`, `cli_facts.py`, `cli_producers.py`, `setup_flow.py`, `workflow.py`

- `schedule.py` refuses to go on when a loaded legacy job cannot be stopped, so two jobs never sync at once. A retired legacy job is never put back, for the same reason.
- A failed install puts the replaced job back, but reloads it only if it was loaded before. A job the user stopped stays stopped.
- The job passes `--log` because launchd's own file redirection cannot stamp lines with a UTC time.
- The job's `PATH` includes the ClickHouse password command's executable, because the scheduler cannot pass the password variable to the job.
- A `launchctl` or `systemctl` call that hangs times out and fails instead of stalling the command.
- `dashboard_service.py`: systemd does not unquote `WorkingDirectory=`, so only its `%` specifier is escaped.
- `dashboard_service.py`: when a new launchd agent will not start, the running one is put back.
- `cli_facts.py`: `facts schedule install` resolves the config path now, so the job reads the same file later.
- `cli_facts.py`: `facts install` creates the workflow store even when a refresh failed, because the dashboard reads it. A store error must not hide the install failure.
- `cli_facts.py`: Jev being unavailable never fails `facts sync`.
- `cli_facts.py`: `facts sync` runs the hook-event loader right after the drain, so the records it delivered are in the table before findings run.
- `cli_producers.py` refuses a bad `--write` path, or a service that cannot name a file, before any query, so a save is never partial.
- `setup_flow.py` refuses a harness key the facts registry does not know at the config step (omp is `oh-my-pi`). Otherwise a dry run passes and the real run stops at `facts`.
- `setup_flow.py` resolves the config path, so a symlinked config is written through, never replaced.
- `workflow.py` uses a rollback journal, not WAL, so read-only openers such as the dashboard can read the store without creating a shared-memory file.

## Classification and drafting

`src/agent_introspection/classify.py`, `drafting.py`, `review.py`, `findings.py`, `dashboard/server/views.ts`, `producers.py`, `preflight.py`

- `classify.py` names the pinned Jev version, so a newer Jev never labels prompts until the questions are re-evaluated. It still checks the resolved model in case OpenRouter maps the name elsewhere.
- HTTP errors from Jev carry no provider body, because the body can echo the input.
- A failed decision still inserts a row with the same label columns, so every row inserts with one column set.
- The OpenRouter key command runs only when a prompt is pending.
- `drafting.py`: Codex reports reasoning tokens inside `output_tokens`, so only input and output are recorded, with availability `partial`. Cached input tokens are part of `input_tokens`.
- `review.py` charges the larger of `token_count` and the sum of every reported component. A low `token_count` must not hide components that already exceed it.
- `findings.py` excludes evaluation workspaces (a working directory or failure path under a temporary directory), because they hold test workloads, not work to improve.
- A whole session is excluded only when every call that recorded a working directory ran in one. A Codex call records one only when its arguments name it, so one temporary call never hides a session.
- A failure class naming only an error type or a header (`ShellError`, `Traceback ...`) cannot be root-caused. It stays emerging, so no proposal is drafted from it.
- Retired detectors' active findings go dormant on the next refresh and stay as history.
- `views.ts` mirrors `findings.GENERIC_CLASS` and `findings.EVALUATION_PATH` as SQL literals, so the dashboard and the findings agree.
- `views.ts` leaves waiting and status-polling tools out of repeat counts, because they repeat identical calls by design.
- `producers.py` keeps far more names in a fingerprint than `producers profile` prints, so drift is judged on complete lists in all but extreme producers.
- `producers.py` writes TOML strings as JSON strings plus an escaped DEL, because TOML also escapes DEL.
- `preflight.py` requires ClickHouse 24.10, the first release where refreshable materialized views need no experimental setting. `facts install` sets none.
- `preflight.py` keeps column-level grants with their parentheses, so they never count as table-wide. A revoke on the facts database or a table counts against the privilege, so partial revokes fail closed.
- `preflight.py` prefers `CHECK GRANT`, which resolves roles and partial revokes on the server, and parses `SHOW GRANTS` only on older servers.

## Development checks

`pyproject.toml`, `.pre-commit-config.yaml`, `scripts/`, `docs/ux/feature-audit/.markdownlint.jsonc`

- `pyproject.toml` turns `ResourceWarning` into an error, because a leaked SQLite connection surfaces only as an unraisable warning at garbage collection.
- `.pre-commit-config.yaml`: the command-shape parity hook needs the local SigNoz ClickHouse, which CI lacks, so it runs only when facts SQL changes.
- `scripts/lib/python_tool_scope.py` includes untracked files, so a new package cannot dodge coverage until it is committed. A directory that is not a Git checkout, such as canary fixtures, is walked instead.
- `scripts/lib/capabilities_render.py`, `harness_render.py`, and `measures_render.py` pad tables the way Prettier does, so regenerated files stay clean under the format check.
- `harness_render.py` generates `dashboard/src/harnesses.generated.ts`, so the dashboard never copies harness keys and labels by hand.
- `docs/ux/feature-audit/.markdownlint.jsonc` turns off line length, because each audit row holds a full formation chain.

## Fresh-install sandbox

`scripts/fresh-install/`

- `sandbox.sh` serves the stub collector with uv's own Python. A Homebrew Python started over SSH was seen to accept no loopback connections on macOS, so the collector looked down.
- `sandbox.sh` sets the bun cache variable only when one is found. An empty value makes bun cache into the working directory.
- `destroy` deletes only the directory that `create` recorded as its own.
- `signoz-stub.sql` creates SigNoz's distributed tables as plain `MergeTree` tables under the same names, with the span table's camel-case columns as aliases, as in SigNoz.

## SigNoz onboarding files

`.agents/skills/introspection-onboarding/scripts/signoz/`

- `install-signoz.sh`: a container, project, or volume listing that fails stops the install rather than passing the check.
- `otel-collector-opencode-filter.yaml`: on 2026-10-13, the 29 span names the filter matches were 16.57M of opencode's 17.88M spans (93%).
- `sql.execute` spans carry `db.query.text`, which can hold SQL text from opencode's own database. No other opencode span carried it.
- The filter keeps every span that a capability hint in `signal_support.toml` needs, such as `Tool.execute` and `LLM.run`.
- Children of dropped spans are kept and point at a parent the trace no longer has.
- The processors go first in the traces pipeline, so dropped spans also make no span metrics. A processor list is replaced whole, so the existing processors are restated.
- For a Foundry install, the processors go in `casting.yaml`, never the generated collector file, because `foundryctl forge` rewrites it.
