-- Normalized fact views over the curated tables. Producer boundaries follow the
-- validated contracts in agent-observability's model-usage and reasoning-effort
-- signal references. NULL means the producer has no signal, never zero.

-- One row per accepted producer-native model usage record.
CREATE OR REPLACE VIEW introspection.usage_events AS
SELECT
    ts,
    harness,
    attrs_string['conversation.id'] AS session_id,
    'OpenAI' AS provider,
    attrs_string['model'] AS model,
    '' AS response_model,
    if(attrs_string['model_reasoning_effort'] = '', 'unset', attrs_string['model_reasoning_effort']) AS effort,
    if(mapContains(attrs_number, 'input_token_count'), attrs_number['input_token_count'], toFloat64OrZero(attrs_string['input_token_count'])) AS input_tokens,
    if(mapContains(attrs_number, 'cached_token_count'), attrs_number['cached_token_count'], toFloat64OrZero(attrs_string['cached_token_count'])) AS cached_input,
    toNullable(if(mapContains(attrs_number, 'cache_write_token_count'), attrs_number['cache_write_token_count'], toFloat64OrZero(attrs_string['cache_write_token_count']))) AS cache_creation_input,
    if(mapContains(attrs_number, 'output_token_count'), attrs_number['output_token_count'], toFloat64OrZero(attrs_string['output_token_count'])) AS output_tokens,
    toNullable(if(mapContains(attrs_number, 'reasoning_token_count'), attrs_number['reasoning_token_count'], toFloat64OrZero(attrs_string['reasoning_token_count']))) AS reasoning_tokens,
    log_id AS source_id
FROM introspection.logs FINAL
WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
    AND event_name = 'codex.sse_event'
    AND attrs_string['conversation.id'] != ''
    AND attrs_string['model'] != ''
    AND (mapContains(attrs_number, 'input_token_count') OR mapContains(attrs_string, 'input_token_count'))
UNION ALL
SELECT
    ts,
    harness,
    attrs_string['session.id'],
    'Anthropic',
    attrs_string['model'],
    '',
    if(attrs_string['effort'] = '', 'unset', attrs_string['effort']),
    attrs_number['input_tokens'] + attrs_number['cache_creation_tokens'] + attrs_number['cache_read_tokens'],
    attrs_number['cache_read_tokens'],
    toNullable(attrs_number['cache_creation_tokens']),
    attrs_number['output_tokens'],
    CAST(NULL, 'Nullable(Float64)'),
    log_id
FROM introspection.logs FINAL
WHERE harness = 'claude-code'
    AND event_name = 'api_request'
    AND attrs_string['session.id'] != ''
    AND attrs_string['model'] != ''
UNION ALL
SELECT
    ts,
    harness,
    attrs_string['gen_ai.conversation.id'],
    multiIf(
        lower(attrs_string['gen_ai.provider.name']) LIKE '%anthropic%', 'Anthropic',
        lower(attrs_string['gen_ai.provider.name']) LIKE '%openai%', 'OpenAI',
        attrs_string['gen_ai.provider.name']
    ),
    attrs_string['gen_ai.request.model'],
    attrs_string['gen_ai.response.model'],
    if(attrs_string['pi.gen_ai.request.reasoning.effort'] = '', 'unset', attrs_string['pi.gen_ai.request.reasoning.effort']),
    attrs_number['gen_ai.usage.input_tokens'],
    if(mapContains(attrs_number, 'gen_ai.usage.cache_read.input_tokens'), attrs_number['gen_ai.usage.cache_read.input_tokens'], 0),
    toNullable(if(mapContains(attrs_number, 'gen_ai.usage.cache_creation.input_tokens'), attrs_number['gen_ai.usage.cache_creation.input_tokens'], 0)),
    attrs_number['gen_ai.usage.output_tokens'],
    toNullable(if(mapContains(attrs_number, 'gen_ai.usage.reasoning.output_tokens'), attrs_number['gen_ai.usage.reasoning.output_tokens'], 0)),
    span_id
FROM introspection.spans FINAL
WHERE harness = 'oh-my-pi'
    AND attrs_string['gen_ai.operation.name'] = 'chat'
    AND attrs_string['gen_ai.conversation.id'] != ''
    AND attrs_string['gen_ai.request.model'] != ''
    AND mapContains(attrs_number, 'gen_ai.usage.input_tokens')
    AND (attrs_number['gen_ai.usage.input_tokens'] > 0 OR attrs_number['gen_ai.usage.output_tokens'] > 0);

