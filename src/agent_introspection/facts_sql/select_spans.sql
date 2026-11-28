WITH
    arrayFilter(l -> l != '', splitByChar('\n', distributed_signoz_index_v3.status_message)) AS status_lines,
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
    if(
        match(substringUTF8(status_line, 1, 80), '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)'),
        status_line,
        extract(status_line, '(?i)((error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found).*)')
    ) AS status_window
SELECT
    serviceName AS harness,
    timestamp AS ts,
    toString(traceID) AS trace_id,
    spanID AS span_id,
    parentSpanID AS parent_span_id,
    replaceRegexpAll(distributed_signoz_index_v3.name, '/(?:Users|home)/[^/ ]+|/root($|[/ ])', '~\\1') AS name,
    duration_nano AS duration_ns,
    has_error,
    status_code_string AS status_code,
    substringUTF8(
        replaceRegexpAll(
            replaceRegexpAll(
                replaceRegexpAll(
                    replaceRegexpAll(
                        replaceRegexpAll(
                            replaceRegexpAll(
                                replaceRegexpAll(
                                    replaceRegexpAll(
                                        replaceRegexpAll(
                                            replaceRegexpAll(
                                                status_window,
                                                '(?i)((?:token|password|passwd|secret|credential|auth|private[_-]?key|api[_-]?key|authorization)\\w*["'']?\\s*[=:]\\s*)(?:(?:Bearer|Basic)\\s+)?(?:"[^"]*"|''[^'']*''|[^\\s,;"'']+)',
                                                '\\1[REDACTED]'
                                            ),
                                            '(?i)(\\bbearer\\s+)[A-Za-z0-9._~+/=-]{8,}',
                                            '\\1[REDACTED]'
                                        ),
                                        '(?i)(\\b(?:api key|access token|private key|secret|password|credential)\\s+)(?:[A-Za-z0-9._~+/=-]{16,}|[A-Za-z._~+/=-]*[0-9][A-Za-z0-9._~+/=-]*)',
                                        '\\1[REDACTED]'
                                    ),
                                    '\\b(?:sk-|ghp_|gho_|ghs_|github_pat_|xox[a-z]-|AKIA)[A-Za-z0-9_-]{4,}',
                                    '[REDACTED]'
                                ),
                                '\\b([A-Za-z0-9_-]{20,})\\b',
                                '⟦\\1⟧'
                            ),
                            '⟦([A-Za-z_-]*)⟧',
                            '\\1'
                        ),
                        '⟦([0-9_-]*)⟧',
                        '\\1'
                    ),
                    '⟦[^⟧]*⟧',
                    '[REDACTED]'
                ),
                '/(?:Users|home)/[^/ ]+|/root($|[/ ])',
                '~\\1'
            ),
            '[0-9a-f]{8,}|\\d+',
            'N'
        ) AS status_normalised,
        1,
        80
    ) AS status_message,
    mapUpdate(
        mapFilter(
            (k, v) -> k NOT IN (
                'gen_ai.tool.description', 'user_prompt', 'prompt', 'user.email',
                'user.account_id', 'user.account_uuid', 'user.id', 'organization.id'
            ) AND NOT endsWith(k, '.tool.call.intent'),
            mapApply((k, v) -> (replaceRegexpOne(k, '^omp\\.gen_ai\\.', 'pi.gen_ai.'), v), attributes_string)
        ),
        if(
            status_normalised = '',
            CAST(map(), 'Map(LowCardinality(String), String)'),
            CAST(map('x.status_hash', lower(substring(hex(SHA256(status_normalised)), 1, 16))), 'Map(LowCardinality(String), String)')
        )
    ) AS attrs_string,
    mapApply((k, v) -> (replaceRegexpOne(k, '^omp\\.gen_ai\\.', 'pi.gen_ai.'), v), attributes_number) AS attrs_number,
    mapApply((k, v) -> (replaceRegexpOne(k, '^omp\\.gen_ai\\.', 'pi.gen_ai.'), v), attributes_bool) AS attrs_bool,
    now64(3) AS loaded_at
FROM signoz_traces.distributed_signoz_index_v3
WHERE serviceName IN ({services})
    AND (
        serviceName NOT LIKE 'codex%'
        OR distributed_signoz_index_v3.name IN (
            'session_task.turn', 'session_task.run', 'run_sampling_request', 'try_run_sampling_request',
            'turn/start', 'turn/interrupt', 'turn/steer'
        )
        OR (distributed_signoz_index_v3.name = 'handle_responses' AND mapContains(attributes_number, 'gen_ai.usage.input_tokens'))
    )
    AND ({window})
