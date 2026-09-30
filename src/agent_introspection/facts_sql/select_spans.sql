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
    -- arbitrary raw output never reaches the durable table.
    arrayFirst(
        l -> match(l, '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)'),
        status_lines
    ) AS status_line
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
        replaceRegexpAll(replaceRegexpAll(status_line, '/Users/[^/ ]+', '~'), '[0-9a-f]{8,}|\\d+', 'N'),
        1,
        160
    ) AS status_message,
    mapFilter(
        (k, v) -> k NOT IN (
            'gen_ai.tool.description', 'pi.gen_ai.tool.call.intent', 'user_prompt', 'prompt', 'user.email',
            'user.account_id', 'user.account_uuid', 'user.id', 'organization.id'
        ),
        attributes_string
    ) AS attrs_string,
    attributes_number AS attrs_number,
    attributes_bool AS attrs_bool,
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