-- One row per user-initiated task: a Codex turn, an OMP root agent run, or a
-- Claude Code interaction. Outcome flags are friction proxies, not quality scores.
-- `friction_observed` lists the clean-completion components the producer could
-- observe for the row; a NULL component is one the producer does not emit.
-- Activity-hook events (introspection.hook_events) close what native telemetry
-- lacks: Claude Code interrupts, steers, reasoning tokens, subagents, and turn
-- failures, and omp steers. Rows from before a session had hooks keep NULL there.
-- Codex delegations are subagent spawns (`codex.agent_communication`) sent from the
-- turn's thread, and a Codex turn errored when its last response stream failed.
CREATE OR REPLACE VIEW introspection.task_outcomes AS
WITH
    codex_turn_windows AS (
        SELECT
            attrs_string['thread.id'] AS thread_id,
            attrs_string['turn.id'] AS turn_id,
            ts AS turn_start,
            ts + toIntervalNanosecond(duration_ns) AS turn_end
        FROM introspection.spans FINAL
        WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
            AND name = 'session_task.turn' AND attrs_string['turn.id'] != '' AND attrs_string['thread.id'] != ''
    ),
    codex_spawns AS (
        SELECT w.turn_id AS task_id, count() AS delegations
        FROM (
            SELECT attrs_string['sender_thread_id'] AS thread_id, ts
            FROM introspection.logs FINAL
            WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
                AND event_name = 'codex.agent_communication' AND attrs_string['kind'] = 'spawn'
                AND attrs_string['sender_thread_id'] != ''
        ) AS a
        ASOF INNER JOIN codex_turn_windows AS w ON w.thread_id = a.thread_id AND a.ts >= w.turn_start
        WHERE a.ts <= w.turn_end
        GROUP BY task_id
    ),
    codex_last_response AS (
        SELECT w.turn_id AS task_id, argMax(r.failed, r.ts) AS errored
        FROM (
            SELECT attrs_string['conversation.id'] AS thread_id, ts, toUInt8(attrs_string['error.message'] != '') AS failed
            FROM introspection.logs FINAL
            WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
                AND event_name = 'codex.sse_event' AND attrs_string['event.kind'] = 'response.completed'
        ) AS r
        ASOF INNER JOIN codex_turn_windows AS w ON w.thread_id = r.thread_id AND r.ts >= w.turn_start
        WHERE r.ts <= w.turn_end
        GROUP BY task_id
    ),
    codex_steps AS (
        SELECT attrs_string['turn_id'] AS task_id, count() AS model_steps
        FROM introspection.spans FINAL
        WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
            AND name = 'run_sampling_request' AND attrs_string['turn_id'] != ''
        GROUP BY task_id
    ),
    codex_interrupts AS (
        SELECT DISTINCT attrs_string['turn.id'] AS task_id
        FROM introspection.spans FINAL
        WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
            AND name = 'turn/interrupt' AND attrs_string['turn.id'] != ''
    ),
    codex_steers AS (
        SELECT DISTINCT attrs_string['turn.id'] AS task_id
        FROM introspection.spans FINAL
        WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
            AND name = 'turn/steer' AND attrs_string['turn.id'] != ''
    ),
    codex_tasks AS (
        SELECT
            t.ts AS start_ts,
            t.ts + toIntervalNanosecond(t.duration_ns) AS end_ts,
            t.harness AS harness,
            t.attrs_string['thread.id'] AS session_id,
            t.attrs_string['turn.id'] AS task_id,
            'OpenAI' AS provider,
            t.attrs_string['model'] AS model,
            if(t.attrs_string['codex.turn.reasoning_effort'] = '', 'unset', t.attrs_string['codex.turn.reasoning_effort']) AS effort,
            t.duration_ns / 1e9 AS duration_seconds,
            if(mapContains(t.attrs_number, 'codex.turn.token_usage.output_tokens'), toNullable(t.attrs_number['codex.turn.token_usage.output_tokens']), NULL) AS output_tokens,
            if(mapContains(t.attrs_number, 'codex.turn.token_usage.reasoning_output_tokens'), toNullable(t.attrs_number['codex.turn.token_usage.reasoning_output_tokens']), NULL) AS reasoning_tokens,
            toUInt64(s.model_steps) AS model_steps,
            toUInt64(sp.delegations) AS delegations,
            toNullable(toUInt8(t.attrs_string['turn.id'] IN (SELECT task_id FROM codex_interrupts))) AS interrupted,
            toNullable(toUInt8(lr.errored)) AS errored,
            toNullable(toUInt8(t.attrs_string['turn.id'] IN (SELECT task_id FROM codex_steers))) AS steered
        FROM introspection.spans AS t FINAL
        LEFT JOIN codex_steps AS s ON s.task_id = t.attrs_string['turn.id']
        LEFT JOIN codex_spawns AS sp ON sp.task_id = t.attrs_string['turn.id']
        LEFT JOIN codex_last_response AS lr ON lr.task_id = t.attrs_string['turn.id']
        WHERE t.harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
            AND t.name = 'session_task.turn'
            AND t.attrs_string['turn.id'] != ''
            AND t.attrs_string['thread.id'] != ''
            AND t.attrs_string['model'] NOT IN ('', 'codex-auto-review')
    ),
    omp_chats AS (
        SELECT
            trace_id,
            parent_span_id,
            attrs_string['pi.gen_ai.request.reasoning.effort'] AS effort_raw,
            attrs_number['gen_ai.usage.output_tokens'] AS output_tokens,
            if(mapContains(attrs_number, 'gen_ai.usage.reasoning.output_tokens'), attrs_number['gen_ai.usage.reasoning.output_tokens'], 0) AS reasoning_tokens
        FROM introspection.spans FINAL
        WHERE harness = 'oh-my-pi' AND attrs_string['gen_ai.operation.name'] = 'chat'
    ),
    omp_trace_usage AS (
        SELECT trace_id, sum(output_tokens) AS output_tokens, sum(reasoning_tokens) AS reasoning_tokens, count() AS model_steps
        FROM omp_chats GROUP BY trace_id
    ),
    omp_run_effort AS (
        SELECT trace_id, parent_span_id, groupUniqArrayIf(effort_raw, effort_raw != '') AS efforts
        FROM omp_chats GROUP BY trace_id, parent_span_id
    ),
    omp_delegations AS (
        SELECT trace_id, count() AS delegations
        FROM introspection.spans FINAL
        WHERE harness = 'oh-my-pi' AND name LIKE 'invoke_agent %' AND name NOT LIKE 'invoke_agent Advisor:%'
        GROUP BY trace_id
    ),
    omp_hook_sessions AS (
        SELECT DISTINCT session_id FROM introspection.hook_events FINAL WHERE producer = 'omp'
    ),
    omp_steer_events AS (
        SELECT session_id, occurred_at FROM introspection.hook_events FINAL
        WHERE producer = 'omp' AND event_type = 'steer'
    ),
    omp_steers AS (
        SELECT r.span_id AS task_id, count() AS steers
        FROM introspection.spans AS r FINAL
        INNER JOIN omp_steer_events AS e ON e.session_id = r.attrs_string['gen_ai.conversation.id']
        WHERE r.harness = 'oh-my-pi' AND r.name = 'invoke_agent' AND r.parent_span_id = ''
            AND e.occurred_at >= r.ts AND e.occurred_at <= r.ts + toIntervalNanosecond(r.duration_ns)
        GROUP BY task_id
    ),
    omp_tasks AS (
        SELECT
            r.ts AS start_ts,
            r.ts + toIntervalNanosecond(r.duration_ns) AS end_ts,
            r.harness AS harness,
            r.attrs_string['gen_ai.conversation.id'] AS session_id,
            r.span_id AS task_id,
            multiIf(
                lower(r.attrs_string['gen_ai.provider.name']) LIKE '%anthropic%', 'Anthropic',
                lower(r.attrs_string['gen_ai.provider.name']) LIKE '%openai%', 'OpenAI',
                r.attrs_string['gen_ai.provider.name']
            ) AS provider,
            r.attrs_string['gen_ai.request.model'] AS model,
            multiIf(length(e.efforts) = 0, 'unset', length(e.efforts) > 1, 'mixed', e.efforts[1]) AS effort,
            r.duration_ns / 1e9 AS duration_seconds,
            toNullable(u.output_tokens) AS output_tokens,
            toNullable(u.reasoning_tokens) AS reasoning_tokens,
            toUInt64(u.model_steps) AS model_steps,
            toUInt64(d.delegations) AS delegations,
            toNullable(toUInt8(r.attrs_number['pi.gen_ai.agent.chats.stop_reason.aborted.count'] > 0)) AS interrupted,
            toNullable(toUInt8(r.attrs_number['pi.gen_ai.agent.chats.stop_reason.error.count'] > 0)) AS errored,
            if(
                r.attrs_string['gen_ai.conversation.id'] IN (SELECT session_id FROM omp_hook_sessions),
                toNullable(toUInt8(st.steers > 0)),
                NULL
            ) AS steered
        FROM introspection.spans AS r FINAL
        LEFT JOIN omp_trace_usage AS u ON u.trace_id = r.trace_id
        LEFT JOIN omp_run_effort AS e ON e.trace_id = r.trace_id AND e.parent_span_id = r.span_id
        LEFT JOIN omp_delegations AS d ON d.trace_id = r.trace_id
        LEFT JOIN omp_steers AS st ON st.task_id = r.span_id
        WHERE r.harness = 'oh-my-pi'
            AND r.name = 'invoke_agent'
            AND r.parent_span_id = ''
            AND r.attrs_string['gen_ai.conversation.id'] != ''
            AND r.attrs_string['gen_ai.request.model'] != ''
    ),
    claude_requests AS (
        SELECT
            trace_id,
            topK(1)(attrs_string['model'])[1] AS model,
            groupUniqArrayIf(attrs_string['effort'], attrs_string['effort'] != '') AS efforts,
            sum(attrs_number['output_tokens']) AS output_tokens,
            count() AS model_steps,
            max(toUInt8(has_error OR attrs_number['status_code'] >= 400)) AS errored
        FROM introspection.spans FINAL
        WHERE harness = 'claude-code' AND name = 'claude_code.llm_request'
        GROUP BY trace_id
    ),
    claude_prompts AS (
        SELECT trace_id, any(attrs_string['prompt.id']) AS prompt_id
        FROM introspection.logs FINAL
        WHERE harness = 'claude-code' AND event_name = 'api_request' AND attrs_string['prompt.id'] != ''
        GROUP BY trace_id
    ),
    claude_hooks AS (
        SELECT
            attrs_string['prompt_id'] AS prompt_id,
            max(event_type = 'prompt_submitted') AS submitted,
            max(event_type IN ('turn_stop', 'turn_failure')) AS stopped,
            max(event_type = 'turn_failure') AS failed,
            countIf(event_type = 'turn_stop') AS stops,
            sumIf(attrs_number['reasoning_tokens'], event_type = 'turn_stop') AS reasoning_tokens,
            countIf(event_type = 'subagent_start') AS subagents
        FROM introspection.hook_events FINAL
        WHERE producer = 'claude-code' AND attrs_string['prompt_id'] != ''
        GROUP BY prompt_id
    ),
    claude_steers AS (
        SELECT i.span_id AS task_id, count() AS steers
        FROM introspection.spans AS i FINAL
        INNER JOIN (
            SELECT session_id, occurred_at FROM introspection.hook_events FINAL
            WHERE producer = 'claude-code' AND event_type = 'prompt_submitted'
        ) AS p ON p.session_id = i.attrs_string['session.id']
        WHERE i.harness = 'claude-code' AND i.name = 'claude_code.interaction'
            AND p.occurred_at > i.ts + INTERVAL 1 SECOND
            AND p.occurred_at < i.ts + toIntervalNanosecond(i.duration_ns)
        GROUP BY task_id
    ),
    claude_tasks AS (
        SELECT
            i.ts AS start_ts,
            i.ts + toIntervalNanosecond(i.duration_ns) AS end_ts,
            i.harness AS harness,
            i.attrs_string['session.id'] AS session_id,
            i.span_id AS task_id,
            'Anthropic' AS provider,
            c.model AS model,
            multiIf(length(c.efforts) = 0, 'unset', length(c.efforts) > 1, 'mixed', c.efforts[1]) AS effort,
            i.duration_ns / 1e9 AS duration_seconds,
            toNullable(c.output_tokens) AS output_tokens,
            if(h.stops > 0, toNullable(h.reasoning_tokens), NULL) AS reasoning_tokens,
            toUInt64(c.model_steps) AS model_steps,
            toUInt64(h.subagents) AS delegations,
            -- Claude Code runs no Stop hook when the user interrupts a turn.
            if(h.submitted = 1, toNullable(toUInt8(h.stopped = 0)), NULL) AS interrupted,
            toNullable(toUInt8(c.errored = 1 OR h.failed = 1)) AS errored,
            if(h.submitted = 1, toNullable(toUInt8(cs.steers > 0)), NULL) AS steered
        FROM introspection.spans AS i FINAL
        INNER JOIN claude_requests AS c ON c.trace_id = i.trace_id
        LEFT JOIN claude_prompts AS cp ON cp.trace_id = i.trace_id
        LEFT JOIN claude_hooks AS h ON h.prompt_id = cp.prompt_id
        LEFT JOIN claude_steers AS cs ON cs.task_id = i.span_id
        WHERE i.harness = 'claude-code'
            AND i.name = 'claude_code.interaction'
            AND i.attrs_string['session.id'] != ''
    ),
    tasks AS (
        SELECT * FROM codex_tasks
        UNION ALL SELECT * FROM omp_tasks
        UNION ALL SELECT * FROM claude_tasks
    )
