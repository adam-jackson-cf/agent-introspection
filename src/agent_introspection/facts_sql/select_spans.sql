-- Curated span projection. The window placeholder is replaced with the loader or backfill
-- time predicate over the SigNoz source columns `timestamp` and `duration_nano`.
-- Codex emits ~95% of its spans as internal plumbing; those names are dropped
-- except `handle_responses` spans that carry token usage.
SELECT
    serviceName AS harness,
    timestamp AS ts,
    toString(traceID) AS trace_id,
    spanID AS span_id,
    parentSpanID AS parent_span_id,
    name,
    duration_nano AS duration_ns,
    has_error,
    status_code_string AS status_code,
    status_message,
    mapFilter(
        (k, v) -> k NOT IN (
            'gen_ai.tool.description', 'user_prompt', 'prompt', 'user.email',
            'user.account_id', 'user.account_uuid', 'user.id', 'organization.id'
        ),
        attributes_string
    ) AS attrs_string,
    attributes_number AS attrs_number,
    attributes_bool AS attrs_bool,
    now64(3) AS loaded_at
FROM signoz_traces.distributed_signoz_index_v3
WHERE serviceName IN ('codex-app-server', 'codex_cli_rs', 'codex_exec', 'oh-my-pi', 'claude-code')
    AND NOT (
        serviceName LIKE 'codex%'
        AND name IN (
            'receiving', 'handle_responses', 'append_items', 'persist_rollout_items',
            'realtime_conversation.running_state', 'fs.get_metadata', 'fs.read_file', 'auth',
            'account/rateLimits/read'
        )
        AND NOT (name = 'handle_responses' AND mapContains(attributes_number, 'gen_ai.usage.input_tokens'))
    )
    AND ({window})
