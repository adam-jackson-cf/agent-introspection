WITH
    attributes_string['event.name'] AS event,
    attributes_string['arguments'] AS args,
    attributes_string['output'] AS out,
    JSONExtractString(args, 'cmd') AS json_cmd,
    if(json_cmd != '', json_cmd, extract(args, '["'']?cmd["'']?\\s*:\\s*["''`]([^"''`]+)')) AS cmd,
    arrayFilter(
        t -> t != '' AND NOT match(t, '^[A-Za-z_][A-Za-z0-9_]*='),
        splitByRegexp('\\s+', cmd)
    ) AS cmd_tokens,
{command_shape}
    arrayFilter(
        t -> NOT startsWith(t, '-')
            AND match(t, '^[~.]{0,2}/?[A-Za-z0-9_.@+-]+(/[A-Za-z0-9_.@+-]+)*/?$')
            AND (
                position(t, '/') > 0
                OR match(t, '\\.(md|py|ts|tsx|js|mjs|json|jsonl|toml|ya?ml|sh|sql|txt|lock|css|html|rs|go|sqlite3?|cfg|ini|csv|xml|plist|log)$')
            )
            AND NOT match(t, '^[0-9.]+$'),
        arrayMap(t -> replaceRegexpAll(t, '^["'']+|["'';,)]+$', ''), arraySlice(cmd_tokens, 2))
    ) AS command_paths,
    arraySlice(
        arrayDistinct(
            arrayMap(
                p -> substring(replaceRegexpAll(trimBoth(p), '/(?:Users|home)/[^/\\s;:,]+|/root($|[/\\s;:,])', '~\\1'), 1, 300),
                arrayConcat(
                    extractAll(args, '\\*\\*\\* (?:Update|Add|Delete) File: ([^\\n\\\\]+)'),
                    if(JSONExtractString(args, 'path') != '', [JSONExtractString(args, 'path')], []),
                    command_paths
                )
            )
        ),
        1,
        20
    ) AS targets,
    coalesce(
        nullIf(extract(out, 'Process exited with code (-?\\d+)'), ''),
        nullIf(extract(out, '^Exit code: (-?\\d+)'), '')
    ) AS exit_code,
    arrayFilter(
        l -> l != '' AND NOT match(
            l,
            '^(Chunk ID|Wall time|Process exited|Exit code|Original token count|Output:|Script completed|Script running)'
        ),
        splitByChar('\n', out)
    ) AS output_lines,
    arrayLast(
        l -> match(l, '^[A-Za-z_][A-Za-z0-9_.]*(Error|Exception|Exit|Interrupt)(\\s*\\[[A-Z_]+\\])?(:|$)'),
        output_lines
    ) AS exception_line,
    arrayFirst(
        l -> match(l, '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)')
            AND NOT match(l, '^\\s*(Traceback \\(most recent call last\\):|triggerUncaughtException\\(|at |File "|node:internal|throw |raise |Script (failed|error:?)\\s*$|error: script "[^"]*" exited|={3,}|- Status: failed)'),
        output_lines
    ) AS specific_line,
    multiIf(
        exception_line != '', exception_line,
        specific_line != '', specific_line,
        arrayFirst(
            l -> match(l, '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)'),
            output_lines
        )
    ) AS error_line,
    if(
        match(substringUTF8(error_line, 1, 80), '(?i)(error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found)'),
        error_line,
        extract(error_line, '(?i)((error|fail|denied|not permitted|no such|traceback|exception|cannot|can''t|invalid|not found).*)')
    ) AS error_window,
    event = 'codex.tool_result' AS is_codex_tool_result,
    is_codex_tool_result AND (
        attributes_string['success'] = 'false' OR (exit_code IS NOT NULL AND exit_code != '0')
    ) AS failed
SELECT
    resource.`service.name`::String AS harness,
    fromUnixTimestamp64Nano(toInt64(timestamp), 'UTC') AS ts,
    id AS log_id,
    trace_id,
    span_id,
    event AS event_name,
    severity_text AS severity,
    CAST(
        mapUpdate(
            CAST(
                mapFilter(
                    (k, v) -> k NOT IN (
                        'prompt', 'arguments', 'output', 'content', 'user.email',
                        'user.account_id', 'user.account_uuid', 'user.id', 'organization.id'
                    ),
                    attributes_string
                ),
                'Map(String, String)'
            ),
            if(
                is_codex_tool_result,
                map(
                    'x.command_head', command_head,
                    'x.command_sub', command_sub,
                    'x.verification_kinds', toJSONString(verification_kinds),
                    'x.gate_bypass', toString(match(args, '(--no-verify|(^|[\\s"\\x27])HUSKY=0|(^|[\\s"\\x27])SKIP=|--no-gpg-sign)')),
                    'x.workdir', replaceRegexpOne(JSONExtractString(args, 'workdir'), '^(?:/(?:Users|home)/[^/]+|/root($|/))', '~\\1'),
                    'x.targets', toJSONString(targets),
                    'x.arguments_hash', toString(cityHash64(args)),
                    'x.failure_signature', if(
                        failed,
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
                                                                    error_window,
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
                            ) AS error_normalised,
                            1,
                            80
                        ),
                        ''
                    ),
                    'x.failure_signature_hash', if(
                        failed AND error_normalised != '',
                        lower(substring(hex(SHA256(error_normalised)), 1, 16)),
                        ''
                    )
                ),
                CAST(map(), 'Map(String, String)')
            )
        ),
        'Map(LowCardinality(String), String)'
    ) AS attrs_string,
    CAST(
        mapUpdate(
            CAST(attributes_number, 'Map(String, Float64)'),
            if(
                is_codex_tool_result,
                mapUpdate(
                    map('x.arguments_length', toFloat64(length(args)), 'x.output_length', toFloat64(length(out)), 'x.gate_run', toFloat64(gate_run), 'x.segments', toFloat64(segments)),
                    if(exit_code IS NULL, CAST(map(), 'Map(String, Float64)'), map('x.exit_code', toFloat64(toInt64(assumeNotNull(exit_code)))))
                ),
                CAST(map(), 'Map(String, Float64)')
            )
        ),
        'Map(LowCardinality(String), Float64)'
    ) AS attrs_number,
    attributes_bool AS attrs_bool,
    now64(3) AS loaded_at
FROM signoz_logs.distributed_logs_v2
WHERE resource.`service.name`::String IN ({services})
    AND ({window})