SELECT
    *,
    if(
        harness = 'codex_exec',
        NULL,
        toUInt8(next_start_ts IS NOT NULL AND next_start_ts <= end_ts + INTERVAL 10 MINUTE)
    ) AS quick_follow_up,
    if(
        harness = 'codex_exec',
        NULL,
        toUInt8(coalesce(interrupted, 0) = 0 AND coalesce(errored, 0) = 0 AND quick_follow_up = 0)
    ) AS clean_completion,
    arrayFilter(
        c -> c != '',
        [
            if(interrupted IS NULL, '', 'interrupt'),
            if(errored IS NULL, '', 'error'),
            if(quick_follow_up IS NULL, '', 'follow_up')
        ]
    ) AS friction_observed
FROM (
    SELECT
        *,
        leadInFrame(toNullable(start_ts), 1, NULL) OVER (
            PARTITION BY harness, session_id ORDER BY start_ts
            ROWS BETWEEN CURRENT ROW AND 1 FOLLOWING
        ) AS next_start_ts
    FROM tasks
);

-- One row per terminal model call. Boundaries differ by producer: a Codex response
-- stream (`codex.sse_event` `response.completed`, which carries `error.message` when
-- the stream fails), an OMP chat span, and a Claude Code LLM request span. NULL
-- means the producer has no such field. Outcome is `unknown` without explicit evidence,
-- and `cancelled` when the user aborted the call (OMP `error.type = 'aborted'`).
CREATE OR REPLACE VIEW introspection.model_calls AS
SELECT
    ts,
    harness,
    attrs_string['conversation.id'] AS session_id,
    'OpenAI' AS provider,
    attrs_string['model'] AS model,
    '' AS response_model,
    if(attrs_string['error.message'] = '', 'succeeded', 'failed') AS outcome,
    multiIf(
        attrs_string['error.message'] = '', '',
        startsWith(attrs_string['error.message'], 'stream disconnected before completion'), 'stream_disconnect',
        'error'
    ) AS error_class,
    CAST(NULL, 'Nullable(Float64)') AS duration_seconds,
    if(mapContains(attrs_number, 'ttft_ms'), toNullable(attrs_number['ttft_ms'] / 1000), NULL) AS ttft_seconds,
    if(mapContains(attrs_number, 'output_token_count'), toNullable(attrs_number['output_token_count']), toFloat64OrNull(attrs_string['output_token_count'])) AS output_tokens,
    CAST(NULL, 'Nullable(UInt16)') AS attempt,
    toNullable(toUInt8(startsWith(attrs_string['error.message'], 'stream disconnected before completion'))) AS stream_disconnect,
    log_id AS source_id
