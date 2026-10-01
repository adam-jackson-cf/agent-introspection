-- Curated span projection. The window placeholder is replaced with the loader or backfill
-- time predicate over the SigNoz source columns `timestamp` and `duration_nano`.
-- Codex emits almost all of its spans as internal plumbing (older builds also
-- emitted h2/tokio frame spans), so only the task, sampling, and user-signal span
-- names below are kept, plus `handle_responses` spans that carry token usage. Status messages can
-- hold raw tool output, so only a normalized first error line is kept: home
-- directories become `~`, digit and hex runs become `N`, capped at 160
-- characters. Span names get the same home-directory redaction.
WITH
    arrayFilter(l -> l != '', splitByChar('\n', distributed_signoz_index_v3.status_message)) AS status_lines,
    -- Only a diagnostic line is kept; with none the stored status is empty, so
    -- arbitrary raw output never reaches the durable table. The most specific line
    -- wins, as in the log projection: a stack trace's final exception line, else the
    -- first diagnostic line that is not a generic header or frame, else the first.
    arrayLast(
        l -> match(l, '^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit|Interrupt)(\\s*\\[[A-Z_]+\\])?(:|$)'),
        status_lines
    ) AS exception_line,
    arrayFirst(
        l -> match(l, '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)')
            AND NOT match(l, '^\\s*(Traceback \\(most recent call last\\):|triggerUncaughtException\\(|at |File "|node:internal|throw |raise |Script (failed|error:?)\\s*$|error: script "[^"]*" exited|={3,}|- Status: failed)'),
        status_lines
    ) AS specific_line,
    multiIf(
        exception_line != '', exception_line,
        specific_line != '', specific_line,
        arrayFirst(
            l -> match(l, '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)'),
            status_lines
        )
    ) AS status_line,
    -- The stored text is capped at 160 characters. When the keyword lies past the cap,
    -- keep the line from the keyword on, so the stored text is always diagnostic.
    if(
        match(substring(status_line, 1, 160), '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)'),
        status_line,
        extract(status_line, '(?i)((error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found).*)')
    ) AS status_window
SELECT
    serviceName AS harness,
    timestamp AS ts,
    toString(traceID) AS trace_id,
    spanID AS span_id,
    parentSpanID AS parent_span_id,
    replaceRegexpAll(distributed_signoz_index_v3.name, '/Users/[^/ ]+', '~') AS name,
    duration_nano AS duration_ns,
    has_error,
    status_code_string AS status_code,
    substring(
        replaceRegexpAll(replaceRegexpAll(status_window, '/Users/[^/ ]+', '~'), '[0-9a-f]{8,}|\\d+', 'N'),
        1,
        160
    ) AS status_message,
    -- omp renamed its `pi.gen_ai.*` attributes to `omp.gen_ai.*` on 2026-09-29; both
    -- are stored under the original `pi.gen_ai.*` names so every view reads one key.
    -- Tool intent text is dropped under any prefix.
    mapFilter(
        (k, v) -> k NOT IN (
            'gen_ai.tool.description', 'user_prompt', 'prompt', 'user.email',
            'user.account_id', 'user.account_uuid', 'user.id', 'organization.id'
        ) AND NOT endsWith(k, '.tool.call.intent'),
        mapApply((k, v) -> (replaceRegexpOne(k, '^omp\\.gen_ai\\.', 'pi.gen_ai.'), v), attributes_string)
    ) AS attrs_string,
    mapApply((k, v) -> (replaceRegexpOne(k, '^omp\\.gen_ai\\.', 'pi.gen_ai.'), v), attributes_number) AS attrs_number,
    mapApply((k, v) -> (replaceRegexpOne(k, '^omp\\.gen_ai\\.', 'pi.gen_ai.'), v), attributes_bool) AS attrs_bool,
    now64(3) AS loaded_at
FROM signoz_traces.distributed_signoz_index_v3
WHERE serviceName IN ('codex-app-server', 'codex_cli_rs', 'codex_exec', 'oh-my-pi', 'claude-code')
    AND (
        serviceName NOT LIKE 'codex%'
        OR distributed_signoz_index_v3.name IN (
            'session_task.turn', 'session_task.run', 'run_sampling_request', 'try_run_sampling_request',
            'turn/start', 'turn/interrupt', 'turn/steer'
        )
        OR (distributed_signoz_index_v3.name = 'handle_responses' AND mapContains(attributes_number, 'gen_ai.usage.input_tokens'))
    )
    AND ({window})