FROM introspection.logs FINAL
WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
    AND event_name = 'codex.sse_event'
    AND attrs_string['event.kind'] = 'response.completed'
UNION ALL
SELECT
    ts,
    harness,
    attrs_string['gen_ai.conversation.id'],
    multiIf(
        lower(attrs_string['gen_ai.provider.name']) LIKE '%anthropic%', 'Anthropic',
        lower(attrs_string['gen_ai.provider.name']) LIKE '%openai%', 'OpenAI',
        attrs_string['gen_ai.provider.name']
    ),
    attrs_string['gen_ai.request.model'],
    attrs_string['gen_ai.response.model'],
    multiIf(
        has_error AND attrs_string['error.type'] = 'aborted', 'cancelled',
        has_error, 'failed',
        attrs_string['gen_ai.response.finish_reasons'] IN ('', '[]'), 'unknown',
        'succeeded'
    ),
    if(has_error, if(attrs_string['error.type'] = '', 'error', attrs_string['error.type']), ''),
    toNullable(duration_ns / 1e9),
    if(mapContains(attrs_number, 'gen_ai.response.time_to_first_chunk'), toNullable(attrs_number['gen_ai.response.time_to_first_chunk']), NULL),
    toNullable(attrs_number['gen_ai.usage.output_tokens']),
    CAST(NULL, 'Nullable(UInt16)'),
    CAST(NULL, 'Nullable(UInt8)'),
    span_id
FROM introspection.spans FINAL
WHERE harness = 'oh-my-pi' AND attrs_string['gen_ai.operation.name'] = 'chat'
UNION ALL
SELECT
    ts,
    harness,
    attrs_string['session.id'],
    'Anthropic',
    attrs_string['model'],
    '',
    multiIf(
        has_error OR attrs_number['status_code'] >= 400 OR (mapContains(attrs_bool, 'success') AND NOT attrs_bool['success']), 'failed',
        NOT mapContains(attrs_bool, 'success'), 'unknown',
        'succeeded'
    ),
    multiIf(
        attrs_number['status_code'] >= 400, concat('http_', toString(toUInt16(attrs_number['status_code']))),
        has_error OR (mapContains(attrs_bool, 'success') AND NOT attrs_bool['success']), 'error',
        ''
    ),
    toNullable(duration_ns / 1e9),
    if(mapContains(attrs_number, 'ttft_ms'), toNullable(attrs_number['ttft_ms'] / 1000), NULL),
    toNullable(attrs_number['output_tokens']),
    toNullable(toUInt16(attrs_number['attempt'])),
    CAST(NULL, 'Nullable(UInt8)'),
    span_id
FROM introspection.spans FINAL
WHERE harness = 'claude-code' AND name = 'claude_code.llm_request';

-- One row per activity-hook `tool_call` (omp and Claude Code), keyed by tool call ID.
CREATE OR REPLACE VIEW introspection.hook_tool_calls AS
SELECT
    attrs_string['tool_use_id'] AS hc_tool_use_id,
    any(attrs_string['command_head']) AS hc_command_head,
    any(attrs_string['command_sub']) AS hc_command_sub,
    toUInt8(max(attrs_number['gate_bypass'])) AS hc_gate_bypass,
    any(attrs_string['targets']) AS hc_targets,
    any(attrs_string['arguments_hash']) AS hc_arguments_hash,
    any(attrs_string['workdir']) AS hc_workdir
FROM introspection.hook_events FINAL
WHERE producer IN ('omp', 'claude-code') AND event_type = 'tool_call' AND attrs_string['tool_use_id'] != ''
GROUP BY hc_tool_use_id;

-- One row per tool invocation with an explicit outcome where the producer has one.
-- Codex reports success=true for commands that exit non-zero; the exit code wins.
-- `task_id` joins task_outcomes: a Codex call belongs to the user turn of its
-- session whose span contains the call time; an OMP call to the root run of its
-- trace; a Claude Code call to the interaction of its trace. '' when unattributed.
-- omp and Claude Code tool spans carry no arguments, so their command shape, targets,
-- gate-bypass flag, and argument hash come from the activity hooks' `tool_call`
-- events (joined by tool call ID), and a Claude Code failure's signature from its
-- `tool_failure` event; calls from before hooks were installed keep the native fields.
CREATE OR REPLACE VIEW introspection.tool_calls AS
-- `tool_family` groups each harness's tool names by what the tool does, so one
-- behaviour is one finding whatever harness produced it. `failure_class` is the
-- failure signature with quoted values and paths generalized, so the same failure on
-- different files is one class.
SELECT
    *,
    multiIf(
        lower(tool) IN ('bash', 'exec_command', 'exec', 'shell', 'local_shell', 'write_stdin', 'run', 'js'), 'shell',
        lower(tool) IN ('read', 'view_image', 'lsp', 'lsp_evidence_bind'), 'read',
        lower(tool) IN ('edit', 'write', 'multiedit', 'apply_patch', 'notebookedit'), 'edit',
        lower(tool) IN ('grep', 'glob', 'find', 'ls'), 'search',
        lower(tool) IN ('eval'), 'eval',
        lower(tool) IN ('web_search', 'webfetch', 'websearch', 'fetch'), 'web',
        lower(tool) IN ('task', 'agent', 'spawn_agent', 'send_message', 'send_message_to_thread', 'followup_task', 'hub'), 'delegate',
        startsWith(tool, 'mcp__') OR mcp_server != '', 'mcp',
        lower(tool)
    ) AS tool_family,
    if(
        failure_signature = '',
        '',
        trimBoth(replaceRegexpAll(
            replaceRegexpAll(failure_signature, '\'[^\']*\'|"[^"]*"|`[^`]*`', '\'…\''),
            '[^\\s:\'"(),]*/[^\\s:\'"(),]+', '<path>'
        ))
    ) AS failure_class
FROM (
    SELECT
        ts,
        harness,
        attrs_string['conversation.id'] AS session_id,
        attrs_string['call_id'] AS call_id,
        attrs_string['tool_name'] AS tool,
        multiIf(
            attrs_string['success'] = 'false', 'failed',
            mapContains(attrs_number, 'x.exit_code') AND attrs_number['x.exit_code'] != 0, 'failed',
            attrs_string['success'] = 'true', 'succeeded',
            'unknown'
        ) AS outcome,
        if(mapContains(attrs_number, 'x.exit_code'), toNullable(attrs_number['x.exit_code']), NULL) AS exit_code,
        if(mapContains(attrs_number, 'duration_ms'), toNullable(attrs_number['duration_ms']), toFloat64OrNull(attrs_string['duration_ms'])) AS duration_ms,
        attrs_string['x.command_head'] AS command_head,
        attrs_string['x.command_sub'] AS command_sub,
        toUInt8(attrs_string['x.gate_bypass'] = '1') AS gate_bypass,
        JSONExtract(if(attrs_string['x.targets'] = '', '[]', attrs_string['x.targets']), 'Array(String)') AS targets,
        attrs_string['x.failure_signature'] AS failure_signature,
        attrs_string['x.failure_signature_hash'] AS failure_signature_hash,
        attrs_string['x.arguments_hash'] AS arguments_hash,
        attrs_string['mcp_server'] AS mcp_server,
        if(l.ts <= t.turn_end, t.turn_id, '') AS task_id,
        attrs_string['x.workdir'] AS workdir
    FROM introspection.logs AS l FINAL
    ASOF LEFT JOIN (
        SELECT
            harness AS turn_harness,
            attrs_string['thread.id'] AS turn_session,
            ts AS turn_start,
            ts + toIntervalNanosecond(duration_ns) AS turn_end,
            attrs_string['turn.id'] AS turn_id
        FROM introspection.spans FINAL
        WHERE harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
            AND name = 'session_task.turn'
            AND attrs_string['turn.id'] != ''
            AND attrs_string['model'] NOT IN ('', 'codex-auto-review')
    ) AS t ON t.turn_harness = l.harness AND t.turn_session = l.attrs_string['conversation.id'] AND l.ts >= t.turn_start
    WHERE l.harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs') AND l.event_name = 'codex.tool_result'
    UNION ALL
    SELECT
        ts,
        harness,
        attrs_string['gen_ai.conversation.id'],
        attrs_string['gen_ai.tool.call.id'],
        attrs_string['gen_ai.tool.name'],
        multiIf(
            attrs_string['pi.gen_ai.tool.status'] = 'ok', 'succeeded',
            attrs_string['pi.gen_ai.tool.status'] = 'error', 'failed',
            attrs_string['pi.gen_ai.tool.status'] = 'aborted', 'aborted',
            has_error, 'failed',
            'unknown'
        ),
        CAST(NULL, 'Nullable(Float64)'),
        toNullable(duration_ns / 1e6),
        hc.hc_command_head,
        hc.hc_command_sub,
        hc.hc_gate_bypass,
        JSONExtract(if(hc.hc_targets = '', '[]', hc.hc_targets), 'Array(String)'),
        if(
            has_error OR attrs_string['pi.gen_ai.tool.status'] = 'error',
            substringUTF8(replaceRegexpAll(replaceRegexpAll(status_message, '/Users/[^/ ]+', '~'), '[0-9a-f]{8,}|\\d+', 'N'), 1, 80),
            ''
        ),
        if(
            has_error OR attrs_string['pi.gen_ai.tool.status'] = 'error',
            attrs_string['x.status_hash'],
            ''
        ),
        hc.hc_arguments_hash,
        '',
        r.root_span,
        hc.hc_workdir
    FROM introspection.spans FINAL
    LEFT JOIN introspection.hook_tool_calls AS hc ON hc.hc_tool_use_id = attrs_string['gen_ai.tool.call.id']
    LEFT JOIN (
        SELECT trace_id AS root_trace, any(span_id) AS root_span
        FROM introspection.spans FINAL
        WHERE harness = 'oh-my-pi' AND name = 'invoke_agent' AND parent_span_id = ''
        GROUP BY trace_id
    ) AS r ON r.root_trace = trace_id
    WHERE harness = 'oh-my-pi' AND name LIKE 'execute_tool %'
    UNION ALL
    SELECT
        l.ts,
        l.harness,
        l.attrs_string['session.id'],
        l.attrs_string['tool_use_id'],
        l.attrs_string['tool_name'],
        multiIf(l.attrs_string['success'] = 'false', 'failed', l.attrs_string['success'] = 'true', 'succeeded', 'unknown'),
        CAST(NULL, 'Nullable(Float64)'),
        if(mapContains(l.attrs_number, 'duration_ms'), toNullable(l.attrs_number['duration_ms']), toFloat64OrNull(l.attrs_string['duration_ms'])),
        if(hc.hc_tool_use_id != '', hc.hc_command_head, s.bash_argv0),
        if(hc.hc_tool_use_id != '', hc.hc_command_sub, s.bash_command_class),
        hc.hc_gate_bypass,
        JSONExtract(if(hc.hc_targets = '', '[]', hc.hc_targets), 'Array(String)'),
        if(l.attrs_string['success'] = 'false', coalesce(nullIf(hf.hf_signature, ''), l.attrs_string['error_type']), ''),
        if(l.attrs_string['success'] = 'false' AND hf.hf_signature != '', hf.hf_signature_hash, ''),
        hc.hc_arguments_hash,
        '',
        i.interaction_span,
        hc.hc_workdir
    FROM introspection.logs AS l FINAL
    LEFT JOIN introspection.hook_tool_calls AS hc ON hc.hc_tool_use_id = l.attrs_string['tool_use_id']
    LEFT JOIN (
        SELECT
            attrs_string['tool_use_id'] AS hf_tool_use_id,
            any(attrs_string['failure_signature']) AS hf_signature,
            any(attrs_string['failure_signature_hash']) AS hf_signature_hash
        FROM introspection.hook_events FINAL
        WHERE producer = 'claude-code' AND event_type = 'tool_failure'
        GROUP BY hf_tool_use_id
    ) AS hf ON hf.hf_tool_use_id = l.attrs_string['tool_use_id']
    LEFT JOIN (
        SELECT
            attrs_string['tool_use_id'] AS tool_use_id,
            any(attrs_string['bash_argv0']) AS bash_argv0,
            any(attrs_string['bash_command_class']) AS bash_command_class
        FROM introspection.spans FINAL
        WHERE harness = 'claude-code' AND name = 'claude_code.tool'
        GROUP BY tool_use_id
    ) AS s ON s.tool_use_id = l.attrs_string['tool_use_id']
    LEFT JOIN (
        SELECT trace_id AS interaction_trace, any(span_id) AS interaction_span
        FROM introspection.spans FINAL
        WHERE harness = 'claude-code' AND name = 'claude_code.interaction'
        GROUP BY trace_id
    ) AS i ON i.interaction_trace = l.trace_id
    WHERE l.harness = 'claude-code' AND l.event_name = 'tool_result'
);

-- Explicit user and policy friction signals. Codex interrupt/steer RPC spans
-- carry only the turn ID; the session comes from the matching turn span. omp tool
-- approvals come from the activity hooks' `approval` events.
CREATE OR REPLACE VIEW introspection.user_signals AS
WITH codex_turn_sessions AS (
    SELECT attrs_string['turn.id'] AS turn_id, any(attrs_string['thread.id']) AS session_id
    FROM introspection.spans FINAL
    WHERE name = 'session_task.turn' AND attrs_string['turn.id'] != ''
    GROUP BY turn_id
)
SELECT
    r.ts AS ts,
    r.harness AS harness,
    coalesce(t.session_id, '') AS session_id,
    r.attrs_string['turn.id'] AS turn_id,
    if(r.name = 'turn/interrupt', 'interrupt', 'steer') AS signal,
    '' AS detail,
    'user' AS source,
    '' AS tool
FROM introspection.spans AS r FINAL
LEFT JOIN codex_turn_sessions AS t ON t.turn_id = r.attrs_string['turn.id']
WHERE r.harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs') AND r.name IN ('turn/interrupt', 'turn/steer')
UNION ALL
SELECT
    ts,
    harness,
    attrs_string[if(harness = 'claude-code', 'session.id', 'conversation.id')],
    '',
    multiIf(
        event_name IN ('codex.tool_decision', 'tool_decision'), 'tool_decision',
        event_name = 'codex.sandbox_outcome', 'sandbox_outcome',
        'prompt'
    ),
    multiIf(
        event_name = 'codex.sandbox_outcome', attrs_string['outcome'],
        event_name IN ('codex.tool_decision', 'tool_decision'), attrs_string['decision'],
        toString(coalesce(nullIf(attrs_number['prompt_length'], 0), toFloat64OrZero(attrs_string['prompt_length'])))
    ),
    attrs_string['source'],
    attrs_string['tool_name']
FROM introspection.logs FINAL
WHERE (harness IN ('codex-app-server', 'codex_exec', 'codex_cli_rs')
        AND event_name IN ('codex.tool_decision', 'codex.sandbox_outcome', 'codex.user_prompt'))
    OR (harness = 'claude-code' AND event_name IN ('tool_decision', 'user_prompt'))
UNION ALL
SELECT
    ts,
    harness,
    attrs_string['gen_ai.conversation.id'],
    '',
    'interrupt',
    name,
    'user',
    attrs_string['gen_ai.tool.name']
FROM introspection.spans FINAL
WHERE harness = 'oh-my-pi'
    AND (attrs_string['pi.gen_ai.tool.status'] = 'aborted' OR status_message = 'Interrupted by user')
UNION ALL
SELECT
    occurred_at,
    'oh-my-pi',
    session_id,
    '',
    'tool_decision',
    if(attrs_number['approved'] = 1, 'approved', 'rejected'),
    'user',
    attrs_string['tool_name']
FROM introspection.hook_events FINAL
WHERE producer = 'omp' AND event_type = 'approval';

-- The Git project of each attributed session, joined to the facts by session ID
-- (session IDs are harness-native UUIDs, so the harness is not needed).
CREATE OR REPLACE VIEW introspection.session_project AS
SELECT
    session_id,
    project_name AS project,
    project_id,
    store,
    project_root
FROM introspection.session_projects FINAL
WHERE status = 'attributed';

-- One row per task with the Jev labels of the prompt that started it and of the
-- next prompt in the session (`introspection.prompt_labels`, labelled by `facts sync`
-- from the producers' prompt events). The task's own
-- label is the latest one submitted up to five seconds after the task started. A
-- next prompt within 10 minutes of the task's end that Jev reads as a correction
-- (probability ≥ 0.5) marks the task `corrected_next`. Tasks whose session had no
-- labelled prompts keep empty labels and NULL flags. It reads the task_outcomes
-- snapshot rather than recomputing the view: labels exist only from the hooks'
-- install date (2026-09-30), well inside the snapshot's 91 days.
CREATE OR REPLACE VIEW introspection.task_labels AS
WITH labels AS (
    SELECT
        session_id,
        ts AS label_ts,
        task_type,
        task_type_confidence,
        correction,
        correction_kind,
        sentiment
    FROM introspection.prompt_labels FINAL
    WHERE status = 'labelled' AND session_id != ''
),
labelled_sessions AS (SELECT DISTINCT session_id FROM labels),
tasks AS (
    SELECT harness, session_id, task_id, start_ts, end_ts, effort, clean_completion,
        start_ts + INTERVAL 5 SECOND AS label_before
    FROM introspection.task_outcomes_snapshot
    WHERE session_id IN (SELECT session_id FROM labelled_sessions)
),
own AS (
    SELECT t.harness AS harness, t.task_id AS task_id, o.task_type AS task_type,
        o.task_type_confidence AS task_type_confidence, o.sentiment AS sentiment
    FROM tasks AS t
    ASOF INNER JOIN labels AS o ON o.session_id = t.session_id AND t.label_before >= o.label_ts
),
next AS (
    SELECT t.harness AS harness, t.task_id AS task_id, n.label_ts AS next_ts, n.correction AS correction,
        n.correction_kind AS correction_kind, n.sentiment AS sentiment, t.end_ts AS end_ts
    FROM tasks AS t
    ASOF INNER JOIN labels AS n ON n.session_id = t.session_id AND n.label_ts > t.end_ts
)
SELECT
    t.harness AS harness,
    t.session_id AS session_id,
    t.task_id AS task_id,
    t.start_ts AS start_ts,
    t.effort AS effort,
    t.clean_completion AS clean_completion,
    coalesce(o.task_type, '') AS task_type,
    o.task_type_confidence AS task_type_confidence,
    coalesce(o.sentiment, '') AS sentiment,
    toUInt8(o.task_id != '') AS labelled,
    toNullable(toUInt8(n.task_id != '' AND n.next_ts <= t.end_ts + INTERVAL 10 MINUTE AND n.correction >= 0.5)) AS corrected_next,
    if(n.task_id != '' AND n.next_ts <= t.end_ts + INTERVAL 10 MINUTE AND n.correction >= 0.5, n.correction_kind, '') AS correction_kind_next,
    if(n.task_id != '' AND n.next_ts <= t.end_ts + INTERVAL 10 MINUTE, n.sentiment, '') AS sentiment_next
FROM tasks AS t
LEFT JOIN own AS o ON o.harness = t.harness AND o.task_id = t.task_id
LEFT JOIN next AS n ON n.harness = t.harness AND n.task_id = t.task_id;

-- Activity-hook events with the harness resolved, for registry route predicates and
-- the Pipeline coverage grid. Codex hooks are shared by app-server, CLI, and exec, so
-- a Codex event takes the harness of the session's Codex logs ('' until they arrive).
CREATE OR REPLACE VIEW introspection.hook_rows AS
SELECT
    multiIf(
        h.producer = 'omp', 'oh-my-pi',
        h.producer = 'claude-code', 'claude-code',
        coalesce(c.codex_harness, '')
    ) AS harness,
    h.occurred_at AS ts,
    h.producer AS producer,
    h.session_id AS session_id,
    h.event_type AS event_type,
    h.attrs_string AS attrs_string,
    h.attrs_number AS attrs_number
FROM introspection.hook_events AS h FINAL
LEFT JOIN (
    SELECT attrs_string['conversation.id'] AS session_id, any(harness) AS codex_harness
    FROM introspection.logs
    WHERE harness IN ('codex-app-server', 'codex_cli_rs', 'codex_exec') AND attrs_string['conversation.id'] != ''
    GROUP BY session_id
) AS c ON h.producer = 'codex' AND c.session_id = h.session_id;
